"""Hybrid code-location orchestration for 'which file handles X' questions.

2026 pattern for small local models: lexical grep first, optional semantic
index, progressive on-disk file scan, then a synthesized answer so the agent
does not have to reliably tool-call on a 7B model.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from src.workspace_locate_intent import is_workspace_locate_request

_MAX_GREP_PASSES = 6
_MAX_HITS_PER_PASS = 60
_MAX_READ_FILES = 6
_MAX_READ_CHARS = 12_000
_MAX_CONTEXT_CHARS = 18_000
_MAX_DEEP_SCAN_FILES = 24
_MAX_SCAN_BYTES_PER_FILE = 120_000
_SKIP_PATH_FRAGMENTS = (
    "node_modules/", ".git/", "dist/", "build/", ".next/",
    "package-lock.json", "yarn.lock", "pnpm-lock",
)

# Default passes for UI / GitHub README embedding questions.
_DEFAULT_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (
        r"github\.com|raw\.githubusercontent|api\.github\.com/repos",
        "**/*.{js,ts,tsx,jsx,vue,html,py,svelte,css}",
    ),
    ("readme|README", "**/*.{js,ts,tsx,jsx,vue,html,py,svelte}"),
    ("home|landing|welcome|HomePage|Landing", "**/*.{js,ts,tsx,jsx,vue,html,svelte}"),
    ("fetch.*readme|load.*readme|render.*readme", "**/*.{js,ts,tsx,jsx,vue,html,py}"),
)

_KEYWORD_PATTERNS: Tuple[Tuple[re.Pattern[str], Tuple[str, str]], ...] = (
    (
        re.compile(r"\bgithub\b", re.I),
        (r"github\.com|raw\.githubusercontent|api\.github\.com", "**/*.{js,ts,tsx,jsx,vue,html,py,svelte}"),
    ),
    (re.compile(r"\breadme\b", re.I), ("readme|README", "**/*.{js,ts,tsx,jsx,vue,html,py,svelte}")),
    (
        re.compile(r"\bhome\s*page\b|\blanding\b", re.I),
        ("home|landing|index|Home|Landing", "**/*.{js,ts,tsx,jsx,vue,html,svelte}"),
    ),
    (
        re.compile(r"\bfetch\b|\bload\b|\brender\b", re.I),
        ("fetch|load|render", "**/*.{js,ts,tsx,jsx,vue,html,py,svelte}"),
    ),
)

_UI_PATH_HINTS = re.compile(
    r"(?:^|/)(?:pages?|views?|routes?|components?|screens?|app)/|"
    r"(?:home|landing|index|main|page|app)\.[jt]sx?$",
    re.I,
)


@dataclass
class AutoWorkspaceLocateResult:
    workspace: Optional[str]
    search_context: str
    files_read: List[str]
    grep_hit_count: int
    assistant_message: str
    tool_events: List[Dict[str, Any]] = field(default_factory=list)
    skip_llm: bool = False
    fallback_summary: str = ""


def derive_grep_passes(user_msg: str) -> List[Tuple[str, str]]:
    """Return (pattern, glob) pairs to run for this question."""
    passes: List[Tuple[str, str]] = []
    seen: Set[str] = set()
    for rx, pair in _KEYWORD_PATTERNS:
        if rx.search(user_msg or ""):
            if pair[0] not in seen:
                passes.append(pair)
                seen.add(pair[0])
    for pair in _DEFAULT_PATTERNS:
        if pair[0] not in seen:
            passes.append(pair)
            seen.add(pair[0])
        if len(passes) >= _MAX_GREP_PASSES:
            break
    return passes[:_MAX_GREP_PASSES]


def derive_scan_keywords(user_msg: str) -> List[str]:
    """Keywords for progressive on-disk file scan."""
    low = (user_msg or "").lower()
    kws: List[str] = []
    if "github" in low:
        kws.extend(["github.com", "raw.githubusercontent", "api.github.com", "github"])
    if "readme" in low:
        kws.extend(["readme", "README"])
    if re.search(r"\bhome\s*page\b|\blanding\b", low):
        kws.extend(["home", "landing", "homepage"])
    if "fetch" in low or "load" in low:
        kws.extend(["fetch", "load", "axios", "get"])
    if "render" in low or "show" in low or "display" in low:
        kws.extend(["render", "markdown", "dangerouslySetInnerHTML"])
    if not kws:
        kws = ["github", "readme", "fetch", "home", "render"]
    seen: Set[str] = set()
    out: List[str] = []
    for k in kws:
        kl = k.lower()
        if kl not in seen:
            seen.add(kl)
            out.append(k)
    return out


def _should_skip_path(path: str) -> bool:
    low = path.replace("\\", "/").lower()
    return any(skip in low for skip in _SKIP_PATH_FRAGMENTS)


def _parse_grep_paths(output: str) -> List[str]:
    """Extract unique relative file paths from grep file:line:match output."""
    paths: List[str] = []
    seen: Set[str] = set()
    for line in (output or "").splitlines():
        if ":" not in line:
            continue
        path = line.split(":", 1)[0].strip()
        if not path or path in seen or _should_skip_path(path):
            continue
        seen.add(path)
        paths.append(path)
    return paths


def parse_grep_evidence(output: str) -> List[Tuple[str, int, str]]:
    """Parse grep lines into (path, line_no, line_text)."""
    evidence: List[Tuple[str, int, str]] = []
    for line in (output or "").splitlines():
        if ":" not in line:
            continue
        path, rest = line.split(":", 1)
        path = path.strip()
        if not path or _should_skip_path(path):
            continue
        if ":" in rest:
            num_s, text = rest.split(":", 1)
            try:
                num = int(num_s.strip())
            except ValueError:
                num = 0
                text = rest
        else:
            num, text = 0, rest
        evidence.append((path, num, text.strip()))
    return evidence


def rank_locate_candidates(
    evidence: List[Tuple[str, int, str]],
    user_msg: str,
    *,
    semantic_paths: Optional[List[str]] = None,
) -> List[Tuple[str, int, List[str]]]:
    """Rank files by grep + path heuristics. Returns (path, score, evidence_lines)."""
    scores: Dict[str, int] = {}
    lines_by_path: Dict[str, List[str]] = {}
    low_msg = (user_msg or "").lower()
    wants_github = "github" in low_msg
    wants_readme = "readme" in low_msg
    wants_home = bool(re.search(r"\bhome\s*page\b|\blanding\b|\bweb\s*page\b", low_msg))

    for path, line_no, text in evidence:
        low_path = path.replace("\\", "/").lower()
        low_text = text.lower()
        score = scores.get(path, 0) + 4
        if wants_github and "github" in low_text:
            score += 12
        if wants_readme and "readme" in low_text:
            score += 12
        if wants_github and wants_readme and "github" in low_text and "readme" in low_text:
            score += 20
        if wants_home and _UI_PATH_HINTS.search(path):
            score += 10
        if _UI_PATH_HINTS.search(path):
            score += 6
        if low_path.endswith((".tsx", ".jsx", ".vue", ".svelte")):
            score += 4
        if low_path.endswith(("index.html", "app.tsx", "app.jsx", "main.tsx")):
            score += 3
        if "test" in low_path or "spec." in low_path:
            score -= 6
        scores[path] = score
        label = f"{path}:{line_no}: {text}" if line_no else f"{path}: {text}"
        lines_by_path.setdefault(path, []).append(label)

    for i, path in enumerate(semantic_paths or []):
        if _should_skip_path(path):
            continue
        bonus = max(8, 14 - i)
        scores[path] = scores.get(path, 0) + bonus
        lines_by_path.setdefault(path, []).append(f"(semantic index rank #{i + 1})")

    ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
    return [(p, s, lines_by_path.get(p, [])[:12]) for p, s in ranked]


def _scan_file_on_disk(
    workspace_root: str,
    rel_path: str,
    keywords: List[str],
) -> List[Tuple[int, str]]:
    """Scan a file for keyword hits without going through the tool layer."""
    abs_path = os.path.join(workspace_root, rel_path.replace("/", os.sep))
    if not os.path.isfile(abs_path):
        return []
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            data = f.read(_MAX_SCAN_BYTES_PER_FILE)
    except OSError:
        return []
    hits: List[Tuple[int, str]] = []
    kw_low = [k.lower() for k in keywords]
    for i, line in enumerate(data.splitlines(), 1):
        low = line.lower()
        if any(k in low for k in kw_low):
            hits.append((i, line.strip()[:240]))
    return hits


def synthesize_locate_answer(
    *,
    user_msg: str,
    ranked: List[Tuple[str, int, List[str]]],
    files_read: List[str],
) -> Tuple[str, bool]:
    """Build a deterministic answer for local LLMs. Returns (text, confident)."""
    if not ranked:
        return "", False
    top_path, top_score, top_evidence = ranked[0]
    if top_score < 6:
        return "", False

    low_msg = (user_msg or "").lower()
    lines = [
        f"**`{top_path}`** is the most likely source file for your question.",
        "",
    ]
    if "github" in low_msg and "readme" in low_msg:
        lines.append(
            "It (or a component it imports) likely fetches or renders a GitHub "
            "repository README on the site — not GitHub.com's own README.md page "
            "and not a generic `public/index.html` unless grep proved it."
        )
        lines.append("")
    if top_evidence:
        lines.append("**Evidence (grep / scan):**")
        for ev in top_evidence[:10]:
            lines.append(f"- `{ev}`")
        lines.append("")
    if len(ranked) > 1 and ranked[1][1] >= top_score * 0.55:
        lines.append(f"Also review **`{ranked[1][0]}`** (secondary match).")
        lines.append("")
    if files_read:
        lines.append("**Files read:** " + ", ".join(f"`{p}`" for p in files_read))
        lines.append("")

    confident = (
        top_score >= 14
        or (top_score >= 8 and len(top_evidence) >= 2)
        or any(
            "github" in e.lower() and "readme" in e.lower()
            for e in top_evidence
        )
    )
    if confident:
        lines.append(
            "_Answer synthesized from automated grep + file scan — "
            "no model tool-calling required._"
        )
    return "\n".join(lines).strip(), confident


async def _glob_candidate_paths(workspace: str, user_msg: str) -> List[str]:
    """Fallback path list when grep is sparse — UI-ish filenames first."""
    from src.tool_execution import _direct_fallback

    patterns = [
        "**/*.{tsx,jsx,vue,html,svelte}",
        "**/pages/**/*.{tsx,jsx,vue,html}",
        "**/components/**/*.{tsx,jsx,vue}",
    ]
    paths: List[str] = []
    seen: Set[str] = set()
    name_hints = re.compile(r"home|landing|index|main|page|app|readme|github", re.I)
    for pat in patterns:
        result = await _direct_fallback("glob", pat, workspace=workspace)
        output = (result or {}).get("output") or ""
        for line in output.splitlines():
            p = line.strip()
            if not p or p in seen or _should_skip_path(p):
                continue
            if name_hints.search(p) or name_hints.search(user_msg or ""):
                paths.append(p)
                seen.add(p)
    return paths[:40]


async def run_auto_workspace_locate_if_needed(
    *,
    user_msg: str,
    workspace: Optional[str],
    approved_plan: str = "",
    session_id: Optional[str],
    owner: Optional[str],
) -> Optional[AutoWorkspaceLocateResult]:
    """Hybrid locate pipeline before the LLM runs."""
    if not session_id or not workspace:
        return None
    if not is_workspace_locate_request(user_msg):
        return None

    from src.shell_orchestration import resolve_effective_workspace
    from src.tool_execution import _direct_fallback

    effective_ws = resolve_effective_workspace(workspace, user_msg, approved_plan) or workspace
    if not os.path.isdir(effective_ws):
        return None

    tool_events: List[Dict[str, Any]] = []
    grep_sections: List[str] = []
    all_evidence: List[Tuple[str, int, str]] = []
    total_hits = 0

    for pattern, glob_pat in derive_grep_passes(user_msg):
        payload = json.dumps({
            "pattern": pattern,
            "glob": glob_pat,
            "ignore_case": True,
            "max_results": _MAX_HITS_PER_PASS,
        })
        result = await _direct_fallback("grep", payload, workspace=effective_ws)
        output = (result or {}).get("output") or (result or {}).get("error") or ""
        exit_code = (result or {}).get("exit_code", 1)
        tool_events.append({
            "tool": "grep",
            "command": pattern[:120],
            "output": output[:3000],
            "exit_code": exit_code,
            "description": f"grep {pattern[:60]}",
        })
        if exit_code != 0 or not output.strip():
            continue
        hits = [ln for ln in output.splitlines() if ln.strip()]
        total_hits += len(hits)
        grep_sections.append(f"#### pattern `{pattern}`\n```\n" + "\n".join(hits[:40]) + "\n```")
        all_evidence.extend(parse_grep_evidence(output))

    semantic_paths: List[str] = []
    semantic_section = ""
    try:
        from src.workspace_index import (
            ensure_workspace_index,
            format_search_hits,
            search_workspace_code,
        )

        ensure_workspace_index(effective_ws)
        sem_hits = search_workspace_code(effective_ws, user_msg, k=10)
        if sem_hits:
            semantic_section = format_search_hits(sem_hits, max_chars=6000)
            semantic_paths = [
                h.get("rel_path") or (h.get("metadata") or {}).get("rel_path") or ""
                for h in sem_hits
            ]
            semantic_paths = [p for p in semantic_paths if p]
    except Exception:
        pass

    ranked = rank_locate_candidates(
        all_evidence, user_msg, semantic_paths=semantic_paths,
    )

    if not ranked and not grep_sections:
        extra_paths = await _glob_candidate_paths(effective_ws, user_msg)
        scan_kws = derive_scan_keywords(user_msg)
        for rel in extra_paths[:_MAX_DEEP_SCAN_FILES]:
            for line_no, text in _scan_file_on_disk(effective_ws, rel, scan_kws):
                all_evidence.append((rel, line_no, text))
        ranked = rank_locate_candidates(all_evidence, user_msg)
        if not ranked:
            return None

    scan_kws = derive_scan_keywords(user_msg)
    scan_paths = [p for p, _, _ in ranked[:_MAX_DEEP_SCAN_FILES]]
    for rel in scan_paths:
        for line_no, text in _scan_file_on_disk(effective_ws, rel, scan_kws):
            all_evidence.append((rel, line_no, text))
    ranked = rank_locate_candidates(
        all_evidence, user_msg, semantic_paths=semantic_paths,
    )

    files_read: List[str] = []
    read_sections: List[str] = []
    for rel, _, _ in ranked[:_MAX_READ_FILES]:
        read_result = await _direct_fallback("read_file", rel, workspace=effective_ws)
        exit_code = (read_result or {}).get("exit_code", 1)
        body = (read_result or {}).get("output") or (read_result or {}).get("error") or ""
        tool_events.append({
            "tool": "read_file",
            "command": rel,
            "output": body[:4000],
            "exit_code": exit_code,
            "description": f"Read {rel}",
        })
        if exit_code == 0 and body.strip():
            files_read.append(rel)
            read_sections.append(f"#### `{rel}`\n```\n{body[:_MAX_READ_CHARS]}\n```")

    search_context = (
        "## CODE SEARCH (auto — hybrid grep + scan ground truth)\n"
        f"Folder: {effective_ws}\n"
        f"Question: {user_msg.strip()[:500]}\n\n"
    )
    if ranked:
        search_context += "### Ranked candidates\n"
        for path, score, ev in ranked[:10]:
            preview = "; ".join(ev[:3]) if ev else "(no line evidence)"
            search_context += f"- `{path}` (score {score}): {preview[:300]}\n"
        search_context += "\n"
    if grep_sections:
        search_context += "\n\n".join(grep_sections)
    if semantic_section:
        search_context += "\n\n" + semantic_section
    if read_sections:
        search_context += "\n\n### Top matching files (excerpt)\n\n" + "\n\n".join(read_sections)
    if len(search_context) > _MAX_CONTEXT_CHARS:
        search_context = (
            search_context[: _MAX_CONTEXT_CHARS - 60]
            + "\n\n... [code search truncated]\n"
        )

    synthesized, confident = synthesize_locate_answer(
        user_msg=user_msg,
        ranked=ranked,
        files_read=files_read,
    )
    paths_preview = ", ".join(f"`{p}`" for p in (files_read or [r[0] for r in ranked[:5]]))
    if synthesized and confident:
        assistant_message = synthesized
        skip_llm = True
    else:
        assistant_message = (
            f"Code search found {total_hits} grep line(s)"
            + (f"; read {len(files_read)} file(s): {paths_preview}." if paths_preview else ".")
            + " Answer with the exact source file path(s) that implement what the user asked — "
            "cite grep evidence. Do NOT guess `public/index.html` or root README.md unless "
            "they appear in CODE SEARCH above."
        )
        if synthesized:
            assistant_message = synthesized + "\n\n" + assistant_message
        skip_llm = False

    return AutoWorkspaceLocateResult(
        workspace=effective_ws,
        search_context=search_context,
        files_read=files_read,
        grep_hit_count=total_hits,
        assistant_message=assistant_message,
        tool_events=tool_events,
        skip_llm=skip_llm,
        fallback_summary=synthesized,
    )


def format_workspace_locate_sse(
    auto: AutoWorkspaceLocateResult,
    *,
    round_num: int = 0,
) -> List[str]:
    out: List[str] = []
    for ev in auto.tool_events:
        tool = ev.get("tool", "grep")
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
