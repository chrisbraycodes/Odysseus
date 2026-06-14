"""Auto-create numbered workspace files and block unreliable bash shortcuts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.workspace_file_intent import is_workspace_file_create_request

_NUMBERED_END = re.compile(
    r"(?:increment(?:ing)?|counting)\s+(?:up\s+)?(?:to|through)\s+(\d+)\b",
    re.I,
)
_NUMBERED_UP_TO = re.compile(r"\bup\s+to\s+(\d+)\b", re.I)
_NAME_START = re.compile(r"\bname\s+(?:it|them|each)\s+(\d+)\b", re.I)

_BASH_FILE_CREATE = re.compile(
    r"(?:"
    r"(?:^|[;\n])\s*for\b[^;\n]*(?:>|>>|\becho\b)"
    r"|(?:^|[;\n])\s*while\b[^;\n]*(?:>|>>|\becho\b)"
    r"|\becho\b[^;\n|&]*(?:>|>>)"
    r"|\btouch\b"
    r"|\brm\b[^;\n]*\*\s*$"
    r")",
    re.I | re.M,
)


def parse_numbered_file_batch(text: str, *, max_files: int = 50) -> Optional[List[Tuple[str, str]]]:
    """Parse requests like 'name it 1 … incrementing up to 10' into (path, content) pairs."""
    if not text or not is_workspace_file_create_request(text):
        return None
    end_m = _NUMBERED_END.search(text) or _NUMBERED_UP_TO.search(text)
    if not end_m:
        return None
    try:
        end = int(end_m.group(1))
    except (TypeError, ValueError):
        return None
    start_m = _NAME_START.search(text)
    start = int(start_m.group(1)) if start_m else 1
    if end < start or (end - start + 1) > max_files:
        return None
    ext = ".txt" if re.search(r"\btxt\b|text\s+file", text, re.I) else ""
    return [(f"{i}{ext}", str(i)) for i in range(start, end + 1)]


def is_bash_file_creation_attempt(command: str) -> bool:
    """Detect bash loops / redirects used to create files (breaks under /bin/sh)."""
    if not command or not str(command).strip():
        return False
    return bool(_BASH_FILE_CREATE.search(str(command)))


@dataclass
class AutoWriteFilesResult:
    files: List[str]
    workspace: Optional[str]
    skip_llm: bool
    assistant_message: str
    tool_events: List[Dict[str, Any]] = field(default_factory=list)
    results: List[Dict[str, Any]] = field(default_factory=list)


async def run_auto_write_files_if_needed(
    *,
    user_msg: str,
    workspace: Optional[str],
    approved_plan: str = "",
    session_id: Optional[str],
    owner: Optional[str],
) -> Optional[AutoWriteFilesResult]:
    """Create numbered workspace files directly — no LLM or bash loop required."""
    if not session_id or not workspace:
        return None
    specs = parse_numbered_file_batch(user_msg)
    if not specs:
        return None

    from src.shell_orchestration import resolve_effective_workspace
    from src.tool_execution import _direct_fallback

    effective_ws = resolve_effective_workspace(workspace, user_msg, approved_plan) or workspace
    tool_events: List[Dict[str, Any]] = []
    created: List[str] = []
    raw_results: List[Dict[str, Any]] = []

    for path, body in specs:
        result = await _direct_fallback(
            "write_file",
            f"{path}\n{body}",
            workspace=effective_ws,
        )
        raw_results.append(result or {})
        exit_code = (result or {}).get("exit_code", 1)
        output = (result or {}).get("output") or (result or {}).get("error") or ""
        tool_events.append({
            "tool": "write_file",
            "command": path,
            "output": output,
            "exit_code": exit_code,
            "description": f"Write {path}",
        })
        if exit_code == 0:
            created.append(path)

    if not created:
        return None

    names = ", ".join(created[:6])
    if len(created) > 6:
        names += f", … ({len(created)} files total)"
    msg = f"Created {len(created)} file(s) in the workspace: {names}."
    return AutoWriteFilesResult(
        files=created,
        workspace=effective_ws,
        skip_llm=len(created) == len(specs),
        assistant_message=msg,
        tool_events=tool_events,
        results=raw_results,
    )


def format_write_files_sse(
    auto: AutoWriteFilesResult,
    *,
    round_num: int = 0,
) -> List[str]:
    """Build tool_start + tool_output SSE payloads for each write_file."""
    out: List[str] = []
    for i, ev in enumerate(auto.tool_events):
        start = {
            "type": "tool_start",
            "tool": "write_file",
            "command": ev.get("command", "")[:200],
            "round": round_num,
        }
        body = {
            "type": "tool_output",
            "tool": "write_file",
            "command": ev.get("command", "")[:200],
            "output": ev.get("output") or "",
            "exit_code": ev.get("exit_code", 0),
        }
        out.append(f"data: {json.dumps(start)}\n\n")
        out.append(f"data: {json.dumps(body)}\n\n")
    return out
