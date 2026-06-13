"""Workspace terminal — bidirectional WebSocket PTY."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from src.terminal_manager import (
    pty_available,
    pty_unavailable_reason,
    session_manager,
)
from src.tool_security import owner_is_admin_or_single_user
from src.workspace_path import resolve_workspace_path

logger = logging.getLogger(__name__)


def _resolve_terminal_cwd(workspace: str) -> str:
    raw = (workspace or "").strip()
    if not raw:
        raise ValueError("workspace is required")
    resolved = resolve_workspace_path(raw) or os.path.realpath(os.path.expanduser(raw))
    if not os.path.isdir(resolved):
        raise ValueError("workspace folder not found")
    return resolved


def _authenticate_ws(websocket: WebSocket, auth_manager=None) -> Optional[str]:
    """Return username (empty string in single-user mode)."""
    return ""


def setup_terminal_routes() -> APIRouter:
    router = APIRouter(tags=["terminal"])

    @router.websocket("/api/terminal/ws")
    async def terminal_ws(
        websocket: WebSocket,
        workspace: str = Query(""),
        session: str = Query(""),
        cols: int = Query(80, ge=2, le=500),
        rows: int = Query(24, ge=2, le=200),
    ):
        auth_manager = getattr(websocket.app.state, "auth_manager", None)
        user = _authenticate_ws(websocket, auth_manager)
        if user is None:
            await websocket.close(code=4401, reason="Not authenticated")
            return
        if not owner_is_admin_or_single_user(user):
            await websocket.close(code=4403, reason="Admin only")
            return
        if not pty_available():
            await websocket.close(code=4503, reason=pty_unavailable_reason()[:120])
            return

        try:
            cwd = _resolve_terminal_cwd(workspace)
        except ValueError as exc:
            await websocket.close(code=4400, reason=str(exc)[:120])
            return

        session_id = (session or "").strip() or str(uuid.uuid4())
        await websocket.accept()

        term: Optional[object] = None
        try:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "status",
                        "phase": "starting",
                        "message": "Starting shell…",
                        "session": session_id,
                    }
                )
            )
            term = await session_manager.get_or_create(session_id, cwd, cols, rows)
            scrollback = term.scrollback_bytes()
            if scrollback:
                await websocket.send_bytes(scrollback)
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "status",
                        "phase": "ready",
                        "message": "Connected",
                        "session": session_id,
                        "cwd": cwd,
                    }
                )
            )

            read_task = asyncio.create_task(_pty_to_ws(websocket, term))
            try:
                await _ws_to_pty(websocket, term)
            finally:
                read_task.cancel()
                try:
                    await read_task
                except asyncio.CancelledError:
                    pass
        except WebSocketDisconnect:
            logger.info("Terminal WebSocket disconnected session=%s", session_id)
        except Exception as exc:
            logger.warning("Terminal session error: %s", exc, exc_info=True)
            try:
                await websocket.send_text(
                    json.dumps({"type": "status", "phase": "error", "message": str(exc)})
                )
            except Exception:
                pass
        finally:
            await session_manager.close(session_id)

    return router


async def _pty_to_ws(websocket: WebSocket, term) -> None:
    while term.is_alive():
        try:
            chunk = await term.read(4096)
            if chunk:
                await websocket.send_bytes(chunk)
            else:
                await asyncio.sleep(0.02)
        except WebSocketDisconnect:
            break
        except Exception as exc:
            logger.debug("PTY read stopped: %s", exc)
            break


async def _ws_to_pty(websocket: WebSocket, term) -> None:
    while True:
        message = await websocket.receive()
        if message.get("type") == "websocket.disconnect":
            break
        if "bytes" in message and message["bytes"] is not None:
            await term.write(message["bytes"])
            continue
        text = message.get("text")
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            await term.write(text.encode("utf-8", errors="replace"))
            continue
        msg_type = payload.get("type")
        if msg_type == "resize":
            cols = int(payload.get("cols", 80))
            rows = int(payload.get("rows", 24))
            await term.resize(cols, rows)
        elif msg_type == "input":
            data = payload.get("data", "")
            if isinstance(data, str):
                await term.write(data.encode("utf-8", errors="replace"))
