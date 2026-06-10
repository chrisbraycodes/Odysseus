"""Detect when the user message is itself a shell command to run."""

from __future__ import annotations

import re
from typing import List, Optional

# Single-line imperative commands the agent should execute, not explain.
_DIRECT_CMD = re.compile(
    r"^\s*(?:"
    r"npx|npm(?:\s+(?:run|install|ci|start|init|exec))?|yarn|pnpm|"
    r"node|python3?|pip3?|git|docker|curl|wget|make|cmake|cargo|go|"
    r"rustc|javac|java|gradle|mvn|"
    r"ls|mkdir|rm|cp|mv|cat|touch|chmod|chown|tar|unzip|pwd|"
    r"deploy|build|install|restart|reboot|kill|tail|grep|head|tail|find"
    r")\b",
    re.I,
)


def is_direct_shell_command(text: str) -> bool:
    """True when the user sent one line that looks like a shell command."""
    if not text or len(text) > 500:
        return False
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) != 1:
        return False
    if _DIRECT_CMD.match(lines[0]):
        return True
    # `create-react-app foo` without npx prefix (some users omit it).
    return bool(re.match(r"^\s*create-react-app\s+\S+", lines[0], re.I))


def direct_shell_system_note(text: str) -> Optional[str]:
    """System note forcing the model to run the user's command via bash."""
    if not is_direct_shell_command(text):
        return None
    cmd = text.strip().splitlines()
    cmd = next((ln.strip() for ln in cmd if ln.strip()), "")
    return (
        "## USER SENT A SHELL COMMAND\n"
        f"The user's message is a command to RUN, not a question to answer:\n"
        f"  {cmd}\n\n"
        "Your FIRST response must be ONE ```bash block containing EXACTLY that "
        "command (one line). No tutorial, no numbered steps, no 'you can run' "
        "prose. At most one short sentence after the block if needed."
    )


def last_user_message(messages: List[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str):
                return content
    return ""
