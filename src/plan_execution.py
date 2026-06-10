"""Approved-plan parsing, shell-step auto-run, and completion verification.

Weak local models often claim they ran shell commands without emitting ```bash
blocks. This module extracts the next plan step, verifies ticked steps against
disk, and recovers commands from false-completion prose.
"""

from __future__ import annotations

import os
import re
from typing import Optional, Tuple

_UNCHECKED_STEP = re.compile(r"^-\s*\[\s*\]\s*(.+)$", re.MULTILINE)
_CHECKED_STEP = re.compile(r"^-\s*\[\s*[xX]\s*\]\s*(.+)$", re.MULTILINE)
_BACKTICK = re.compile(r"`([^`]+)`")
_SHELL_CMD = re.compile(
    r"\b("
    r"npx\s+create-react-app\s+\S+"
    r"|npx\s+\S+"
    r"|npm\s+(?:install|ci|start|run\s+\S+|init|exec\s+\S+)"
    r"|yarn\s+(?:add|install|start|dev|run\s+\S+)"
    r"|pnpm\s+(?:add|install|start|dev|run\s+\S+)"
    r"|cd\s+\S+"
    r")\b",
    re.I,
)
_CRA_RE = re.compile(r"\bcreate-react-app\s+([^\s`'\]]+)", re.I)
_CD_RE = re.compile(r"\bcd\s+([^\s;&|]+)", re.I)
_FALSE_DONE_RE = re.compile(
    r"\b(?:ran|executed|have run|successfully run|completed running|started|"
    r"scaffolded|created)\s+(?:the\s+)?(?:command\s+)?[`'\"]?"
    r"(?:npx\s+)?(?:create-react-app\s+\S+|npm\s+\S+|npx\s+\S+)",
    re.I,
)
_SKIP_STEP = re.compile(
    r"^(?:verify|check|confirm)?\s*(?:that\s+)?(?:the\s+)?"
    r"(?:cwd|current directory|pwd|location|workspace)\b",
    re.I,
)


def _looks_executable(cmd: str) -> bool:
    cmd = (cmd or "").strip()
    if not cmd or len(cmd) > 500:
        return False
    return bool(_SHELL_CMD.search(cmd) or _CD_RE.match(cmd))


def _cmd_from_step_text(step: str) -> Optional[str]:
    step = (step or "").strip()
    if not step or _SKIP_STEP.search(step):
        return None
    bt = _BACKTICK.search(step)
    if bt:
        cmd = bt.group(1).strip()
        if _looks_executable(cmd):
            return cmd
    m = _SHELL_CMD.search(step)
    if m:
        return m.group(1).strip()
    return None


def extract_next_plan_shell_command(plan: str) -> Optional[str]:
    """First unchecked plan step that contains a runnable shell command."""
    if not plan or not plan.strip():
        return None
    for m in _UNCHECKED_STEP.finditer(plan):
        cmd = _cmd_from_step_text(m.group(1))
        if cmd:
            return cmd
    return None


def detect_false_completion_command(prose: str, approved_plan: str = "") -> Optional[str]:
    """Recover a shell command when the model claims it ran one without tools."""
    text = (prose or "").strip()
    if not text or not _FALSE_DONE_RE.search(text):
        return None
    bt = _BACKTICK.search(text)
    if bt:
        cmd = bt.group(1).strip()
        if _looks_executable(cmd):
            return cmd
    m = _SHELL_CMD.search(text)
    if m:
        return m.group(1).strip()
    return extract_next_plan_shell_command(approved_plan)


def verify_checked_step(step_text: str, workspace: Optional[str]) -> Tuple[bool, str]:
    """Return (ok, error_message) for a plan step marked done."""
    step = (step_text or "").strip()
    if not step:
        return True, ""

    from src.tool_execution import _default_agent_cwd

    base = os.path.realpath(workspace or _default_agent_cwd())

    cra = _CRA_RE.search(step)
    if cra:
        name = cra.group(1).strip("'\"`,.")
        proj = os.path.join(base, name)
        pkg = os.path.join(proj, "package.json")
        if os.path.isfile(pkg):
            return True, ""
        return False, (
            f"Cannot mark create-react-app step done: `{proj}` does not exist "
            f"(no package.json). Run the scaffold command first."
        )

    cd = _CD_RE.search(step)
    if cd and step.lower().startswith("cd"):
        name = cd.group(1).strip("'\"`.")
        if name in (".", ".."):
            return True, ""
        target = os.path.realpath(os.path.join(base, name))
        if not target.startswith(base):
            return False, f"cd target `{name}` is outside the workspace."
        if os.path.isdir(target):
            return True, ""
        return False, f"Cannot mark cd step done: `{target}` does not exist yet."

    return True, ""


def verify_plan_checkmarks(plan: str, workspace: Optional[str]) -> Tuple[bool, str]:
    """Verify every `- [x]` step in a plan is backed by filesystem state."""
    if not plan or not plan.strip():
        return True, ""
    for m in _CHECKED_STEP.finditer(plan):
        ok, err = verify_checked_step(m.group(1), workspace)
        if not ok:
            return False, err
    return True, ""


def resolve_scaffold_cwd(workspace: Optional[str], proposed_cwd: str) -> str:
    """Keep Node scaffolds off odysseus/data/ when /workspace is mounted."""
    from src.constants import DATA_DIR

    cwd = os.path.realpath(proposed_cwd or "")
    data_root = os.path.realpath(DATA_DIR)
    if os.path.isdir("/workspace") and (cwd == data_root or cwd.startswith(data_root + os.sep)):
        return "/workspace"
    return proposed_cwd
