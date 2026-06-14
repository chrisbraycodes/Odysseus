"""Persist user consent for the Windows host terminal bridge."""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, Optional

from core.constants import DATA_DIR

_CONSENT_PATH = os.path.join(DATA_DIR, "host_terminal_consent.json")
_LOCK = threading.Lock()


_VALID_SHELLS = frozenset({"powershell", "cmd"})


def normalize_host_shell(shell: Optional[str]) -> str:
    text = (shell or "").strip().lower()
    if text in ("cmd", "cmd.exe", "comspec"):
        return "cmd"
    return "powershell"


def _default() -> Dict[str, Any]:
    return {
        "accepted": False,
        "unrestricted": False,
        "accepted_at": "",
        "workspace_path": "",
        "shell": "powershell",
    }


def load_consent() -> Dict[str, Any]:
    with _LOCK:
        if not os.path.isfile(_CONSENT_PATH):
            return _default()
        try:
            with open(_CONSENT_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return _default()
            out = _default()
            out.update(data)
            out["accepted"] = bool(out.get("accepted"))
            out["unrestricted"] = bool(out.get("unrestricted"))
            out["workspace_path"] = str(out.get("workspace_path") or "")
            out["accepted_at"] = str(out.get("accepted_at") or "")
            out["shell"] = normalize_host_shell(str(out.get("shell") or "powershell"))
            return out
        except (OSError, json.JSONDecodeError):
            return _default()


def save_consent(
    *,
    accepted: bool,
    unrestricted: bool = False,
    workspace_path: str = "",
    shell: str = "powershell",
) -> Dict[str, Any]:
    prev = load_consent() if accepted else _default()
    data = {
        "accepted": bool(accepted),
        "unrestricted": bool(unrestricted) if accepted else False,
        "accepted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if accepted else "",
        "workspace_path": (workspace_path or "").strip() if accepted else "",
        "shell": normalize_host_shell(shell if accepted else prev.get("shell")),
    }
    os.makedirs(os.path.dirname(_CONSENT_PATH), exist_ok=True)
    with _LOCK:
        with open(_CONSENT_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    return data


def clear_consent() -> Dict[str, Any]:
    return save_consent(accepted=False, unrestricted=False, workspace_path="")


def host_terminal_enabled(workspace: Optional[str] = None) -> bool:
    data = load_consent()
    if not data.get("accepted"):
        return False
    if workspace:
        saved = (data.get("workspace_path") or "").strip()
        if saved and not _workspace_paths_match(saved, workspace.strip()):
            return False
    return True


def _norm_workspace_key(path: str) -> str:
    text = (path or "").strip().replace("\\", "/")
    if not text:
        return ""
    if text.startswith("/workspace"):
        return text.rstrip("/") or "/workspace"
    try:
        return os.path.normcase(os.path.normpath(text))
    except (TypeError, ValueError):
        return text


def _workspace_paths_match(saved: str, current: str) -> bool:
    a = _norm_workspace_key(saved)
    b = _norm_workspace_key(current)
    if not a or not b:
        return True
    return a == b


def host_terminal_unrestricted() -> bool:
    data = load_consent()
    return bool(data.get("accepted") and data.get("unrestricted"))


def host_terminal_shell() -> str:
    data = load_consent()
    if not data.get("accepted"):
        return "powershell"
    return normalize_host_shell(str(data.get("shell") or "powershell"))
