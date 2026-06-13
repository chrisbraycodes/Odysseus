"""Automatic Node/dev-server helpers for Docker workspace bind mounts.

With WORKSPACE_DEV_EXEC=host (default in Docker), dev servers run on the
user's computer at the bind-mounted host folder — not inside the container.
Files stay in sync because /workspace is the same directory on disk.

With WORKSPACE_DEV_EXEC=container, dev servers run in the container with
HOST=0.0.0.0 so mapped preview ports work.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from typing import Optional, Tuple

from src.workspace_path import container_path_to_host

_DEV_SERVER_RE = re.compile(
    r"(?:"
    r"\bnpm\s+start\b"
    r"|\bnpm\s+run\s+(?:dev|start|serve)\b"
    r"|\bnpx\s+(?:react-scripts\s+start|vite)\b"
    r"|\byarn\s+(?:dev|start)\b"
    r"|\bpnpm\s+(?:dev|start|run\s+(?:dev|start))\b"
    r"|\bvite(?:\s|$)"
    r")",
    re.I,
)

_NPM_NEEDS_DEPS_RE = re.compile(
    r"(?:"
    r"\bnpm\s+(?:run|start|exec|test)\b"
    r"|\bnpx\s+\S"
    r"|\byarn\b"
    r"|\bpnpm\b"
    r")",
    re.I,
)

_NPM_INSTALL_RE = re.compile(r"^\s*npm\s+install\b", re.I)


def docker_workspace_mounted() -> bool:
    """True when the /workspace bind mount is present."""
    return os.path.isdir("/workspace")


def dev_exec_target() -> str:
    """host | container | local — where dev preview servers should run."""
    raw = (os.environ.get("WORKSPACE_DEV_EXEC") or "").strip().lower()
    if raw in ("host", "container", "local"):
        return raw
    if docker_workspace_mounted():
        return "host"
    return "local"


def dev_server_run_on_host() -> bool:
    return dev_exec_target() == "host" and docker_workspace_mounted()


def preview_host() -> str:
    return (
        os.environ.get("WORKSPACE_DEV_BIND")
        or os.environ.get("DEV_BIND")
        or "127.0.0.1"
    ).strip() or "127.0.0.1"


def react_preview_port() -> int:
    try:
        return int(os.environ.get("WORKSPACE_DEV_PORT", "3000"))
    except ValueError:
        return 3000


def vite_preview_port() -> int:
    try:
        return int(os.environ.get("WORKSPACE_VITE_PORT", "5173"))
    except ValueError:
        return 5173


def is_dev_server_command(cmd: str) -> bool:
    return bool(_DEV_SERVER_RE.search(cmd or ""))


def needs_npm_deps(cmd: str) -> bool:
    return bool(_NPM_NEEDS_DEPS_RE.search(cmd or ""))


def npm_deps_missing(workspace: str) -> bool:
    pkg = os.path.join(workspace, "package.json")
    if not os.path.isfile(pkg):
        return False
    nm = os.path.join(workspace, "node_modules")
    if not os.path.isdir(nm):
        return True
    bin_dir = os.path.join(nm, ".bin")
    if not os.path.isdir(bin_dir) or not os.listdir(bin_dir):
        return True
    try:
        with open(pkg, encoding="utf-8") as f:
            data = json.loads(f.read())
        scripts = data.get("scripts") or {}
        start = str(scripts.get("start", ""))
        dev = str(scripts.get("dev", ""))
        for script in (start, dev):
            if "react-scripts" in script:
                rs = os.path.join(bin_dir, "react-scripts")
                if not os.path.exists(rs) and not os.path.exists(rs + ".cmd"):
                    return True
            if "vite" in script:
                vt = os.path.join(bin_dir, "vite")
                if not os.path.exists(vt) and not os.path.exists(vt + ".cmd"):
                    return True
    except OSError:
        return True
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return False


def _is_vite_command(cmd: str) -> bool:
    return bool(re.search(r"\bvite\b", cmd, re.I))


def _is_react_dev_command(cmd: str) -> bool:
    return bool(
        re.search(r"\b(react-scripts|npm\s+start|npm\s+run\s+start)\b", cmd, re.I)
    )


def inject_dev_server_env(cmd: str) -> str:
    """Prefix env / flags so in-container dev servers are reachable via port maps."""
    if not docker_workspace_mounted() or dev_server_run_on_host():
        return cmd
    out = cmd.strip()
    if _is_react_dev_command(out) and not re.search(r"\bHOST=", out):
        out = f"HOST=0.0.0.0 BROWSER=none {out}"
    if _is_vite_command(out) and not re.search(r"--host\b", out):
        out = re.sub(r"\bvite\b", "vite --host 0.0.0.0", out, count=1)
    return out


def dev_server_preview_url(cmd: str) -> Optional[str]:
    if not is_dev_server_command(cmd):
        return None
    host = preview_host()
    if _is_vite_command(cmd) and not _is_react_dev_command(cmd):
        return f"http://{host}:{vite_preview_port()}/"
    return f"http://{host}:{react_preview_port()}/"


def host_dev_server_message(workspace: Optional[str], cmd: str) -> str:
    """Instructions when dev servers must run on the host computer."""
    host_dir = container_path_to_host(workspace or "") if workspace else ""
    host_dir = host_dir or "(your workspace folder on this computer)"
    preview = dev_server_preview_url(cmd) or f"http://{preview_host()}:{react_preview_port()}/"
    quoted = shlex.quote(host_dir) if host_dir.startswith("/") else f'"{host_dir}"'
    return (
        "Dev server runs on your computer (not inside the Docker container).\n"
        f"Workspace files are synced via the bind mount.\n\n"
        f"On your computer, open a terminal and run:\n"
        f"  cd {quoted}\n"
        f"  {cmd.strip()}\n\n"
        f"Then open the preview: {preview}"
    )


def prepare_node_workspace_command(
    cmd: str, workspace: Optional[str]
) -> Tuple[str, Optional[str], bool]:
    """Normalize a bash command for workspace Node projects.

    Returns ``(prepared_command, preview_url, run_on_host)``.
    When ``run_on_host`` is True, callers must not execute the dev-server
    command inside the container.
    """
    prepared = (cmd or "").strip()
    if not prepared or not workspace:
        return prepared, dev_server_preview_url(prepared), False

    run_on_host = dev_server_run_on_host() and is_dev_server_command(prepared)

    install_prefix = ""
    if (
        not run_on_host
        and needs_npm_deps(prepared)
        and npm_deps_missing(workspace)
        and not _NPM_INSTALL_RE.match(prepared)
    ):
        install_prefix = "npm install && "

    if is_dev_server_command(prepared):
        prepared = inject_dev_server_env(prepared)

    return install_prefix + prepared, dev_server_preview_url(prepared), run_on_host


def preview_note(url: Optional[str], *, run_on_host: bool = False, workspace: Optional[str] = None, cmd: str = "") -> str:
    if run_on_host and workspace and cmd:
        return "\n\n" + host_dev_server_message(workspace, cmd)
    if not url:
        return ""
    return (
        f"\n\nDev preview (open in a new browser tab when the server is ready): {url}"
    )
