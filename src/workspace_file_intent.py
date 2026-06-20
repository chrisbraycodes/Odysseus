"""Detect natural-language workspace file-creation requests."""

from __future__ import annotations

import re
from typing import Optional

_CREATE_VERB = r"(?:make|create|write|save|add|generate|produce|output|put|dump|touch|build|new|spin\s+up|set\s+up|give\s+me)"
_FILE_NOUN = r"(?:txt|text\s+file|file|files|document|documents|doc|docs|log|note|notes|script|scripts)"

_FILE_CREATE = re.compile(
    rf"\b(?:{_CREATE_VERB})\s+(?:a\s+|an\s+|the\s+|some\s+|my\s+)?(?:\w+\s+){{0,5}}{_FILE_NOUN}\b",
    re.I,
)
_NAME_IT = re.compile(r"\bname\s+(?:it|them|each)\s+\S+", re.I)
_INCREMENT = re.compile(
    r"\b(?:increment(?:ing)?|count(?:ing)?)\s+(?:up\s+)?(?:to|through)\s+\d+\b",
    re.I,
)
_UP_TO = re.compile(r"\b(?:up\s+to|through|from)\s+\d+\b", re.I)


def is_workspace_file_create_request(text: str) -> bool:
    """True when the user wants files created in the workspace (not bash/make)."""
    if not text or len(text) > 2000:
        return False
    t = text.strip()
    if not t:
        return False
    if _FILE_CREATE.search(t):
        return True
    if _NAME_IT.search(t) and re.search(rf"\b{_FILE_NOUN}\b", t, re.I):
        return True
    if _INCREMENT.search(t) and re.search(rf"\b{_FILE_NOUN}\b", t, re.I):
        return True
    if _UP_TO.search(t) and _NAME_IT.search(t):
        return True
    try:
        from src.intent_index import semantic_action_match
        if semantic_action_match(t, "create_files"):
            return True
    except Exception:
        pass
    return False


def workspace_file_create_system_note(text: str) -> Optional[str]:
    """Pin write_file execution for English file-creation phrasing."""
    if not is_workspace_file_create_request(text):
        return None
    snippet = text.strip().replace("\n", " ")[:400]
    batch_hint = ""
    try:
        from src.workspace_file_orchestration import parse_numbered_file_batch
        batch = parse_numbered_file_batch(text)
        if batch:
            names = ", ".join(p for p, _ in batch[:4])
            if len(batch) > 4:
                names += f", … ({len(batch)} files)"
            batch_hint = (
                f"\nCreate exactly these files: {names}. "
                "Use one ```write_file``` block per file (path on line 1, content on line 2+).\n"
            )
    except Exception:
        pass
    return (
        "## USER WANTS WORKSPACE FILES CREATED\n"
        f'The user message is a file-creation task (NOT a shell command):\n'
        f'  "{snippet}"\n\n'
        "Use ```write_file``` for each file in the active workspace. "
        "Do NOT use bash, for-loops, touch, echo redirects, python -c, or GNU `make`.\n"
        f"{batch_hint}"
        "Example for one file:\n"
        "```write_file\n1.txt\n1\n```\n"
        "Execute all files now — no tutorial prose, no fake 'Step N: Created' claims."
    )
