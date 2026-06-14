"""Auto-scan workspace for project-analysis requests (weak local models)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.workspace_analyze_intent import is_workspace_analyze_request

logger = logging.getLogger(__name__)

# Root-level files that usually explain a project (checked in order).
_MANIFEST_NAMES = (
    "README.md",
    "README",
    "readme.md",
    "Readme.md",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "Pipfile",
    "setup.py",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Dockerfile",
    "index.html",
    "app.py",
    "main.py",
    "server.py",
    "go.mod",
    "Cargo.toml",
    "composer.json",
    "Gemfile",
    "Makefile",
)

_MAX_FILES = 6
_MAX_FILE_CHARS = 4_000
_MAX_TOTAL_CHARS = 18_000
_MAX_INDEX_SEARCH_CHARS = 8_000
_MAX_TREE_CHARS = 2_500


@dataclass
class AutoWorkspaceScanResult:
    workspace: Optional[str]
    tree: str
    files_read: List[str]
    scan_context: str
    assistant_message: str
    fallback_summary: str = ""
    tool_events: List[Dict[str, Any]] = field(default_factory=list)


def _collect_manifest_paths(workspace: str) -> List[str]:
    """Return relative paths for manifest files that exist (root, then depth 1)."""
    found: List[str] = []
    seen = set()
    roots = [("", workspace)]
    try:
        for name in os.listdir(workspace):
            p = os.path.join(workspace, name)
            if os.path.isdir(p) and name not in {".git", "node_modules", "__pycache__", ".venv", "venv"}:
                roots.append((name + os.sep, p))
    except OSError:
        pass

    for prefix, root in roots:
        for manifest in _MANIFEST_NAMES:
            rel = prefix + manifest
            if rel in seen:
                continue
            full = os.path.join(workspace, rel.replace("/", os.sep).rstrip(os.sep))
            if os.path.isfile(full):
                found.append(rel.replace("\\", "/").rstrip("/"))
                seen.add(rel)
            if len(found) >= _MAX_FILES:
                return found
    return found


def _extract_readme_blurb(text: str, limit: int = 600) -> str:
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            if s.startswith("#") and len(lines) > 2:
                break
            if s.startswith("#"):
                lines.append(s.lstrip("#").strip())
            continue
        if s.startswith("![") or s.startswith("[!"):
            continue
        lines.append(s)
        if sum(len(x) + 1 for x in lines) >= limit:
            break
    blurb = " ".join(lines).strip()
    return blurb[:limit] if blurb else text.strip()[:limit]


def _summarize_package_json(text: str) -> str:
    import json
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text[:400]
    if not isinstance(data, dict):
        return text[:400]
    bits = []
    if data.get("name"):
        bits.append(f"name: {data['name']}")
    if data.get("description"):
        bits.append(f"description: {data['description']}")
    deps = data.get("dependencies") or {}
    dev = data.get("devDependencies") or {}
    if deps:
        bits.append("dependencies: " + ", ".join(sorted(deps.keys())[:12]))
    if dev:
        bits.append("devDependencies: " + ", ".join(sorted(dev.keys())[:8]))
    scripts = data.get("scripts") or {}
    if scripts:
        bits.append("scripts: " + ", ".join(list(scripts.keys())[:6]))
    return "; ".join(bits) or text[:400]


def synthesize_analyze_summary(
    *,
    workspace: str,
    tree: str,
    files_read: List[str],
    file_contents: Dict[str, str],
) -> str:
    """Rule-based summary when the LLM returns empty after a workspace scan."""
    lines = [
        f"## Project overview ({os.path.basename(workspace.rstrip('/\\')) or workspace})",
        "",
    ]
    readme_key = next((p for p in files_read if p.lower().startswith("readme")), None)
    if readme_key and file_contents.get(readme_key):
        lines.append("### What it is")
        lines.append(_extract_readme_blurb(file_contents[readme_key]))
        lines.append("")

    stack_bits = []
    if "package.json" in file_contents:
        stack_bits.append("**Node/npm:** " + _summarize_package_json(file_contents["package.json"]))
    if "pyproject.toml" in file_contents:
        stack_bits.append("**Python:** pyproject.toml present")
    if "requirements.txt" in file_contents:
        first = file_contents["requirements.txt"].splitlines()[:8]
        stack_bits.append(
            "**Python deps:** "
            + ", ".join(l.strip() for l in first if l.strip() and not l.startswith("#"))
        )
    if "docker-compose.yml" in file_contents or "docker-compose.yaml" in file_contents:
        stack_bits.append("**Docker:** docker-compose configuration found")
    if "Dockerfile" in file_contents:
        stack_bits.append("**Docker:** Dockerfile present")

    if stack_bits:
        lines.append("### Tools / stack")
        lines.extend(stack_bits)
        lines.append("")

    if tree.strip():
        lines.append("### Top-level layout")
        lines.append("```")
        lines.append(tree.strip()[:1500])
        lines.append("```")
        lines.append("")

    if files_read:
        lines.append("### Key files read")
        lines.append(", ".join(f"`{p}`" for p in files_read))
        lines.append("")

    lines.append(
        "_Auto-generated from workspace scan — the model did not produce a follow-up summary._"
    )
    return "\n".join(lines).strip()


def cap_scan_context(scan_context: str, max_chars: int = 14_000) -> str:
    if len(scan_context) <= max_chars:
        return scan_context
    return (
        scan_context[: max_chars - 80]
        + "\n\n... [workspace scan truncated to fit model context]\n"
    )


async def run_auto_workspace_scan_if_needed(
    *,
    user_msg: str,
    workspace: Optional[str],
    approved_plan: str = "",
    session_id: Optional[str],
    owner: Optional[str],
) -> Optional[AutoWorkspaceScanResult]:
    """List workspace + read key manifests before the LLM runs."""
    if not session_id or not workspace:
        return None
    if not is_workspace_analyze_request(user_msg):
        return None

    from src.shell_orchestration import resolve_effective_workspace
    from src.tool_execution import _direct_fallback

    effective_ws = resolve_effective_workspace(workspace, user_msg, approved_plan) or workspace
    if not os.path.isdir(effective_ws):
        return None

    tool_events: List[Dict[str, Any]] = []
    parts: List[str] = []
    files_read: List[str] = []
    file_contents: Dict[str, str] = {}
    budget = _MAX_TOTAL_CHARS

    ls_result = await _direct_fallback("ls", "", workspace=effective_ws)
    tree = (ls_result or {}).get("output") or (ls_result or {}).get("error") or ""
    tool_events.append({
        "tool": "ls",
        "command": effective_ws,
        "output": tree,
        "exit_code": (ls_result or {}).get("exit_code", 1),
        "description": "List workspace root",
    })
    if tree:
        parts.append(f"### Workspace listing\n```\n{tree[:_MAX_TREE_CHARS]}\n```")
        budget -= min(len(tree), _MAX_TREE_CHARS)

    for rel in _collect_manifest_paths(effective_ws):
        if budget <= 0 or len(files_read) >= _MAX_FILES:
            break
        read_result = await _direct_fallback(
            "read_file",
            rel,
            workspace=effective_ws,
        )
        exit_code = (read_result or {}).get("exit_code", 1)
        output = (read_result or {}).get("output") or (read_result or {}).get("error") or ""
        tool_events.append({
            "tool": "read_file",
            "command": rel,
            "output": output[:2000],
            "exit_code": exit_code,
            "description": f"Read {rel}",
        })
        if exit_code != 0 or not output.strip():
            continue
        file_contents[rel] = output
        chunk = output[: min(_MAX_FILE_CHARS, budget)]
        parts.append(f"### {rel}\n```\n{chunk}\n```")
        files_read.append(rel)
        budget -= len(chunk)

    # Deep path: vector index + semantic search across the repo (requires Chroma).
    try:
        from src.workspace_index import (
            ensure_workspace_index,
            format_search_hits,
            search_workspace_code,
        )
        index_result = ensure_workspace_index(effective_ws, force=False)
        if index_result.get("success"):
            tool_events.append({
                "tool": "workspace_index",
                "command": effective_ws,
                "output": index_result.get("message", "")[:2000],
                "exit_code": 0,
                "description": "Index workspace for deep search",
            })
            if not index_result.get("skipped"):
                parts.append(
                    f"### Workspace index\n{index_result.get('message', '')} "
                    f"({index_result.get('file_count', 0)} files, "
                    f"{index_result.get('chunk_count', 0)} chunks)"
                )
            hits = search_workspace_code(effective_ws, user_msg, k=12)
            if hits:
                search_block = format_search_hits(hits, max_chars=_MAX_INDEX_SEARCH_CHARS)
                if search_block:
                    parts.append(search_block)
                    budget -= min(len(search_block), _MAX_INDEX_SEARCH_CHARS)
                tool_events.append({
                    "tool": "workspace_search",
                    "command": user_msg[:200],
                    "output": f"{len(hits)} matches",
                    "exit_code": 0,
                    "description": "Semantic codebase search",
                })
    except Exception as deep_err:
        logger.warning("[auto-workspace-scan] deep index skipped: %s", deep_err)

    if not parts:
        return None

    scan_context = cap_scan_context(
        "## WORKSPACE SCAN (auto — use as ground truth)\n"
        f"Folder: {effective_ws}\n\n"
        + "\n\n".join(parts)
    )
    fallback_summary = synthesize_analyze_summary(
        workspace=effective_ws,
        tree=tree,
        files_read=files_read,
        file_contents=file_contents,
    )
    names = ", ".join(files_read[:5])
    if len(files_read) > 5:
        names += f", … ({len(files_read)} files)"
    msg = (
        f"Scanned workspace: listed root"
        + (f" and read {len(files_read)} file(s) ({names})." if files_read else ".")
        + " Write a concise summary: what this project/website is and what tools/stack it uses."
        + " Do NOT call read_file again for files already in WORKSPACE SCAN — answer now."
    )
    return AutoWorkspaceScanResult(
        workspace=effective_ws,
        tree=tree,
        files_read=files_read,
        scan_context=scan_context,
        assistant_message=msg,
        fallback_summary=fallback_summary,
        tool_events=tool_events,
    )


def format_workspace_scan_sse(
    auto: AutoWorkspaceScanResult,
    *,
    round_num: int = 0,
) -> List[str]:
    """Build tool_start + tool_output SSE payloads for scan tools."""
    import json

    out: List[str] = []
    for ev in auto.tool_events:
        tool = ev.get("tool", "read_file")
        start = {
            "type": "tool_start",
            "tool": tool,
            "command": ev.get("command", "")[:200],
            "round": round_num,
        }
        body = {
            "type": "tool_output",
            "tool": tool,
            "command": ev.get("command", "")[:200],
            "output": ev.get("output") or "",
            "exit_code": ev.get("exit_code", 0),
        }
        out.append(f"data: {json.dumps(start)}\n\n")
        out.append(f"data: {json.dumps(body)}\n\n")
    return out
