"""Interactive PTY sessions for the workspace terminal panel.

Windows: pywinpty (ConPTY) when installed — see requirements-optional.txt.
POSIX: stdlib pty + asyncio subprocess.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections import deque
from typing import Deque, Dict, Optional

from core.platform_compat import IS_WINDOWS, find_bash

logger = logging.getLogger(__name__)

SCROLLBACK_MAX_BYTES = 512_000

try:
    from winpty import PtyProcess as _WinPtyProcess  # type: ignore

    HAS_WINPTY = True
except ImportError:
    _WinPtyProcess = None  # type: ignore
    HAS_WINPTY = False

try:
    import fcntl
    import pty
    import select

    HAS_POSIX_PTY = hasattr(os, "setsid")
except ImportError:
    fcntl = None  # type: ignore
    pty = None  # type: ignore
    select = None  # type: ignore
    HAS_POSIX_PTY = False


def pty_available() -> bool:
    if IS_WINDOWS:
        return HAS_WINPTY
    return HAS_POSIX_PTY


def pty_unavailable_reason() -> str:
    if IS_WINDOWS:
        return (
            "Windows ConPTY requires pywinpty. Install with: "
            "pip install pywinpty  (or pip install -r requirements-optional.txt)"
        )
    return "PTY is not available on this platform."


def _shell_argv() -> list[str]:
    """Pick an interactive login shell for the host OS."""
    if IS_WINDOWS:
        for cand in (
            shutil.which("pwsh.exe"),
            shutil.which("pwsh"),
            shutil.which("powershell.exe"),
            find_bash(),
            os.environ.get("ComSpec", "cmd.exe"),
        ):
            if cand:
                base = os.path.basename(cand).lower()
                if base in ("pwsh.exe", "pwsh"):
                    return [cand, "-NoLogo"]
                if base == "powershell.exe":
                    return [cand, "-NoLogo"]
                if "bash" in base:
                    return [cand, "--login", "-i"]
                return [cand]
        return ["cmd.exe"]
    shell = os.environ.get("SHELL") or "/bin/bash"
    return [shell, "-l"]


def _shell_env(cwd: str) -> dict[str, str]:
    env = os.environ.copy()
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    env["PWD"] = cwd
    return env


class TerminalSession:
    """One persistent interactive shell bound to a workspace directory."""

    def __init__(self, cwd: str, cols: int = 80, rows: int = 24) -> None:
        self.cwd = cwd
        self.cols = max(cols, 2)
        self.rows = max(rows, 2)
        self._win_pty = None
        self._master_fd: Optional[int] = None
        self._proc = None
        self._closed = False
        self._scrollback: Deque[bytes] = deque()
        self._scrollback_bytes = 0

    def _append_scrollback(self, data: bytes) -> None:
        if not data:
            return
        self._scrollback.append(data)
        self._scrollback_bytes += len(data)
        while self._scrollback_bytes > SCROLLBACK_MAX_BYTES and self._scrollback:
            dropped = self._scrollback.popleft()
            self._scrollback_bytes -= len(dropped)

    def scrollback_bytes(self) -> bytes:
        return b"".join(self._scrollback)

    async def start(self) -> None:
        if not pty_available():
            raise RuntimeError(pty_unavailable_reason())
        env = _shell_env(self.cwd)
        argv = _shell_argv()
        if IS_WINDOWS:
            await self._start_windows(argv, env)
        else:
            await self._start_posix(argv, env)

    async def _start_windows(self, argv: list[str], env: dict[str, str]) -> None:
        assert _WinPtyProcess is not None

        def _spawn():
            return _WinPtyProcess.spawn(
                argv,
                cwd=self.cwd,
                env=env,
                dimensions=(self.cols, self.rows),
            )

        self._win_pty = await asyncio.to_thread(_spawn)

    async def _start_posix(self, argv: list[str], env: dict[str, str]) -> None:
        assert pty is not None and fcntl is not None

        master_fd, slave_fd = pty.openpty()
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=self.cwd,
            env=env,
            preexec_fn=os.setsid,
        )
        os.close(slave_fd)
        self._master_fd = master_fd

    async def read(self, size: int = 4096) -> bytes:
        if self._closed:
            return b""
        if self._win_pty is not None:
            return await self._read_windows(size)
        return await self._read_posix(size)

    async def _read_windows(self, size: int) -> bytes:
        pty_proc = self._win_pty
        assert pty_proc is not None

        def _read():
            try:
                return pty_proc.read(size)
            except Exception:
                return b""

        data = await asyncio.to_thread(_read)
        if isinstance(data, str):
            data = data.encode("utf-8", errors="replace")
        if data:
            self._append_scrollback(data)
        return data or b""

    async def _read_posix(self, size: int) -> bytes:
        fd = self._master_fd
        if fd is None:
            return b""
        loop = asyncio.get_running_loop()

        def _blocking_read() -> bytes:
            try:
                r, _, _ = select.select([fd], [], [], 0.05)
                if not r:
                    return b""
                chunk = os.read(fd, size)
                return chunk or b""
            except OSError:
                return b""

        data = await loop.run_in_executor(None, _blocking_read)
        if data:
            self._append_scrollback(data)
        return data

    async def write(self, data: bytes) -> None:
        if self._closed or not data:
            return
        if self._win_pty is not None:
            payload = data.decode("utf-8", errors="replace")
            await asyncio.to_thread(self._win_pty.write, payload)
            return
        fd = self._master_fd
        if fd is None:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, os.write, fd, data)

    async def resize(self, cols: int, rows: int) -> None:
        self.cols = max(cols, 2)
        self.rows = max(rows, 2)
        if self._win_pty is not None:
            await asyncio.to_thread(self._win_pty.set_size, self.cols, self.rows)
            return
        fd = self._master_fd
        if fd is None:
            return
        import struct
        import termios

        try:
            winsize = struct.pack("HHHH", self.rows, self.cols, 0, 0)
            termios.TIOCSWINSZ = getattr(termios, "TIOCSWINSZ", 0x5414)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
        except Exception as exc:
            logger.debug("PTY resize failed: %s", exc)

    def is_alive(self) -> bool:
        if self._closed:
            return False
        if self._win_pty is not None:
            try:
                return bool(self._win_pty.isalive())
            except Exception:
                return False
        if self._proc is not None:
            return self._proc.returncode is None
        return False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._win_pty is not None:
            try:
                if self._win_pty.isalive():
                    await asyncio.to_thread(self._win_pty.terminate, True)
            except Exception:
                pass
            self._win_pty = None
        if self._proc is not None:
            try:
                self._proc.kill()
                await self._proc.wait()
            except ProcessLookupError:
                pass
            except Exception:
                pass
            self._proc = None
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None


class TerminalSessionManager:
    """In-process registry of terminal sessions (keyed by client session id)."""

    def __init__(self) -> None:
        self._sessions: Dict[str, TerminalSession] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(
        self, session_id: str, cwd: str, cols: int, rows: int, *, reuse: bool = True
    ) -> TerminalSession:
        async with self._lock:
            existing = self._sessions.get(session_id)
            if existing and reuse and existing.is_alive() and existing.cwd == cwd:
                await existing.resize(cols, rows)
                return existing
            if existing:
                await existing.close()
            sess = TerminalSession(cwd, cols, rows)
            await sess.start()
            self._sessions[session_id] = sess
            return sess

    async def close(self, session_id: str) -> None:
        async with self._lock:
            sess = self._sessions.pop(session_id, None)
        if sess:
            await sess.close()

    async def close_all(self) -> None:
        async with self._lock:
            ids = list(self._sessions.keys())
        for sid in ids:
            await self.close(sid)


# Module-level singleton used by terminal routes
session_manager = TerminalSessionManager()
