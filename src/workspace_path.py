"""Resolve workspace folder paths for Docker bind mounts (host ↔ container)."""

from __future__ import annotations

import os
import re
from typing import Optional, Tuple

_DOCKER_WS = "/workspace"

# Legacy: C:\Users\<name>\Desktop\foo when WORKSPACE_HOST_ROOT is unset.
_WIN_DESKTOP_RE = re.compile(
    r"(?i)^[a-z]:[/\\]Users[/\\][^/\\]+[/\\]Desktop[/\\]?(.*)$"
)


def docker_workspace_available() -> bool:
    return os.path.isdir(_DOCKER_WS)


def workspace_host_root() -> str:
    """Host path configured for the /workspace bind mount (label only in Docker)."""
    return (os.environ.get("WORKSPACE_HOST_ROOT") or "").strip()


def _norm_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path.replace("/", os.sep)))


def host_path_to_container(host_path: str) -> Optional[str]:
    """Map a host filesystem path to its in-container bind-mount path."""
    root = workspace_host_root()
    if not root or not docker_workspace_available():
        return None
    text = (host_path or "").strip()
    if not text:
        return _DOCKER_WS
    try:
        rel = os.path.relpath(text, root)
    except ValueError:
        return None
    if rel.startswith(".."):
        return None
    if rel in (".", ""):
        return _DOCKER_WS
    rel_posix = rel.replace("\\", "/")
    return f"{_DOCKER_WS}/{rel_posix}" if rel_posix else _DOCKER_WS


def container_path_to_host(container_path: str) -> Optional[str]:
    """Map an in-container /workspace path to the host bind-mount path."""
    root = workspace_host_root()
    if not root or not docker_workspace_available():
        return None
    text = (container_path or "").strip()
    if not text:
        return root
    try:
        ws_real = os.path.realpath(_DOCKER_WS)
        target_real = os.path.realpath(text)
        rel = os.path.relpath(target_real, ws_real)
    except (OSError, ValueError):
        return None
    if rel.startswith(".."):
        return None
    if rel in (".", ""):
        return root
    return os.path.join(root, rel.replace("/", os.sep))


def workspace_sync_info(resolved: str = "") -> dict:
    """Metadata for the UI: bind-mount sync and path labels."""
    in_docker = docker_workspace_available()
    host_root = workspace_host_root()
    info = {
        "docker_workspace": in_docker,
        "container_root": _DOCKER_WS if in_docker else "",
        "host_root": host_root if in_docker else "",
        "sync_mode": "bind_mount" if in_docker and host_root else "",
        "dev_exec": (os.environ.get("WORKSPACE_DEV_EXEC") or ("host" if in_docker else "local")).strip().lower(),
    }
    if resolved and in_docker:
        info["host_path"] = container_path_to_host(resolved) or ""
        info["container_path"] = display_workspace_path(resolved)
    return info


def resolve_workspace_path(raw: Optional[str]) -> str:
    """Return a real directory path inside the runtime, or '' if invalid.

    Accepts container paths under /workspace, host paths under WORKSPACE_HOST_ROOT,
    and legacy Windows Desktop paths when the mount is Desktop.
    """
    if not raw or not str(raw).strip():
        return ""

    text = str(raw).strip()
    candidates = [text, os.path.expanduser(text)]

    mapped = host_path_to_container(text)
    if mapped:
        candidates.insert(0, mapped)

    win = _WIN_DESKTOP_RE.match(text.replace("/", os.sep))
    if win and docker_workspace_available() and not workspace_host_root():
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
    """UI-friendly container path when running in Docker."""
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


def display_workspace_paths(resolved: str) -> Tuple[str, str]:
    """Return (container_display, host_display) for UI labels."""
    container = display_workspace_path(resolved)
    host = container_path_to_host(resolved) or ""
    if host:
        host = host.replace("/", os.sep)
    return container, host


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
    try:
        original_real = os.path.realpath(os.path.expanduser(original))
    except OSError:
        original_real = original
    if os.path.normcase(resolved) != os.path.normcase(original_real):
        return True, resolved, original
    return True, resolved, None
