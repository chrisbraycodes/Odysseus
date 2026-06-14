"""API routes for Windows host terminal consent and status."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.auth_helpers import get_current_user
from src.host_agent_client import (
    host_agent_configured,
    host_agent_ready,
    host_agent_status,
    host_agent_status_sync,
)
from src.host_terminal_consent import (
    clear_consent,
    host_terminal_enabled,
    host_terminal_shell,
    host_terminal_unrestricted,
    load_consent,
    normalize_host_shell,
    save_consent,
)
from src.tool_security import owner_is_admin_or_single_user
from src.workspace_path import resolve_workspace_path, workspace_sync_info


class HostTerminalConsentRequest(BaseModel):
    accepted: bool = Field(..., description="User accepted host terminal risks")
    unrestricted: bool = Field(
        default=False,
        description="Allow commands and file edits outside the workspace folder",
    )
    workspace: str = Field(default="", description="Active workspace path")
    shell: str = Field(
        default="powershell",
        description="Windows host shell: powershell or cmd",
    )


def _require_admin(request: Request) -> None:
    owner = get_current_user(request)
    if not owner_is_admin_or_single_user(owner):
        raise HTTPException(status_code=403, detail="Host terminal access is admin-only")


def setup_host_terminal_routes() -> APIRouter:
    router = APIRouter(tags=["host-terminal"])

    @router.get("/api/workspace/host-terminal/status")
    async def status(request: Request, workspace: str = ""):
        _require_admin(request)
        ws = (workspace or "").strip()
        resolved = resolve_workspace_path(ws) if ws else ""
        agent = await host_agent_status() if host_agent_configured() else {"ok": False}
        consent = load_consent()
        return {
            **workspace_sync_info(resolved),
            "host_agent_configured": host_agent_configured(),
            "host_agent_reachable": bool(agent.get("ok")),
            "host_agent_error": agent.get("error", ""),
            "host_terminal_enabled": host_terminal_enabled(resolved or None),
            "host_terminal_unrestricted": host_terminal_unrestricted(),
            "host_terminal_shell": host_terminal_shell(),
            "consent": consent,
            "workspace": resolved,
        }

    @router.post("/api/workspace/host-terminal/consent")
    async def set_consent(request: Request, body: HostTerminalConsentRequest):
        _require_admin(request)
        if not host_agent_configured():
            raise HTTPException(
                status_code=503,
                detail="Windows host agent is not configured (set WORKSPACE_HOST_AGENT_TOKEN)",
            )
        ws = resolve_workspace_path(body.workspace) if body.workspace else ""
        if body.accepted and not ws:
            raise HTTPException(status_code=400, detail="Select a workspace before enabling the host terminal")
        agent = host_agent_status_sync() if host_agent_configured() else {"ok": False}
        agent_reachable = bool(agent.get("ok"))
        agent_error = str(agent.get("error") or "")
        saved = save_consent(
            accepted=body.accepted,
            unrestricted=body.unrestricted if body.accepted else False,
            workspace_path=ws,
            shell=normalize_host_shell(body.shell),
        )
        return {
            "consent": saved,
            "host_terminal_enabled": host_terminal_enabled(ws or None),
            "host_terminal_unrestricted": host_terminal_unrestricted(),
            "host_terminal_shell": host_terminal_shell(),
            "host_agent_reachable": agent_reachable,
            "host_agent_error": agent_error,
            "warning": (
                ""
                if not body.accepted or agent_reachable
                else (
                    "Windows host terminal preference saved, but the host agent is not "
                    "running on your computer yet. Run start.bat again (or launch-docker.ps1) "
                    "and keep the Odysseus window open until you see "
                    "'Windows host agent is ready'."
                )
            ),
        }

    @router.delete("/api/workspace/host-terminal/consent")
    async def revoke_consent(request: Request):
        _require_admin(request)
        return {"consent": clear_consent()}

    return router
