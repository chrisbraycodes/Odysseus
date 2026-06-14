"""Path containment helpers for the Windows host agent bridge."""

from __future__ import annotations

import os


def _is_windows_host_path(path: str) -> bool:
    text = (path or "").strip()
    return len(text) >= 2 and text[1] == ":" and text[0].isalpha()


def norm_path(path: str) -> str:
    if _is_windows_host_path(path):
        return path.replace("\\", "/").rstrip("/").lower()
    return os.path.normcase(os.path.normpath(path.replace("/", os.sep)))


def path_under_root(root: str, target: str) -> bool:
    """True when ``target`` resolves to a path inside ``root``."""
    if not root or not target:
        return False
    if _is_windows_host_path(root) or _is_windows_host_path(target):
        root_norm = norm_path(root)
        target_norm = norm_path(target)
        return target_norm == root_norm or target_norm.startswith(root_norm + "/")
    try:
        root_real = norm_path(os.path.realpath(root))
        target_real = norm_path(os.path.realpath(target))
    except OSError:
        return False
    if root_real == target_real:
        return True
    try:
        common = os.path.commonpath([root_real, target_real])
    except ValueError:
        return False
    return common == root_real


def resolve_under_root(root: str, target: str) -> str:
    """Resolve ``target`` and ensure it stays under ``root``."""
    if _is_windows_host_path(root):
        root_norm = root.replace("\\", "/").rstrip("/")
        text = (target or "").strip().replace("\\", "/") or root_norm
        if _is_windows_host_path(text):
            candidate = text
        elif text.startswith("/"):
            raise ValueError(f"path is outside workspace root: {target}")
        else:
            candidate = f"{root_norm}/{text.lstrip('/')}"
        if not path_under_root(root_norm, candidate):
            raise ValueError(f"path is outside workspace root: {target}")
        return candidate
    root_real = os.path.realpath(root)
    text = (target or "").strip() or root_real
    candidate = text if os.path.isabs(text) else os.path.join(root_real, text)
    resolved = os.path.realpath(candidate)
    if not path_under_root(root_real, resolved):
        raise ValueError(f"path is outside workspace root: {text}")
    return resolved
