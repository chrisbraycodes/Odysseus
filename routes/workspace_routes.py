"""Workspace API — browse server directories, list/read/write/delete project files."""
import os
import shutil
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Query, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.auth_helpers import get_current_user
from src.tool_security import owner_is_admin_or_single_user
from src.tool_execution import _resolve_tool_path_in_workspace
from src.workspace_path import (
    docker_workspace_available,
    display_workspace_path,
    path_under_workspace_root,
    resolve_workspace_path,
    validate_workspace_submission,
)

_MAX_READ_BYTES = 512_000
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _require_workspace_admin(request: Request) -> None:
    owner = get_current_user(request)
    if not owner_is_admin_or_single_user(owner):
        raise HTTPException(status_code=403, detail="Workspace access is admin-only")


def _resolve_workspace_root(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="workspace is required")
    resolved = resolve_workspace_path(raw)
    if not resolved and docker_workspace_available():
        raise HTTPException(
            status_code=400,
            detail=(
                "workspace is not available inside the container — "
                "pick a folder under /workspace (your Desktop mount)"
            ),
        )
    if not resolved:
        try:
            resolved = os.path.realpath(os.path.expanduser(raw))
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not os.path.isdir(resolved):
        raise HTTPException(status_code=404, detail="workspace folder not found")
    if docker_workspace_available() and not path_under_workspace_root("/workspace", resolved):
        raise HTTPException(
            status_code=400,
            detail="workspace must be inside /workspace when running in Docker",
        )
    return resolved


def _resolve_browse_target(raw: str) -> str:
    text = (raw or "").strip()
    if text:
        resolved = resolve_workspace_path(text)
        if resolved:
            target = resolved
        elif docker_workspace_available():
            raise HTTPException(
                status_code=400,
                detail="browse outside /workspace is not available in the container",
            )
        else:
            target = os.path.realpath(os.path.expanduser(text))
    elif docker_workspace_available():
        target = os.path.realpath("/workspace")
    else:
        target = os.path.realpath(os.path.expanduser("~"))
    if not os.path.isdir(target):
        target = os.path.realpath(os.path.expanduser("~"))
    if docker_workspace_available() and not path_under_workspace_root("/workspace", target):
        raise HTTPException(
            status_code=403,
            detail="browse is limited to /workspace in the container",
        )
    return target


def _resolve_in_workspace(workspace: str, rel_path: str, *, must_be_dir: bool = False) -> str:
    rel = (rel_path or "").strip().replace("\\", "/").strip("/")
    try:
        if rel:
            resolved = _resolve_tool_path_in_workspace(workspace, rel)
        else:
            resolved = os.path.realpath(workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if must_be_dir and not os.path.isdir(resolved):
        raise HTTPException(status_code=404, detail="directory not found")
    if not must_be_dir and not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="file not found")
    return resolved


def _rel_to_workspace(workspace: str, absolute: str) -> str:
    rel = os.path.relpath(absolute, workspace)
    if rel == ".":
        return ""
    return rel.replace("\\", "/")


def _safe_upload_basename(name: str) -> str:
    base = os.path.basename((name or "").replace("\\", "/"))
    if not base or base in (".", ".."):
        raise HTTPException(status_code=400, detail="invalid filename")
    return base


def setup_workspace_routes():
    router = APIRouter(prefix="/api/workspace", tags=["workspace"])

    @router.get("/browse")
    def browse(request: Request, path: str = Query(default="")):
        """List subdirectories of `path` (default: home) so the UI can navigate
        the server filesystem and pick a workspace folder. Directories only.

        ADMIN-ONLY: this enumerates the server filesystem, so it is gated the
        same way the file/shell tools are (read_file/write_file/bash are in
        NON_ADMIN_BLOCKED_TOOLS). A non-admin who can't use those tools must not
        be able to map the host's directory tree either.
        """
        _require_workspace_admin(request)

        # Resolve symlinks so the reported path is canonical and the UI navigates
        # real directories (defends against symlink games in displayed paths).
        target = _resolve_browse_target(path)

        dirs = []
        try:
            with os.scandir(target) as it:
                for entry in it:
                    try:
                        # Don't follow symlinks when classifying — a symlinked
                        # dir is skipped rather than letting the browser wander
                        # off via a link. Hidden entries are omitted.
                        if entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
                            # Build the child path server-side with os.path.join
                            # so it's correct on Windows (backslashes) and Linux.
                            dirs.append({"name": entry.name, "path": os.path.join(target, entry.name)})
                    except OSError:
                        continue
        except (PermissionError, OSError):
            dirs = []

        parent = os.path.dirname(target)
        in_docker = docker_workspace_available()
        parent_out: Optional[str] = None
        parent_display_out: Optional[str] = None
        if parent and parent != target:
            if in_docker:
                ws_root = os.path.realpath("/workspace")
                try:
                    parent_real = os.path.realpath(parent)
                    if path_under_workspace_root(ws_root, parent_real):
                        parent_out = parent
                        parent_display_out = display_workspace_path(parent)
                except OSError:
                    pass
            else:
                parent_out = parent
                parent_display_out = display_workspace_path(parent)
        return {
            "path": target,
            "display_path": display_workspace_path(target),
            "docker_workspace": in_docker,
            "default_root": _docker_root() if in_docker else "",
            "parent": parent_out,
            "parent_display": parent_display_out,
            "dirs": sorted(
                [
                    {
                        "name": d["name"],
                        "path": d["path"],
                        "display_path": display_workspace_path(d["path"]),
                    }
                    for d in dirs
                ],
                key=lambda d: d["name"].lower(),
            ),
        }

    @router.get("/validate")
    def validate(request: Request, path: str = Query(default="")):
        """Check whether a workspace path exists (and normalize Desktop paths)."""
        _require_workspace_admin(request)

        raw = (path or "").strip()
        if not raw:
            return {
                "valid": False,
                "path": "",
                "docker_workspace": docker_workspace_available(),
                "default_root": _docker_root(),
            }

        ok, resolved, normalized_from = validate_workspace_submission(raw)
        return {
            "valid": ok,
            "path": resolved if ok else "",
            "display_path": display_workspace_path(resolved) if ok else "",
            "normalized_from": normalized_from,
            "docker_workspace": docker_workspace_available(),
            "default_root": _docker_root(),
        }

    @router.get("/list")
    def list_contents(
        request: Request,
        workspace: str = Query(..., description="Workspace root folder"),
        path: str = Query(default="", description="Subpath relative to workspace"),
    ):
        """List files and directories under a workspace folder (for the project explorer)."""
        _require_workspace_admin(request)
        root = _resolve_workspace_root(workspace)
        target = _resolve_in_workspace(root, path, must_be_dir=True)

        dirs: list[dict] = []
        files: list[dict] = []
        try:
            with os.scandir(target) as it:
                for entry in it:
                    try:
                        if entry.name.startswith("."):
                            continue
                        full = os.path.join(target, entry.name)
                        rel = _rel_to_workspace(root, full)
                        if entry.is_dir(follow_symlinks=False):
                            dirs.append({"name": entry.name, "path": rel, "type": "dir"})
                        elif entry.is_file(follow_symlinks=False):
                            try:
                                size = entry.stat(follow_symlinks=False).st_size
                            except OSError:
                                size = 0
                            files.append({"name": entry.name, "path": rel, "type": "file", "size": size})
                    except OSError:
                        continue
        except (PermissionError, OSError) as exc:
            raise HTTPException(status_code=403, detail=f"cannot read directory: {exc}") from exc

        parent_rel: Optional[str] = None
        if os.path.normcase(target) != os.path.normcase(root):
            parent_abs = os.path.dirname(target)
            parent_rel = _rel_to_workspace(root, parent_abs)

        return {
            "workspace": root,
            "workspace_display": display_workspace_path(root),
            "path": _rel_to_workspace(root, target),
            "parent": parent_rel,
            "dirs": sorted(dirs, key=lambda d: d["name"].lower()),
            "files": sorted(files, key=lambda d: d["name"].lower()),
        }

    @router.get("/file")
    def read_file(
        request: Request,
        workspace: str = Query(...),
        path: str = Query(..., description="File path relative to workspace"),
    ):
        """Read a text file inside the workspace."""
        _require_workspace_admin(request)
        root = _resolve_workspace_root(workspace)
        target = _resolve_in_workspace(root, path, must_be_dir=False)

        try:
            size = os.path.getsize(target)
        except OSError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if size > _MAX_READ_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"file too large ({size} bytes; max {_MAX_READ_BYTES})",
            )

        try:
            with open(target, "r", encoding="utf-8") as fh:
                content = fh.read()
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=415, detail="binary or non-UTF-8 file") from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return {
            "workspace": root,
            "path": _rel_to_workspace(root, target),
            "content": content,
            "size": size,
        }

    class _WriteBody(BaseModel):
        workspace: str
        path: str
        content: str

    @router.put("/file")
    def write_file(request: Request, body: _WriteBody):
        """Write a text file inside the workspace."""
        _require_workspace_admin(request)
        root = _resolve_workspace_root(body.workspace)
        rel = (body.path or "").strip().replace("\\", "/").strip("/")
        if not rel:
            raise HTTPException(status_code=400, detail="path is required")
        try:
            target = _resolve_tool_path_in_workspace(root, rel)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        parent = os.path.dirname(target)
        if parent:
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc

        try:
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(body.content)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return {
            "workspace": root,
            "path": _rel_to_workspace(root, target),
            "size": len(body.content.encode("utf-8")),
        }

    @router.delete("/file")
    def delete_path(
        request: Request,
        workspace: str = Query(...),
        path: str = Query(..., description="File or directory path relative to workspace"),
        recursive: bool = Query(default=False, description="Remove non-empty directories"),
    ):
        """Delete a file or directory inside the workspace."""
        _require_workspace_admin(request)
        root = _resolve_workspace_root(workspace)
        rel = (path or "").strip().replace("\\", "/").strip("/")
        if not rel:
            raise HTTPException(status_code=400, detail="path is required")
        try:
            target = _resolve_tool_path_in_workspace(root, rel)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not os.path.lexists(target):
            raise HTTPException(status_code=404, detail="path not found")

        try:
            if os.path.isdir(target) and not os.path.islink(target):
                if recursive:
                    shutil.rmtree(target)
                else:
                    os.rmdir(target)
            else:
                os.remove(target)
        except OSError as exc:
            if os.path.isdir(target):
                raise HTTPException(
                    status_code=409,
                    detail=f"directory not empty — set recursive=1 to delete: {exc}",
                ) from exc
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return {
            "workspace": root,
            "path": _rel_to_workspace(root, target),
            "deleted": True,
        }

    @router.post("/import")
    async def import_file(
        request: Request,
        workspace: str = Form(...),
        path: str = Form(default=""),
        file: UploadFile = File(...),
    ):
        """Upload a file from the browser into the workspace (copy into container)."""
        _require_workspace_admin(request)
        root = _resolve_workspace_root(workspace)
        rel_dir = (path or "").strip().replace("\\", "/").strip("/")
        if rel_dir:
            target_dir = _resolve_in_workspace(root, rel_dir, must_be_dir=True)
        else:
            target_dir = root

        filename = _safe_upload_basename(file.filename or "upload")
        try:
            rel_dest = os.path.relpath(os.path.join(target_dir, filename), root).replace("\\", "/")
            dest = _resolve_tool_path_in_workspace(root, rel_dest)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        size = 0
        try:
            with open(dest, "wb") as out:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > _MAX_UPLOAD_BYTES:
                        out.close()
                        if os.path.lexists(dest):
                            os.remove(dest)
                        raise HTTPException(
                            status_code=413,
                            detail=f"file too large (max {_MAX_UPLOAD_BYTES} bytes)",
                        )
                    out.write(chunk)
        except HTTPException:
            raise
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return {
            "workspace": root,
            "path": _rel_to_workspace(root, dest),
            "name": filename,
            "size": size,
        }

    @router.get("/download")
    def download_file(
        request: Request,
        workspace: str = Query(...),
        path: str = Query(..., description="File path relative to workspace"),
    ):
        """Download a workspace file to the browser (copy out of container)."""
        _require_workspace_admin(request)
        root = _resolve_workspace_root(workspace)
        target = _resolve_in_workspace(root, path, must_be_dir=False)
        return FileResponse(
            target,
            filename=os.path.basename(target),
            media_type="application/octet-stream",
        )

    return router


def _docker_root() -> str:
    return "/workspace" if docker_workspace_available() else ""
