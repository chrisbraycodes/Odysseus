"""Tests for workspace terminal WebSocket routes and PTY manager."""
import os
import tempfile

import pytest

from src.terminal_manager import (
    TerminalSession,
    TerminalSessionManager,
    pty_available,
    pty_unavailable_reason,
    _shell_argv,
)
from routes import terminal_routes as tr


def test_pty_unavailable_reason_non_empty_on_windows(monkeypatch):
    monkeypatch.setattr(tr, "pty_available", lambda: False)
    reason = pty_unavailable_reason()
    assert reason
    assert "pywinpty" in reason.lower() or "pty" in reason.lower()


def test_resolve_terminal_cwd_valid(tmp_path):
    cwd = tr._resolve_terminal_cwd(str(tmp_path))
    assert os.path.isdir(cwd)
    assert os.path.samefile(cwd, tmp_path)


def test_resolve_terminal_cwd_missing():
    with pytest.raises(ValueError, match="not found"):
        tr._resolve_terminal_cwd("/nonexistent/workspace/path/xyz")


def test_resolve_terminal_cwd_empty():
    with pytest.raises(ValueError, match="required"):
        tr._resolve_terminal_cwd("")


def test_shell_argv_returns_nonempty():
    argv = _shell_argv()
    assert argv
    assert argv[0]


@pytest.mark.asyncio
async def test_terminal_session_manager_create_and_close(tmp_path):
    if not pty_available():
        pytest.skip(pty_unavailable_reason())
    mgr = TerminalSessionManager()
    sid = "test-session-1"
    sess = await mgr.get_or_create(sid, str(tmp_path), 80, 24)
    assert sess.is_alive()
    await sess.write(b"echo terminal_test_marker\r\n")
    # Allow shell to echo
    got = b""
    for _ in range(50):
        chunk = await sess.read(4096)
        if chunk:
            got += chunk
        if b"terminal_test_marker" in got:
            break
        await __import__("asyncio").sleep(0.05)
    await mgr.close(sid)
    assert not sess.is_alive()


@pytest.mark.asyncio
async def test_terminal_session_scrollback(tmp_path):
    if not pty_available():
        pytest.skip(pty_unavailable_reason())
    sess = TerminalSession(str(tmp_path), 80, 24)
    await sess.start()
    await sess.write(b"echo scrollback_marker\r\n")
    got = b""
    for _ in range(50):
        chunk = await sess.read(4096)
        if chunk:
            got += chunk
        if b"scrollback_marker" in got:
            break
        await __import__("asyncio").sleep(0.05)
    assert b"scrollback_marker" in sess.scrollback_bytes()
    await sess.close()


def test_authenticate_ws_rejects_without_cookie():
  class _Auth:
    is_configured = True

    def validate_token(self, _):
      return False

    def get_username_for_token(self, _):
      return "admin"

  class _WS:
    cookies = {}

    app = type("A", (), {"state": type("S", (), {"auth_manager": _Auth()})()})()

  assert tr._authenticate_ws(_WS(), _Auth()) is None


def test_authenticate_ws_accepts_valid_cookie(monkeypatch):
  monkeypatch.setenv("AUTH_ENABLED", "true")

  class _Auth:
    is_configured = True

    def validate_token(self, tok):
      return tok == "good"

    def get_username_for_token(self, _):
      return "admin"

  class _WS:
    cookies = {"odysseus_session": "good"}
    app = type("A", (), {"state": type("S", (), {"auth_manager": _Auth()})()})()

  assert tr._authenticate_ws(_WS(), _Auth()) == "admin"


@pytest.mark.asyncio
async def test_session_manager_multiple_sessions(tmp_path):
    if not pty_available():
        pytest.skip(pty_unavailable_reason())
    mgr = TerminalSessionManager()
    a = await mgr.get_or_create("a", str(tmp_path), 80, 24)
    b = await mgr.get_or_create("b", str(tmp_path), 80, 24)
    assert a is not b
    assert a.is_alive() and b.is_alive()
    await mgr.close("a")
    assert not a.is_alive()
    assert b.is_alive()
    await mgr.close("b")
