"""Auto-create numbered workspace files and block unreliable bash shortcuts."""

from __future__ import annotations

import json
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
_COUNT_TXT = re.compile(r"\b(?:create|make|generate|write|add)\s+(\d+)\s+\.?txt\b", re.I)
_COUNT_TXT_FILES = re.compile(r"\b(\d+)\s+\.?txt\s+files?\b", re.I)
_NUMBERED_START = re.compile(r"\b(?:numbered|labeled|called|named|starting\s+(?:at|from))\s+(\d+)\.txt\b", re.I)
_IN_FILE_CONTENT = re.compile(
    r"\b(?:in|for)\s+(\d+)\.txt\b[^0-9\n]{0,25}(\d+)\b",
    re.I,
)

_BASH_FILE_CREATE = re.compile(
    r"(?:"
    r"(?:^|[;\n])\s*for\b[^;\n]*(?:>|>>|\becho\b)"
    r"|(?:^|[;\n])\s*while\b[^;\n]*(?:>|>>|\becho\b)"
    r"|\becho\b[^;\n|&]*(?:>|>>)"
    r"|\btouch\b"
    r"|(?:^|[;\n])\s*make\s+\d+\s*$"
    r"|\brm\b[^;\n]*\*\s*$"
    r")",
    re.I | re.M,
)


def _parse_counted_txt_batch(text: str, *, max_files: int) -> Optional[List[Tuple[str, str]]]:
    """Parse 'create 10 txt files numbered 1.txt … in 1.txt 10, in 2.txt 9 …'."""
    count_m = _COUNT_TXT.search(text) or _COUNT_TXT_FILES.search(text)
    if not count_m:
        return None
    try:
        count = int(count_m.group(1))
    except (TypeError, ValueError):
        return None
    if count < 1 or count > max_files:
        return None

    start_m = _NUMBERED_START.search(text)
    if start_m:
        start = int(start_m.group(1))
    else:
        list_m = re.search(r"\b(?:numbered|labeled|called|named)\b[^.\n]{0,40}\b(\d+)\.txt\b", text, re.I)
        start = int(list_m.group(1)) if list_m else 1
    end = start + count - 1

    ext = ".txt" if re.search(r"\btxt\b|text\s+file", text, re.I) else ""
    pairs = sorted((int(a), int(b)) for a, b in _IN_FILE_CONTENT.findall(text))
    if len(pairs) >= 2:
        i1, c1 = pairs[0]
        i2, c2 = pairs[1]
        if i2 == i1:
            return None
        step = (c2 - c1) / (i2 - i1)
        if step != int(step):
            return None
        step = int(step)
        specs: List[Tuple[str, str]] = []
        for i in range(start, end + 1):
            content = str(int(c1 + step * (i - i1)))
            specs.append((f"{i}{ext}", content))
        return specs

    return [(f"{i}{ext}", str(i)) for i in range(start, end + 1)]


def _parse_increment_batch(text: str, *, max_files: int) -> Optional[List[Tuple[str, str]]]:
    """Parse 'name it 1 … incrementing up to 10' into (path, content) pairs."""
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


def parse_numbered_file_batch(text: str, *, max_files: int = 50) -> Optional[List[Tuple[str, str]]]:
    """Parse numbered multi-file create requests into (path, content) pairs."""
    if not text or not is_workspace_file_create_request(text):
        return None
    return _parse_counted_txt_batch(text, max_files=max_files) or _parse_increment_batch(
        text, max_files=max_files
    )


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
