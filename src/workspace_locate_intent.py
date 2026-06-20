"""Detect 'which file / where is' workspace code-location questions."""

from __future__ import annotations

import re
from typing import Optional

from src.workspace_analyze_intent import is_workspace_analyze_request

_WHICH_FILE = re.compile(
    r"\b(?:which|what)\s+file\b|\bfile\b.{0,40}\b(?:is\s+)?(?:that|this|responsible|handles?)\b",
    re.I,
)
_WHERE_CODE = re.compile(
    r"\bwhere\s+(?:is|does|would|can|should)\b.{0,80}\b(?:file|code|component|logic|handler|route|page)\b",
    re.I,
)
_FIND_FILE = re.compile(
    r"\b(?:find|locate|figure\s+out)\b.{0,80}\b(?:file|files|component|module)\b",
    re.I,
)
_READ_TO_FIND = re.compile(
    r"\bread\s+(?:each\s+)?(?:file|files)\b.{0,80}\b(?:figure|find|which|locate)\b",
    re.I,
)
_SITE_FEATURE = re.compile(
    r"\b(?:home\s*page|this\s+site|the\s+web\s*page|on\s+the\s+page|in\s+this\s+repo|listed\s+on)\b",
    re.I,
)
_CHANGE_FILE = re.compile(
    r"\bwhich\s+file\b.{0,60}\b(?:change|update|edit|modify)\b",
    re.I,
)
_NOT_GITHUB_README_FAQ = re.compile(
    r"\b(?:on\s+the\s+home\s+page|this\s+site|web\s*page|webpage|listed)\b",
    re.I,
)


def is_workspace_locate_request(text: str) -> bool:
    """True when the user wants to find which source file implements something."""
    if not text or len(text) > 4000:
        return False
    t = text.strip()
    if not t:
        return False
    # Broad project overview — scan path, not grep-first locate.
    if is_workspace_analyze_request(t) and not (
        _WHICH_FILE.search(t)
        or _WHERE_CODE.search(t)
        or _FIND_FILE.search(t)
        or _READ_TO_FIND.search(t)
        or _CHANGE_FILE.search(t)
    ):
        return False
    if _WHICH_FILE.search(t):
        return True
    if _WHERE_CODE.search(t):
        return True
    if _FIND_FILE.search(t):
        return True
    if _READ_TO_FIND.search(t):
        return True
    if _CHANGE_FILE.search(t):
        return True
    if _SITE_FEATURE.search(t) and re.search(r"\b(?:file|code|component|readme|github)\b", t, re.I):
        return True
    try:
        from src.intent_index import semantic_action_match
        if semantic_action_match(t, "locate_code"):
            return True
    except Exception:
        pass
    return False


def workspace_locate_system_note(text: str) -> Optional[str]:
    """Pin grep/read for code-location questions."""
    if not is_workspace_locate_request(text):
        return None
    snippet = text.strip().replace("\n", " ")[:400]
    site_hint = ""
    if _NOT_GITHUB_README_FAQ.search(text):
        site_hint = (
            "The user means a file in THIS project's source that renders content on "
            "the live website — NOT GitHub.com's README.md and NOT generic React "
            "`public/index.html` unless grep proves it.\n"
        )
    return (
        "## EXECUTE NOW — locate source file\n"
        f'User request: "{snippet}"\n'
        f"{site_hint}"
        "You HAVE workspace tools. Do NOT write `cat path` or `Let's open` in prose — "
        "those do NOT run. Use real tool blocks only.\n"
        "If CODE SEARCH results were injected above, answer from those paths and "
        "optional ```read_file``` on the top hits — do NOT guess file names.\n"
        "Odysseus may already have run grep + file scan and synthesized an answer — "
        "if so, present that result; do not claim you lack filesystem access.\n"
        "Otherwise FIRST:\n"
        "1. ```grep``` with patterns from the question (e.g. github, readme, "
        "raw.githubusercontent, api.github.com, home, landing)\n"
        "2. ```read_file``` on the 1–2 best matching source files (skip node_modules)\n"
        "3. Answer: exact relative path(s) and what each does — cite grep line evidence."
    )
