"""Auto-run trivial shell steps so weak models don't stall on pwd / npm start.

When the user sends a one-line shell command (``npm start``, ``pwd``) or an
approved plan's next step is ``pwd``, Odysseus executes bash directly and
returns tool events — no waiting for the LLM to emit a ```bash block.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from src.agent_tools import ToolBlock
from src.direct_shell import is_direct_shell_command

# Imperative shell verbs we can run without model cooperation.
_AUTO_SHELL_RE = re.compile(
    r"^\s*(?:"
    r"pwd\b"
    r"|npm\s+(?:start|run\s+\S+)\b"
    r"|npx\s+\S+"
    r"|yarn\s+(?:start|dev)\b"
    r"|pnpm\s+(?:start|dev|run\s+\S+)\b"
    r"|vite(?:\s|$)"
    r")\s*$",
    re.I,
)

# Pull a shell command out of noisy user text ("this is batman run npm start").
_EMBEDDED_SHELL_RE = re.compile(
    r"\b("
    r"pwd"
    r"|npm\s+start"
    r"|npm\s+run\s+(?:dev|start|serve)"
    r"|yarn\s+(?:start|dev)"
    r"|pnpm\s+(?:start|dev)"
    r"|vite(?:\s+--host[^\s;|&]*)?"
    r")\b",
    re.I,
)

_PLAN_PWD_RE = re.compile(r"\b(?:verify|check|run|use)?\s*`?pwd`?\b", re.I)
_PLAN_CD_RE = re.compile(r"\bcd\s+([^\s`'\]]+)", re.I)
_PLAN_NPM_START_RE = re.compile(r"\bnpm\s+start\b", re.I)


@dataclass
class AutoShellResult:
    command: str
    desc: str
    result: Dict[str, Any]
    workspace: Optional[str]
    skip_llm: bool
    assistant_message: str


def resolve_effective_workspace(
    workspace: Optional[str],
    user_msg: str = "",
    approved_plan: str = "",
) -> Optional[str]:
    """Pick a project subfolder when the user/plan names one (e.g. batman/)."""
    if not workspace:
        return workspace
    base = os.path.realpath(workspace)
    blob = f"{user_msg}\n{approved_plan}".lower()

    # Explicit cd target in plan or message.
    for m in _PLAN_CD_RE.finditer(f"{user_msg}\n{approved_plan}"):
        name = m.group(1).strip().strip("'\"")
        if name in (".", ".."):
            continue
        candidate = os.path.realpath(os.path.join(base, name))
        if candidate.startswith(base) and os.path.isdir(candidate):
            if os.path.isfile(os.path.join(candidate, "package.json")):
                return candidate

    # Named subfolder heuristic (batman, my-app, …).
    for token in re.findall(r"[/\\]?([a-zA-Z0-9][\w-]{0,40})\b", blob):
        if token.lower() in ("workspace", "test", "the", "run", "this", "is", "npm", "start"):
            continue
        candidate = os.path.realpath(os.path.join(base, token))
        if candidate != base and os.path.isdir(candidate):
            if os.path.isfile(os.path.join(candidate, "package.json")):
                return candidate
    return base


def extract_shell_command(text: str) -> Optional[str]:
    """Return a single shell command from the user's message, if any."""
    if not text or not str(text).strip():
        return None
    raw = str(text).strip()
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if len(lines) == 1:
        if is_direct_shell_command(raw) or _AUTO_SHELL_RE.match(raw):
            return lines[0]
    m = _EMBEDDED_SHELL_RE.search(raw)
    if m:
        return m.group(1).strip()
    return None


def cwd_system_note(workspace: Optional[str]) -> str:
    if not workspace:
        return ""
    return (
        f"## SHELL CWD (automatic — do NOT run pwd)\n"
        f"Verified cwd for bash/python: `{workspace}`\n"
        f"Every shell command already runs here. Plan steps that say `pwd` or "
        f"`cd` into this project folder are already satisfied — mark them `- [x]` "
        f"via `update_plan` and continue to the next step."
    )


def plan_auto_notes(approved_plan: str, workspace: Optional[str]) -> str:
    if not approved_plan or not workspace:
        return ""
    notes: List[str] = []
    if _PLAN_PWD_RE.search(approved_plan):
        notes.append(
            f"The plan's `pwd` step is already done (cwd=`{workspace}`). "
            f"Do not run pwd — tick that step with `update_plan` and move on."
        )
    return " ".join(notes)


def detect_auto_shell_command(
    user_msg: str,
    approved_plan: str = "",
) -> Optional[str]:
    cmd = extract_shell_command(user_msg)
    if cmd:
        return cmd
    # Plan-driven: next unchecked npm start when user message is empty/generic.
    if approved_plan and not user_msg.strip():
        if _PLAN_NPM_START_RE.search(approved_plan):
            return "npm start"
    return None


async def run_auto_shell_if_needed(
    *,
    user_msg: str,
    workspace: Optional[str],
    approved_plan: str = "",
    session_id: Optional[str],
    owner: Optional[str],
) -> Optional[AutoShellResult]:
    """Execute a direct shell command without waiting for the model."""
    if not session_id:
        return None
    cmd = detect_auto_shell_command(user_msg, approved_plan)
    if not cmd:
        return None

    effective_ws = resolve_effective_workspace(workspace, user_msg, approved_plan)

    from src.tool_execution import execute_tool_block

    if cmd.strip().lower() == "pwd":
        path = effective_ws or os.getcwd()
        result = {"output": path, "exit_code": 0}
        return AutoShellResult(
            command="pwd",
            desc="bash (auto): pwd",
            result=result,
            workspace=effective_ws,
            skip_llm=True,
            assistant_message=f"Current directory: `{path}`",
        )

    block = ToolBlock("bash", cmd)
    desc, result = await execute_tool_block(
        block,
        session_id=session_id,
        owner=owner,
        workspace=effective_ws,
    )
    preview = result.get("dev_preview_url") or ""
    output = (result.get("output") or result.get("error") or "").strip()
    msg_parts = [f"Ran `{cmd}`."]
    if preview:
        msg_parts.append(f"Preview (new browser tab when ready): {preview}")
    elif output and output != "(no output)":
        msg_parts.append(output[:500])
    assistant = " ".join(msg_parts)

    return AutoShellResult(
        command=cmd,
        desc=f"bash (auto): {desc}",
        result=result,
        workspace=effective_ws,
        skip_llm=True,
        assistant_message=assistant,
    )


def format_tool_sse(
    command: str,
    desc: str,
    result: Dict[str, Any],
    *,
    round_num: int = 0,
) -> Tuple[str, str, Dict[str, Any]]:
    """Build tool_start + tool_output SSE payloads and a tool_events record."""
    output_text = result.get("output") or result.get("error") or "(no output)"
    start = {
        "type": "tool_start",
        "tool": "bash",
        "command": command[:200],
        "round": round_num,
    }
    out = {
        "type": "tool_output",
        "tool": "bash",
        "command": command[:200],
        "output": output_text,
        "exit_code": result.get("exit_code", 0),
    }
    event = {
        "tool": "bash",
        "command": command[:200],
        "output": output_text,
        "exit_code": result.get("exit_code", 0),
        "description": desc,
    }
    return (
        f"data: {json.dumps(start)}\n\n",
        f"data: {json.dumps(out)}\n\n",
        event,
    )
