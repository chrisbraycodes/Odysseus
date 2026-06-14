"""HTTP/WebSocket client for the Windows host agent (runs on the Docker host)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

import httpx

from src.host_agent_paths import path_under_root
from src.host_terminal_consent import host_terminal_enabled, host_terminal_shell, host_terminal_unrestricted
from src.workspace_dev import dev_exec_target, docker_workspace_mounted
from src.workspace_path import container_path_to_host

logger = logging.getLogger(__name__)

_DEFAULT_HTTP = "http://host.docker.internal:17789"
_DEFAULT_WS = "ws://host.docker.internal:17790"


def host_agent_http_url() -> str:
    return (
        os.environ.get("WORKSPACE_HOST_AGENT_URL")
        or os.environ.get("WORKSPACE_HOST_AGENT_HTTP")
        or _DEFAULT_HTTP
    ).rstrip("/")


def host_agent_ws_url() -> str:
    return (
        os.environ.get("WORKSPACE_HOST_AGENT_WS")
        or os.environ.get("WORKSPACE_HOST_AGENT_URL", "").replace("http://", "ws://").replace("https://", "wss://")
        or _DEFAULT_WS
    ).rstrip("/")


def host_agent_token() -> str:
    return (os.environ.get("WORKSPACE_HOST_AGENT_TOKEN") or "").strip()


def host_agent_configured() -> bool:
    return bool(host_agent_token()) and dev_exec_target() == "host" and docker_workspace_mounted()


def host_agent_ready(workspace: Optional[str] = None) -> bool:
    if not host_agent_configured() or not host_terminal_enabled(workspace):
        return False
    try:
        status = host_agent_status_sync()
        return bool(status.get("ok"))
    except Exception:
        return False


def host_path_for_agent(host_path: str) -> str:
    """Normalize a Windows host path for the host agent API.

    Never call ``realpath`` on ``C:...`` paths inside the Linux container —
    POSIX treats them as relative and produces ``/app/C:...``.
    """
    return (host_path or "").strip().replace("\\", "/")


def workspace_host_paths(workspace: str) -> Tuple[str, str]:
    """Return (container_workspace, host_workspace paths)."""
    host_root = container_path_to_host(workspace) or ""
    if not host_root:
        raise ValueError("workspace has no host bind-mount path")
    return workspace, host_path_for_agent(host_root)


def _auth_headers() -> Dict[str, str]:
    token = host_agent_token()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


async def host_agent_status() -> Dict[str, Any]:
    url = f"{host_agent_http_url()}/health"
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            resp = await client.get(url, headers=_auth_headers())
            if resp.status_code >= 400:
                return {"ok": False, "error": resp.text[:200]}
            data = resp.json() if resp.content else {}
            data["ok"] = True
            return data
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def host_agent_status_sync() -> Dict[str, Any]:
    url = f"{host_agent_http_url()}/health"
    try:
        with httpx.Client(timeout=2.5) as client:
            resp = client.get(url, headers=_auth_headers())
            if resp.status_code >= 400:
                return {"ok": False, "error": resp.text[:200]}
            data = resp.json() if resp.content else {}
            data["ok"] = True
            return data
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def host_agent_exec(
    *,
    workspace: str,
    command: str,
    cwd: Optional[str] = None,
    background: bool = False,
    timeout: float = 600.0,
) -> Dict[str, Any]:
    if not host_agent_ready(workspace):
        return {"error": "Windows host agent is not enabled or reachable", "exit_code": 1}
    container_ws, host_ws = workspace_host_paths(workspace)
    host_cwd = host_path_for_agent(container_path_to_host(cwd or workspace) or host_ws)
    unrestricted = host_terminal_unrestricted()
    if not unrestricted and not path_under_root(host_ws, host_cwd):
        return {
            "error": "command cwd must stay inside the workspace folder (enable unrestricted host access to override)",
            "exit_code": 1,
        }
    payload = {
        "workspace_root": host_ws,
        "cwd": host_cwd,
        "command": command,
        "background": background,
        "unrestricted": unrestricted,
    }
    url = f"{host_agent_http_url()}/v1/exec"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=_auth_headers())
            if resp.status_code >= 400:
                detail = resp.text[:500]
                try:
                    detail = resp.json().get("detail") or detail
                except Exception:
                    pass
                return {"error": str(detail), "exit_code": 1}
            return resp.json()
    except Exception as exc:
        return {"error": str(exc), "exit_code": 1}


def host_terminal_ws_connect_url(
    *,
    workspace: str,
    cols: int,
    rows: int,
    session: str = "",
    shell: str = "",
) -> str:
    if not host_agent_ready(workspace):
        raise RuntimeError("Windows host terminal is not enabled")
    _, host_ws = workspace_host_paths(workspace)
    unrestricted = host_terminal_unrestricted()
    use_shell = (shell or "").strip() or host_terminal_shell()
    token = quote(host_agent_token(), safe="")
    params = [
        f"workspace_root={quote(host_ws, safe='')}",
        f"cwd={quote(host_ws, safe='')}",
        f"cols={int(cols)}",
        f"rows={int(rows)}",
        f"token={token}",
        f"unrestricted={'1' if unrestricted else '0'}",
        f"shell={quote(use_shell, safe='')}",
    ]
    if session:
        params.append(f"session={quote(session, safe='')}")
    return f"{host_agent_ws_url()}/v1/terminal?{'&'.join(params)}"


def validate_host_write_path(workspace: str, raw_path: str) -> None:
    """Reject file writes outside workspace unless unrestricted host access is on."""
    if host_terminal_unrestricted():
        return
    base = os.path.realpath(workspace)
    expanded = os.path.expanduser(str(raw_path).strip())
    candidate = expanded if os.path.isabs(expanded) else os.path.join(base, expanded)
    resolved = os.path.realpath(candidate)
    if not path_under_root(base, resolved):
        raise ValueError(
            "path is outside the active workspace — enable unrestricted host access "
            "in the Windows host terminal settings to edit files elsewhere"
        )
