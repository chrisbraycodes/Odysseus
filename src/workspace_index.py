"""Vector index for workspace codebase — deep repo-wide analysis."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from src.embedding_lanes import (
    LANE_CUSTOM,
    LANE_FASTEMBED,
    build_embedding_lanes,
    collection_name,
    dedupe_results,
    lane_count,
    migrate_legacy_collection,
    query_lanes,
)

logger = logging.getLogger(__name__)

COLLECTION_NAME = "odysseus_workspace"
VECTOR_WEIGHT = 0.7
KEYWORD_WEIGHT = 0.3

_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".next", ".cache", "site-packages", ".idea", ".tox",
})

_WORKSPACE_EXTENSIONS = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs",
    ".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml",
    ".html", ".css", ".scss", ".sass", ".vue", ".svelte",
    ".go", ".rs", ".java", ".kt", ".rb", ".php", ".sql",
    ".sh", ".bash", ".ps1", ".ini", ".cfg", ".conf",
})

_SPECIAL_FILENAMES = frozenset({
    "dockerfile", "makefile", "license", "readme",
})

_MAX_FILE_BYTES = 512_000
_MAX_FILES = 800
_CHUNK_SIZE = 1200
_CHUNK_OVERLAP = 200
_STALE_SECONDS = 6 * 3600  # re-index if older than 6h
_SEARCH_SIMILARITY_FLOOR = 0.28


def workspace_key(workspace_root: str) -> str:
    return hashlib.sha256(os.path.abspath(workspace_root).encode("utf-8")).hexdigest()[:16]


def _registry_dir() -> str:
    base = Path(__file__).resolve().parent.parent / "data" / "workspace_indexes"
    base.mkdir(parents=True, exist_ok=True)
    return str(base)


def _registry_path(workspace_root: str) -> str:
    return os.path.join(_registry_dir(), f"{workspace_key(workspace_root)}.json")


def _load_registry(workspace_root: str) -> Dict[str, Any]:
    path = _registry_path(workspace_root)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_registry(workspace_root: str, meta: Dict[str, Any]) -> None:
    path = _registry_path(workspace_root)
    payload = {
        "workspace_root": os.path.abspath(workspace_root),
        **meta,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def _split_into_chunks(text: str, chunk_size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> List[str]:
    if not text or not text.strip():
        return []
    if len(text) <= chunk_size:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+|\n{2,}", text)
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for sentence in sentences:
        if not sentence:
            continue
        slen = len(sentence)
        if slen > chunk_size:
            if current:
                chunks.append(" ".join(current))
                current, current_len = [], 0
            for i in range(0, slen, chunk_size - overlap):
                chunks.append(sentence[i : i + chunk_size])
            continue
        if current_len + slen + 1 > chunk_size and current:
            chunks.append(" ".join(current))
            tail = " ".join(current)[-overlap:] if overlap else ""
            current = [tail, sentence] if tail else [sentence]
            current_len = sum(len(x) + 1 for x in current)
        else:
            current.append(sentence)
            current_len += slen + 1
    if current:
        chunks.append(" ".join(current))
    return [c for c in chunks if c.strip()]


class _GitignoreRules:
    def __init__(self) -> None:
        self._rules: List[Tuple[str, bool, bool]] = []

    def add_file(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("/"):
                        line = line[1:]
                    negated = line.startswith("!")
                    if negated:
                        line = line[1:]
                    dir_only = line.endswith("/")
                    pat = line.rstrip("/")
                    self._rules.append((pat, negated, dir_only))
        except OSError:
            pass

    def ignored(self, relpath: str, is_dir: bool = False) -> bool:
        path = relpath.replace("\\", "/").lstrip("./")
        for pattern, negated, dir_only in self._rules:
            if negated:
                continue
            pat = pattern
            if "/" in pat:
                target = path
            else:
                target = os.path.basename(path)
            if fnmatch.fnmatch(target, pat) or fnmatch.fnmatch(path, pat):
                return True
            if dir_only and (path == pat or path.startswith(pat + "/")):
                return True
        return False


def _should_index_file(rel_path: str, name: str, gitignore: _GitignoreRules) -> bool:
    if gitignore.ignored(rel_path):
        return False
    lower = name.lower()
    if lower in _SPECIAL_FILENAMES or lower.startswith("readme"):
        return True
    ext = Path(name).suffix.lower()
    return ext in _WORKSPACE_EXTENSIONS


def _iter_workspace_files(workspace_root: str) -> Iterable[Tuple[str, str]]:
    """Yield (absolute_path, relative_posix_path) for indexable files."""
    root = os.path.abspath(workspace_root)
    gitignore = _GitignoreRules()
    gi_path = os.path.join(root, ".gitignore")
    if os.path.isfile(gi_path):
        gitignore.add_file(gi_path)

    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir_posix = "" if rel_dir == "." else rel_dir.replace("\\", "/")

        for fname in sorted(filenames):
            if fname.startswith(".") and fname not in {".env.example"}:
                continue
            rel = fname if not rel_dir_posix else f"{rel_dir_posix}/{fname}"
            if gitignore.ignored(rel):
                continue
            if not _should_index_file(rel, fname, gitignore):
                continue
            full = os.path.join(dirpath, fname)
            try:
                if os.path.getsize(full) > _MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield full, rel
            count += 1
            if count >= _MAX_FILES:
                return


def _chunk_doc_id(workspace_root: str, rel_path: str, chunk_idx: int, text: str) -> str:
    key = f"{os.path.abspath(workspace_root)}\x00{rel_path}\x00{chunk_idx}\x00{text[:64]}"
    return f"ws_{hashlib.sha256(key.encode('utf-8')).hexdigest()[:20]}"


class WorkspaceVectorStore:
    """Chroma-backed semantic index scoped by workspace_root metadata."""

    def __init__(self) -> None:
        self._lanes = []
        self._healthy = False
        self._initialize()

    def _initialize(self) -> None:
        try:
            self._lanes = build_embedding_lanes(COLLECTION_NAME)
            if not self._lanes:
                raise RuntimeError("No embedding lanes available")
            migrate_legacy_collection(COLLECTION_NAME, self._lanes)
            self._healthy = True
            logger.info(
                "WorkspaceVectorStore ready (lanes=%s chunks=%s)",
                [lane.name for lane in self._lanes],
                lane_count(self._lanes),
            )
        except Exception as e:
            logger.warning("WorkspaceVectorStore init failed: %s", e)

    @property
    def healthy(self) -> bool:
        return self._healthy

    def _collections_for_delete(self):
        collections = []
        seen = set()
        for lane in self._lanes:
            key = getattr(lane.collection, "name", None) or id(lane.collection)
            if key not in seen:
                seen.add(key)
                collections.append(lane.collection)
        return collections

    def remove_workspace(self, workspace_root: str) -> int:
        if not self._healthy:
            return 0
        ws = os.path.abspath(workspace_root)
        removed = 0
        for collection in self._collections_for_delete():
            try:
                results = collection.get(include=["metadatas"])
                ids = [
                    results["ids"][i]
                    for i, m in enumerate(results["metadatas"])
                    if isinstance(m, dict) and m.get("workspace_root") == ws
                ]
                if ids:
                    collection.delete(ids=ids)
                    removed += len(ids)
            except Exception as e:
                logger.warning("remove_workspace failed: %s", e)
        return removed

    def index_workspace(self, workspace_root: str, *, force: bool = False) -> Dict[str, Any]:
        ws = os.path.abspath(workspace_root)
        if not os.path.isdir(ws):
            return {"success": False, "message": f"Not a directory: {ws}"}
        if not self._healthy:
            return {"success": False, "message": "Workspace index unavailable (Chroma/embeddings offline)"}

        reg = _load_registry(ws)
        if (
            not force
            and reg.get("chunk_count", 0) > 0
            and (time.time() - float(reg.get("indexed_at", 0))) < _STALE_SECONDS
        ):
            return {
                "success": True,
                "skipped": True,
                "file_count": reg.get("file_count", 0),
                "chunk_count": reg.get("chunk_count", 0),
                "message": "Workspace index is fresh — skipped re-index",
            }

        self.remove_workspace(ws)
        file_count = 0
        chunk_count = 0
        failed = 0

        for full_path, rel_path in _iter_workspace_files(ws):
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(_MAX_FILE_BYTES + 1)
                if not content.strip():
                    continue
                if len(content) > _MAX_FILE_BYTES:
                    content = content[:_MAX_FILE_BYTES]
                file_count += 1
                for i, chunk in enumerate(_split_into_chunks(content)):
                    meta = {
                        "workspace_root": ws,
                        "source": full_path,
                        "rel_path": rel_path,
                        "filename": os.path.basename(full_path),
                        "chunk_id": i,
                        "index_kind": "workspace",
                    }
                    doc_id = _chunk_doc_id(ws, rel_path, i, chunk)
                    for lane in self._lanes:
                        try:
                            existing = lane.collection.get(ids=[doc_id])
                            if existing.get("ids"):
                                continue
                            lane.collection.add(
                                ids=[doc_id],
                                embeddings=lane.encode([chunk]),
                                documents=[chunk],
                                metadatas=[meta],
                            )
                        except Exception as e:
                            logger.warning("workspace index add failed (%s): %s", rel_path, e)
                            failed += 1
                            break
                    else:
                        chunk_count += 1
            except OSError as e:
                logger.warning("workspace index read %s: %s", rel_path, e)
                failed += 1

        _save_registry(ws, {
            "indexed_at": time.time(),
            "file_count": file_count,
            "chunk_count": chunk_count,
            "failed_count": failed,
        })
        return {
            "success": True,
            "file_count": file_count,
            "chunk_count": chunk_count,
            "failed_count": failed,
            "message": f"Indexed {file_count} files ({chunk_count} chunks) from workspace",
        }

    def search(self, workspace_root: str, query: str, k: int = 12) -> List[Dict[str, Any]]:
        if not self._healthy or not query.strip():
            return []
        ws = os.path.abspath(workspace_root)
        if lane_count(self._lanes) == 0:
            return []

        query_words = set(query.lower().split())
        candidates: List[Dict[str, Any]] = []
        try:
            for lane, results in query_lanes(
                self._lanes,
                query,
                n_results=min(max(k * 4, 24), 80),
                where={"workspace_root": ws},
                include=["documents", "metadatas", "distances"],
                raise_if_all_failed=False,
            ):
                for idx in range(len(results["ids"][0])):
                    doc_text = results["documents"][0][idx]
                    meta = results["metadatas"][0][idx]
                    distance = results["distances"][0][idx]
                    vector_sim = 1.0 - distance
                    doc_words = set(doc_text.lower().split())
                    overlap = len(query_words & doc_words)
                    keyword_score = overlap / len(query_words) if query_words else 0.0
                    hybrid = (VECTOR_WEIGHT * vector_sim) + (KEYWORD_WEIGHT * keyword_score)
                    candidates.append({
                        "document": doc_text,
                        "metadata": meta,
                        "similarity": round(hybrid, 4),
                        "rel_path": (meta or {}).get("rel_path", ""),
                    })
        except Exception as e:
            logger.warning("workspace search failed: %s", e)
            return []

        candidates.sort(key=lambda c: c["similarity"], reverse=True)
        top = dedupe_results(candidates, limit=k)
        return [c for c in top if c.get("similarity", 0) >= _SEARCH_SIMILARITY_FLOOR]


_store: Optional[WorkspaceVectorStore] = None
_last_attempt = 0.0
_RETRY_INTERVAL = 30.0


def get_workspace_index_store() -> Optional[WorkspaceVectorStore]:
    global _store, _last_attempt
    if _store is not None and _store.healthy:
        return _store
    now = time.monotonic()
    if now - _last_attempt < _RETRY_INTERVAL and _store is not None:
        return _store if _store.healthy else None
    _last_attempt = now
    try:
        _store = WorkspaceVectorStore()
    except Exception as e:
        logger.warning("get_workspace_index_store: %s", e)
        _store = None
    return _store if (_store and _store.healthy) else None


def ensure_workspace_index(workspace_root: str, *, force: bool = False) -> Dict[str, Any]:
    store = get_workspace_index_store()
    if not store:
        return {"success": False, "message": "Workspace index unavailable (start Chroma + embeddings)"}
    return store.index_workspace(workspace_root, force=force)


def search_workspace_code(workspace_root: str, query: str, k: int = 12) -> List[Dict[str, Any]]:
    store = get_workspace_index_store()
    if not store:
        return []
    return store.search(workspace_root, query, k=k)


def format_search_hits(hits: List[Dict[str, Any]], *, max_chars: int = 24_000) -> str:
    if not hits:
        return ""
    lines = ["### Indexed codebase search (semantic)", ""]
    budget = max_chars
    for i, hit in enumerate(hits, 1):
        rel = hit.get("rel_path") or (hit.get("metadata") or {}).get("rel_path") or "?"
        sim = hit.get("similarity", 0)
        doc = (hit.get("document") or "").strip()
        if not doc:
            continue
        block = f"#### [{i}] `{rel}` (score {sim})\n```\n{doc}\n```"
        if len(block) > budget:
            block = block[: budget - 40] + "\n... [truncated]\n```"
        lines.append(block)
        lines.append("")
        budget -= len(block)
        if budget <= 0:
            break
    return "\n".join(lines).strip()


async def run_workspace_index_tool(content: str, *, workspace: Optional[str]) -> Dict[str, Any]:
    if not workspace:
        return {"error": "workspace_index: no active workspace selected", "exit_code": 1}
    force = False
    raw = (content or "").strip()
    if raw.startswith("{"):
        try:
            args = json.loads(raw)
            force = bool(args.get("force"))
        except json.JSONDecodeError:
            pass
    result = ensure_workspace_index(workspace, force=force)
    if not result.get("success"):
        return {"error": result.get("message", "index failed"), "exit_code": 1}
    msg = result.get("message", "Indexed workspace")
    if result.get("skipped"):
        msg = (
            f"{msg} ({result.get('file_count', 0)} files, "
            f"{result.get('chunk_count', 0)} chunks). Pass {{\"force\": true}} to rebuild."
        )
    else:
        msg = (
            f"{msg}: {result.get('file_count', 0)} files, "
            f"{result.get('chunk_count', 0)} chunks."
        )
    return {"output": msg, "exit_code": 0}


async def run_workspace_search_tool(content: str, *, workspace: Optional[str]) -> Dict[str, Any]:
    if not workspace:
        return {"error": "workspace_search: no active workspace selected", "exit_code": 1}
    query = (content or "").strip()
    k = 12
    if query.startswith("{"):
        try:
            args = json.loads(query)
            query = str(args.get("query", "")).strip()
            k = int(args.get("k") or 12)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    if not query:
        return {"error": "workspace_search: query is required", "exit_code": 1}
    ensure_workspace_index(workspace, force=False)
    hits = search_workspace_code(workspace, query, k=max(1, min(k, 30)))
    if not hits:
        return {"output": "No indexed matches. Run workspace_index first or broaden the query.", "exit_code": 0}
    out_lines = []
    for hit in hits:
        rel = hit.get("rel_path") or "?"
        sim = hit.get("similarity", 0)
        snippet = (hit.get("document") or "")[:800]
        out_lines.append(f"{rel} (score {sim}):\n{snippet}\n---")
    return {"output": "\n".join(out_lines), "exit_code": 0}
