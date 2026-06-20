"""LLM intent planner: natural language → structured workspace file writes."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from src.workspace_file_intent import is_workspace_file_create_request
from src.workspace_file_orchestration import (
    AutoWriteFilesResult,
    format_write_files_sse,
    parse_numbered_file_batch,
    run_auto_write_files_if_needed,
)

logger = logging.getLogger(__name__)

_MAX_PLANNER_FILES = 50
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.I)


def _planner_candidates(owner: Optional[str]) -> List[Tuple[str, str, Optional[Dict]]]:
    from src.endpoint_resolver import (
        resolve_chat_fallback_candidates,
        resolve_endpoint,
    )

    util_url, util_model, util_headers = resolve_endpoint("utility", owner=owner)
    default_url, default_model, default_headers = resolve_endpoint("default", owner=owner)
    seen = set()
    out: List[Tuple[str, str, Optional[Dict]]] = []
    for url, model, headers in (
        (util_url, util_model, util_headers),
        (default_url, default_model, default_headers),
    ):
        if url and model and (url, model) not in seen:
            seen.add((url, model))
            out.append((url, model, headers))
    for fb in resolve_chat_fallback_candidates(owner=owner) or []:
        key = (fb[0], fb[1])
        if key not in seen:
            seen.add(key)
            out.append(fb)
    return out


def _parse_planner_json(text: str) -> Optional[List[Tuple[str, str]]]:
    raw = (text or "").strip()
    if not raw:
        return None
    m = _JSON_BLOCK_RE.search(raw)
    if m:
        raw = m.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, list) or not files:
        return None
    specs: List[Tuple[str, str]] = []
    for item in files[:_MAX_PLANNER_FILES]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or item.get("file") or "").strip()
        if not path or ".." in path or path.startswith("/"):
            continue
        content = item.get("content")
        if content is None:
            content = item.get("body", "")
        specs.append((path, str(content)))
    return specs or None


async def plan_file_creation_specs(
    user_msg: str,
    *,
    owner: Optional[str] = None,
    timeout: int = 45,
) -> Optional[List[Tuple[str, str]]]:
    """Use a cheap LLM call to extract file paths + contents from natural language."""
    if not user_msg or not user_msg.strip():
        return None

    # Fast deterministic path first.
    batch = parse_numbered_file_batch(user_msg)
    if batch:
        return batch

    from src.intent_index import get_intent_index
    from src.llm_core import llm_call_async_with_fallback

    idx = get_intent_index()
    intent_ok = False
    if idx:
        match = idx.match(user_msg)
        intent_ok = match is not None and match.action == "create_files"
    if not intent_ok and not is_workspace_file_create_request(user_msg):
        return None

    candidates = _planner_candidates(owner)
    if not candidates:
        logger.info("[intent-planner] no LLM endpoint for file planner")
        return None

    system = (
        "You extract workspace file-creation tasks from user messages. "
        "Reply with ONLY valid JSON — no markdown outside a json code block, no commentary.\n"
        'Schema: {"files": [{"path": "relative/path.txt", "content": "exact file body"}, ...]}\n'
        "Rules:\n"
        "- paths are relative to the workspace root (no leading /, no ..)\n"
        "- list EVERY file explicitly (up to 50)\n"
        "- content is the exact bytes/text to write (numbers as strings are fine)\n"
        "- infer patterns (counting up/down, numbered names) from natural language\n"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg.strip()},
    ]
    try:
        raw = await llm_call_async_with_fallback(
            candidates,
            messages,
            temperature=0.1,
            max_tokens=2048,
            timeout=timeout,
        )
    except Exception as e:
        logger.warning("[intent-planner] LLM call failed: %s", e)
        return None

    specs = _parse_planner_json(raw)
    if specs:
        logger.info("[intent-planner] planned %d file(s) via LLM", len(specs))
    return specs


async def run_planned_write_files_if_needed(
    *,
    user_msg: str,
    workspace: Optional[str],
    approved_plan: str = "",
    session_id: Optional[str],
    owner: Optional[str],
) -> Optional[AutoWriteFilesResult]:
    """Regex batch, then LLM planner, then write_file execution."""
    if not session_id or not workspace:
        return None

    # Reuse regex fast-path from workspace_file_orchestration.
    fast = await run_auto_write_files_if_needed(
        user_msg=user_msg,
        workspace=workspace,
        approved_plan=approved_plan,
        session_id=session_id,
        owner=owner,
    )
    if fast:
        return fast

    specs = await plan_file_creation_specs(user_msg, owner=owner)
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


__all__ = [
    "plan_file_creation_specs",
    "run_planned_write_files_if_needed",
    "format_write_files_sse",
]
