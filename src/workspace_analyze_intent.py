"""Detect natural-language workspace analysis / project review requests."""

from __future__ import annotations

import re
from typing import Optional

_ANALYZE_VERB = r"(?:analyze|analyse|review|explore|inspect|audit|summarize|summarise|explain|describe|understand|tell\s+me\s+about)"
_TARGET = r"(?:this\s+)?(?:project|codebase|workspace|repo(?:sitory)?|app(?:lication)?|website|site|code|folder|directory|whole\s+workspace)"

_ANALYZE_TARGET = re.compile(
    rf"\b{_ANALYZE_VERB}\b.{{0,80}}\b{_TARGET}\b|\b{_TARGET}\b.{{0,80}}\b{_ANALYZE_VERB}\b",
    re.I,
)
_WHAT_IS_ABOUT = re.compile(
    r"\bwhat\s+(?:is|does)\s+(?:this\s+)?(?:project|website|app|codebase|repo|site)\b",
    re.I,
)
_STACK_QUESTION = re.compile(
    r"\bwhat\s+(?:tools|tech(?:nologies)?|stack|frameworks?)\b.{{0,60}}\b(?:used|built|make|making)\b",
    re.I,
)
_WHOLE_WORKSPACE = re.compile(
    r"\b(?:analyze|analyse|review|explore|scan|summarize|summarise)\b.{{0,40}}\b(?:whole\s+)?workspace\b",
    re.I,
)


def is_workspace_analyze_request(text: str) -> bool:
    """True when the user wants the agent to inspect/read the active workspace."""
    if not text or len(text) > 4000:
        return False
    t = text.strip()
    if not t:
        return False
    if _ANALYZE_TARGET.search(t):
        return True
    if _WHAT_IS_ABOUT.search(t):
        return True
    if _STACK_QUESTION.search(t):
        return True
    if _WHOLE_WORKSPACE.search(t):
        return True
    return False


def workspace_analyze_system_note(text: str) -> Optional[str]:
    """Pin read/explore tools for project-analysis phrasing."""
    if not is_workspace_analyze_request(text):
        return None
    snippet = text.strip().replace("\n", " ")[:400]
    return (
        "## EXECUTE NOW — workspace analysis\n"
        f'User request: "{snippet}"\n'
        "You HAVE direct access to the active workspace via tools. "
        "Do NOT say you lack filesystem access, simulate `ls`, or invent sample files.\n"
        "If a workspace scan was injected above, use it as ground truth.\n"
        "Otherwise your FIRST actions must be:\n"
        "1. Use the WORKSPACE SCAN / indexed search injected above if present\n"
        "2. ```ls``` (or ```bash\\nls -la\\n```) on the workspace root\n"
        "3. ```workspace_search``` for architecture/flow questions on large repos\n"
        "4. ```read_file``` on README*, package.json, pyproject.toml, and entry files\n"
        "5. ```grep``` or ```glob``` for targeted symbol/route lookup\n"
        "Then answer: what the project/website is, and what tools/stack it uses — "
        "based ONLY on files you actually read."
    )
