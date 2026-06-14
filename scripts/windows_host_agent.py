#!/usr/bin/env python3
"""Windows host agent — runs on the Docker host, not inside the container.

Provides:
  GET  /health
  POST /v1/exec   — run a command in the workspace (scoped by default)
  WS   /v1/terminal — interactive PowerShell PTY

Bind to 127.0.0.1 only. Requires WORKSPACE_HOST_AGENT_TOKEN (or --token).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger("windows_host_agent")

try:
    from aiohttp import web
except ImportError:
    print("Install host agent deps: pip install -r scripts/host_agent_requirements.txt", file=sys.stderr)
    raise

try:
    from winpty import PtyProcess
except ImportError:
    PtyProcess = None  # type: ignore

# Reuse path helpers without pulling the full app stack.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from src.host_agent_paths import path_under_root, resolve_under_root  # noqa: E402

_SESSIONS: Dict[str, "HostTerminalSession"] = {}
_TOKEN = ""
_BIND = "127.0.0.1"
_HTTP_PORT = 17789
_WS_PORT = 17790

_NPM_CMD_RE = re.compile(
    r"(?:"
    r"\bnpm\b"
    r"|\bnpx\b"
    r"|\byarn\b"
    r"|\bpnpm\b"
    r"|\bnode\b"
    r"|\bvite\b"
    r")",
    re.I,
)


def _auth_ok(request: web.Request) -> bool:
    if not _TOKEN:
        return False
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and auth[7:].strip() == _TOKEN:
        return True
    return request.query.get("token", "").strip() == _TOKEN


def _shell_argv(shell: str = "powershell") -> list[str]:
    choice = (shell or "powershell").strip().lower()
    if choice == "cmd":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        cmd_path = os.path.join(system_root, "System32", "cmd.exe")
        if os.path.isfile(cmd_path):
            return [cmd_path]
        comspec = os.environ.get("ComSpec") or shutil.which("cmd.exe")
        return [comspec or "cmd.exe"]
    for cand in (
        shutil.which("pwsh.exe"),
        shutil.which("pwsh"),
        shutil.which("powershell.exe"),
    ):
        if cand:
            base = os.path.basename(cand).lower()
            if base in ("pwsh.exe", "pwsh", "powershell.exe"):
                return [cand, "-NoLogo"]
    return [os.environ.get("ComSpec", "cmd.exe")]


def _validate_scope(
    workspace_root: str,
    cwd: str,
    unrestricted: bool,
) -> tuple[str, str]:
    root = os.path.realpath(workspace_root)
    if not os.path.isdir(root):
        raise ValueError("workspace_root is not a directory")
    if unrestricted:
        use_cwd = os.path.realpath(cwd or root)
        return root, use_cwd
    safe_cwd = resolve_under_root(root, cwd or root)
    return root, safe_cwd


class HostTerminalSession:
    def __init__(self, cwd: str, cols: int, rows: int, shell: str = "powershell") -> None:
        self.id = str(uuid.uuid4())
        self.cwd = cwd
        self.cols = max(cols, 2)
        self.rows = max(rows, 2)
        self.shell = (shell or "powershell").strip().lower()
        self._pty = None
        self._closed = False

    def start(self) -> None:
        if PtyProcess is None:
            raise RuntimeError("pywinpty is required on Windows")
        argv = _shell_argv(self.shell)
        shell_label = "CMD" if self.shell == "cmd" else "PowerShell"
        banner = (
            "Prometheus Source — Windows host terminal (YARB Industries LLC).\r\n"
            f"Shell: {shell_label}. Commands run on your computer.\r\n"
            "Stay inside your workspace folder unless unrestricted access was enabled.\r\n\r\n"
        )
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        self._pty = PtyProcess.spawn(
            argv,
            cwd=self.cwd,
            env=env,
            dimensions=(self.cols, self.rows),
        )
        self._pty.write(banner)

    def read(self, size: int = 4096) -> bytes:
        if self._closed or not self._pty:
            return b""
        try:
            data = self._pty.read(size)
        except Exception:
            return b""
        if isinstance(data, str):
            return data.encode("utf-8", errors="replace")
        return data or b""

    def write(self, data: bytes) -> None:
        if self._closed or not self._pty or not data:
            return
        try:
            self._pty.write(data)
        except Exception:
            self._closed = True

    def resize(self, cols: int, rows: int) -> None:
        self.cols = max(cols, 2)
        self.rows = max(rows, 2)
        if self._pty and hasattr(self._pty, "set_size"):
            try:
                self._pty.set_size(self.cols, self.rows)
            except Exception:
                pass

    def is_alive(self) -> bool:
        if self._closed or not self._pty:
            return False
        try:
            return bool(self._pty.isalive())
        except Exception:
            return False

    def close(self) -> None:
        self._closed = True
        if self._pty:
            try:
                if self._pty.isalive():
                    self._pty.close()
            except Exception:
                pass
        self._pty = None


async def handle_health(request: web.Request) -> web.Response:
    if not _auth_ok(request):
        return web.json_response({"detail": "unauthorized"}, status=401)
    return web.json_response(
        {
            "ok": True,
            "platform": sys.platform,
            "pty": PtyProcess is not None,
            "http_port": _HTTP_PORT,
            "ws_port": _WS_PORT,
        }
    )


async def handle_exec(request: web.Request) -> web.Response:
    if not _auth_ok(request):
        return web.json_response({"detail": "unauthorized"}, status=401)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"detail": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"detail": "invalid body"}, status=400)

    workspace_root = str(body.get("workspace_root") or "")
    cwd = str(body.get("cwd") or workspace_root)
    command = str(body.get("command") or "").strip()
    background = bool(body.get("background"))
    unrestricted = bool(body.get("unrestricted"))

    if not command:
        return web.json_response({"detail": "command is required"}, status=400)
    try:
        root, safe_cwd = _validate_scope(workspace_root, cwd, unrestricted)
    except ValueError as exc:
        return web.json_response({"detail": str(exc)}, status=400)

    if not unrestricted and not _NPM_CMD_RE.search(command):
        return web.json_response(
            {
                "detail": (
                    "restricted host exec allows npm/node/yarn/pnpm/vite commands only — "
                    "enable unrestricted host access for other commands"
                )
            },
            status=403,
        )

    shell = os.environ.get("ComSpec", "cmd.exe")
    full_cmd = f'cd /d {shlex.quote(safe_cwd)} && {command}'
    logger.info("host exec cwd=%s background=%s cmd=%s", safe_cwd, background, command[:120])

    if background:
        proc = subprocess.Popen(
            full_cmd,
            shell=True,
            cwd=safe_cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0),
        )
        return web.json_response(
            {
                "output": f"Started on Windows host (pid {proc.pid}).",
                "exit_code": 0,
                "background": True,
                "pid": proc.pid,
                "cwd": safe_cwd,
                "workspace_root": root,
            }
        )

    completed = subprocess.run(
        full_cmd,
        shell=True,
        cwd=safe_cwd,
        capture_output=True,
        text=True,
        timeout=float(body.get("timeout") or 600),
    )
    output = (completed.stdout or "").rstrip()
    err = (completed.stderr or "").rstrip()
    if err:
        output = (output + "\nSTDERR: " + err).strip() if output else "STDERR: " + err
    return web.json_response(
        {
            "output": output or "(no output)",
            "exit_code": completed.returncode,
            "cwd": safe_cwd,
            "workspace_root": root,
        }
    )


async def handle_terminal_ws(request: web.Request) -> web.WebSocketResponse:
    if not _auth_ok(request):
        raise web.HTTPUnauthorized(text="unauthorized")
    if PtyProcess is None:
        raise web.HTTPServiceUnavailable(text="pywinpty not installed")

    workspace_root = request.query.get("workspace_root", "")
    cwd = request.query.get("cwd", workspace_root)
    unrestricted = request.query.get("unrestricted", "0") in ("1", "true", "yes")
    shell = (request.query.get("shell", "powershell") or "powershell").strip().lower()
    if shell not in ("powershell", "cmd"):
        shell = "powershell"
    cols = int(request.query.get("cols", "80"))
    rows = int(request.query.get("rows", "24"))
    session_id = request.query.get("session", "").strip() or str(uuid.uuid4())

    try:
        _, safe_cwd = _validate_scope(workspace_root, cwd, unrestricted)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    prev = _SESSIONS.pop(session_id, None)
    if prev is not None:
        prev.close()
    term = HostTerminalSession(safe_cwd, cols, rows, shell=shell)
    term.start()
    _SESSIONS[session_id] = term
    logger.info("terminal session %s shell=%s cwd=%s", session_id, shell, safe_cwd)

    shell_label = "CMD" if shell == "cmd" else "PowerShell"
    await ws.send_str(
        json.dumps(
            {
                "type": "status",
                "phase": "ready",
                "message": f"Connected to Windows host ({shell_label})",
                "session": session_id,
                "cwd": safe_cwd,
                "shell": shell,
            }
        )
    )

    async def pty_to_ws() -> None:
        while term.is_alive():
            chunk = await asyncio.to_thread(term.read, 4096)
            if chunk:
                await ws.send_bytes(chunk)
            else:
                await asyncio.sleep(0.02)

    reader = asyncio.create_task(pty_to_ws())
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.BINARY:
                term.write(msg.data)
            elif msg.type == web.WSMsgType.TEXT:
                text = msg.data
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    term.write(text.encode("utf-8", errors="replace"))
                    continue
                if payload.get("type") == "resize":
                    term.resize(int(payload.get("cols", 80)), int(payload.get("rows", 24)))
                elif payload.get("type") == "input":
                    data = payload.get("data", "")
                    if isinstance(data, str):
                        term.write(data.encode("utf-8", errors="replace"))
                else:
                    term.write(text.encode("utf-8", errors="replace"))
            elif msg.type in (web.WSMsgType.CLOSE, web.WSMsgType.ERROR):
                break
    finally:
        reader.cancel()
        try:
            await reader
        except asyncio.CancelledError:
            pass
        term.close()
        _SESSIONS.pop(session_id, None)
    return ws


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", handle_health)
    app.router.add_post("/v1/exec", handle_exec)
    app.router.add_get("/v1/terminal", handle_terminal_ws)
    return app


async def _run_servers(http_port: int, ws_port: int) -> None:
    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    http_site = web.TCPSite(runner, _BIND, http_port)
    ws_site = web.TCPSite(runner, _BIND, ws_port)
    await http_site.start()
    await ws_site.start()
    logger.info("Windows host agent listening on %s:%s (HTTP) and %s:%s (WS)", _BIND, http_port, _BIND, ws_port)
    while True:
        await asyncio.sleep(3600)


def main() -> None:
    global _TOKEN, _BIND, _HTTP_PORT, _WS_PORT
    parser = argparse.ArgumentParser(description="Odysseus Windows host agent")
    parser.add_argument("--token", default=os.environ.get("WORKSPACE_HOST_AGENT_TOKEN", ""))
    parser.add_argument("--bind", default=os.environ.get("WORKSPACE_HOST_AGENT_BIND", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("WORKSPACE_HOST_AGENT_PORT", "17789")))
    parser.add_argument("--ws-port", type=int, default=int(os.environ.get("WORKSPACE_HOST_AGENT_WS_PORT", "17790")))
    parser.add_argument("--log-file", default=os.environ.get("WORKSPACE_HOST_AGENT_LOG", ""))
    args = parser.parse_args()

    if sys.platform != "win32":
        print("windows_host_agent.py must run on Windows", file=sys.stderr)
        sys.exit(1)
    if not args.token:
        print("Set WORKSPACE_HOST_AGENT_TOKEN or pass --token", file=sys.stderr)
        sys.exit(1)

    log_file = (args.log_file or "").strip()
    handlers: list[logging.Handler] = []
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    else:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )
    _TOKEN = args.token.strip()
    _BIND = args.bind
    _HTTP_PORT = args.port
    _WS_PORT = args.ws_port
    asyncio.run(_run_servers(_HTTP_PORT, _WS_PORT))


if __name__ == "__main__":
    main()
