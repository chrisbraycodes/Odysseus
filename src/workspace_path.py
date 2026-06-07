"""Resolve workspace folder paths for Docker Desktop bind mounts."""

from __future__ import annotations

import os
import re
from typing import Optional, Tuple

_DOCKER_WS = "/workspace"

# C:\Users\<name>\Desktop\foo  or  C:/Users/.../Desktop/foo
_WIN_DESKTOP_RE = re.compile(
    r"(?i)^[a-z]:[/\\]Users[/\\][^/\\]+[/\\]Desktop[/\\]?(.*)$"
)


def docker_workspace_available() -> bool:
    return os.path.isdir(_DOCKER_WS)


def resolve_workspace_path(raw: Optional[str]) -> str:
    """Return a real directory path inside the container, or '' if invalid.

    Accepts POSIX paths under /workspace, container paths, and Windows Desktop
    paths that map onto the Desktop bind mount.
    """
    if not raw or not str(raw).strip():
        return ""

    text = str(raw).strip()
    candidates = [text, os.path.expanduser(text)]

    win = _WIN_DESKTOP_RE.match(text.replace("/", os.sep))
    if win and docker_workspace_available():
        tail = (win.group(1) or "").replace("\\", "/").strip("/")
        candidates.append(os.path.join(_DOCKER_WS, tail) if tail else _DOCKER_WS)

    seen = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        try:
            real = os.path.realpath(cand)
        except OSError:
            continue
        if os.path.isdir(real):
            return real
    return ""


def display_workspace_path(resolved: str) -> str:
    """UI-friendly path — in Docker, always show the in-container /workspace path."""
    if not resolved:
        return ""
    text = resolved.replace("\\", "/")
    if docker_workspace_available():
        try:
            root = os.path.realpath(_DOCKER_WS)
            real = os.path.realpath(resolved)
            rel = os.path.relpath(real, root)
            if rel == ".":
                return _DOCKER_WS
            return f"{_DOCKER_WS}/{rel.replace(os.sep, '/')}"
        except (OSError, ValueError):
            pass
    return text


def path_under_workspace_root(root: str, target: str) -> bool:
    """True when ``target`` resolves to a path inside ``root``."""
    try:
        root_real = os.path.realpath(root)
        target_real = os.path.realpath(target)
        common = os.path.commonpath([root_real, target_real])
        return os.path.normcase(common) == os.path.normcase(root_real)
    except (OSError, ValueError):
        return False


def validate_workspace_submission(raw: Optional[str]) -> Tuple[bool, str, Optional[str]]:
    """(valid, resolved_path, normalized_from)."""
    if not raw or not str(raw).strip():
        return False, "", None
    resolved = resolve_workspace_path(raw)
    if not resolved:
        return False, "", None
    original = str(raw).strip()
    if os.path.normcase(resolved) != os.path.normcase(os.path.realpath(os.path.expanduser(original))):
        return True, resolved, original
    return True, resolved, None
