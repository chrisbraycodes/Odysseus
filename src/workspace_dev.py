"""Automatic Node/dev-server helpers for Docker workspace bind mounts.

When /workspace is mounted (Desktop → container), npm projects often need:
  * deps installed inside the container (not only on the Windows host)
  * HOST=0.0.0.0 / BROWSER=none so mapped preview ports work
  * long-running dev servers run in the background

Tool execution calls ``prepare_node_workspace_command`` so the agent (and the
user typing ``npm start``) do not need IDE intervention for these steps.
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional, Tuple

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
    """True when the Desktop /workspace bind mount is present."""
    return os.path.isdir("/workspace")


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
    """Prefix env / flags so dev servers are reachable via Docker port maps."""
    if not docker_workspace_mounted():
        return cmd
    out = cmd.strip()
    if _is_react_dev_command(out) and not re.search(r"\bHOST=", out):
        out = f"HOST=0.0.0.0 BROWSER=none {out}"
    if _is_vite_command(out) and not re.search(r"--host\b", out):
        out = re.sub(r"\bvite\b", "vite --host 0.0.0.0", out, count=1)
    return out


def dev_server_preview_url(cmd: str) -> Optional[str]:
    if not docker_workspace_mounted() or not is_dev_server_command(cmd):
        return None
    host = preview_host()
    if _is_vite_command(cmd) and not _is_react_dev_command(cmd):
        return f"http://{host}:{vite_preview_port()}/"
    return f"http://{host}:{react_preview_port()}/"


def prepare_node_workspace_command(
    cmd: str, workspace: Optional[str]
) -> Tuple[str, Optional[str]]:
    """Normalize a bash command for workspace Node projects.

    Returns ``(prepared_command, preview_url)``. When deps are missing,
    ``npm install &&`` is prepended automatically. Dev-server env is injected
    when running under the /workspace Docker mount.
    """
    prepared = (cmd or "").strip()
    if not prepared or not workspace:
        return prepared, dev_server_preview_url(prepared)

    install_prefix = ""
    if (
        needs_npm_deps(prepared)
        and npm_deps_missing(workspace)
        and not _NPM_INSTALL_RE.match(prepared)
    ):
        install_prefix = "npm install && "

    if is_dev_server_command(prepared):
        prepared = inject_dev_server_env(prepared)

    return install_prefix + prepared, dev_server_preview_url(prepared)


def preview_note(url: Optional[str]) -> str:
    if not url:
        return ""
    return (
        f"\n\nDev preview (open in a new browser tab when the server is ready): {url}"
    )
