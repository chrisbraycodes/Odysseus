"""Workspace confinement: file tools are hard-bounded to the workspace folder
(layered on upstream's sensitive-path policy); bash runs with cwd there."""
import os
import tempfile

import pytest

from src.tool_execution import _resolve_tool_path_in_workspace, _direct_fallback


def test_workspace_resolver_confines():
    ws = tempfile.mkdtemp()
    open(os.path.join(ws, "a.txt"), "w").write("x")
    real = os.path.realpath(os.path.join(ws, "a.txt"))
    # relative path resolves under the workspace
    assert _resolve_tool_path_in_workspace(ws, "a.txt") == real
    # absolute path inside the workspace is allowed
    assert _resolve_tool_path_in_workspace(ws, os.path.join(ws, "a.txt")) == real
    # absolute path outside is rejected (sibling temp dir, portable across OSes)
    outside = tempfile.mkdtemp()
    with pytest.raises(ValueError):
        _resolve_tool_path_in_workspace(ws, os.path.join(outside, "x.txt"))
    # parent-escape is rejected
    with pytest.raises(ValueError):
        _resolve_tool_path_in_workspace(ws, os.path.join("..", "..", "escape.txt"))


def test_workspace_resolver_blocks_sensitive():
    """Upstream's sensitive-file deny list still applies inside the workspace."""
    ws = tempfile.mkdtemp()
    os.makedirs(os.path.join(ws, ".ssh"), exist_ok=True)
    with pytest.raises(ValueError):
        _resolve_tool_path_in_workspace(ws, ".ssh/authorized_keys")


@pytest.mark.asyncio
async def test_read_write_confined_in_workspace():
    ws = tempfile.mkdtemp()
    # Write inside the workspace (relative path) succeeds.
    res = await _direct_fallback("write_file", "note.txt\nhello", workspace=ws)
    assert res["exit_code"] == 0
    assert os.path.isfile(os.path.join(ws, "note.txt"))
    # Read it back.
    res = await _direct_fallback("read_file", "note.txt", workspace=ws)
    assert res["exit_code"] == 0 and res["output"] == "hello"
    # Reading outside the workspace is rejected (sibling temp dir, portable).
    outside = tempfile.mkdtemp()
    outside_file = os.path.join(outside, "secret.txt")
    open(outside_file, "w").write("nope")
    res = await _direct_fallback("read_file", outside_file, workspace=ws)
    assert res["exit_code"] == 1 and "outside the workspace" in res["error"]
    # Writing outside is rejected (file must not be created).
    escape = os.path.join(outside, "_ws_escape.txt")
    res = await _direct_fallback("write_file", f"{escape}\nx", workspace=ws)
    assert res["exit_code"] == 1 and "outside the workspace" in res["error"]
    assert not os.path.exists(escape)


def test_browse_is_admin_gated(monkeypatch):
    """The directory-browser endpoint must refuse non-admin callers."""
    from fastapi import HTTPException
    import routes.workspace_routes as wr

    router = wr.setup_workspace_routes()
    browse = next(r.endpoint for r in router.routes if r.path == "/api/workspace/browse")

    monkeypatch.setattr(wr, "get_current_user", lambda req: "bob")
    monkeypatch.setattr(wr, "owner_is_admin_or_single_user", lambda owner: False)
    with pytest.raises(HTTPException) as ei:
        browse(request=object(), path="/")
    assert ei.value.status_code == 403

    # Admin / single-user is allowed.
    monkeypatch.setattr(wr, "owner_is_admin_or_single_user", lambda owner: True)
    out = browse(request=object(), path=os.path.expanduser("~"))
    assert "dirs" in out and "path" in out
    assert all("name" in d and "path" in d for d in out["dirs"])


def test_workspace_list_read_write_api(monkeypatch, tmp_path):
    """Project explorer endpoints list/read/write within the workspace root."""
    from fastapi import HTTPException
    import routes.workspace_routes as wr

    ws = tmp_path / "project"
    ws.mkdir()
    (ws / "hello.txt").write_text("hi", encoding="utf-8")
    sub = ws / "src"
    sub.mkdir()
    (sub / "app.js").write_text("console.log(1)", encoding="utf-8")

    monkeypatch.setattr(wr, "get_current_user", lambda req: "admin")
    monkeypatch.setattr(wr, "owner_is_admin_or_single_user", lambda owner: True)

    router = wr.setup_workspace_routes()
    list_fn = next(r.endpoint for r in router.routes if r.path == "/api/workspace/list")
    read_fn = next(r.endpoint for r in router.routes if r.path == "/api/workspace/file" and "GET" in r.methods)
    write_fn = next(r.endpoint for r in router.routes if r.path == "/api/workspace/file" and "PUT" in r.methods)

    root = str(ws)
    listed = list_fn(request=object(), workspace=root, path="")
    assert listed["path"] == ""
    assert any(d["name"] == "src" for d in listed["dirs"])
    assert any(f["name"] == "hello.txt" for f in listed["files"])

    nested = list_fn(request=object(), workspace=root, path="src")
    assert nested["path"] == "src"
    assert any(f["name"] == "app.js" for f in nested["files"])

    got = read_fn(request=object(), workspace=root, path="hello.txt")
    assert got["content"] == "hi"

    from types import SimpleNamespace
    body = SimpleNamespace(workspace=root, path="src/new.txt", content="new")
    write_fn(request=object(), body=body)
    assert (sub / "new.txt").read_text(encoding="utf-8") == "new"

    monkeypatch.setattr(wr, "owner_is_admin_or_single_user", lambda owner: False)
    with pytest.raises(HTTPException) as ei:
        list_fn(request=object(), workspace=root, path="")
    assert ei.value.status_code == 403


def test_docker_rejects_unmapped_host_workspace(monkeypatch):
    """In Docker, host paths outside /workspace must not reach the file tree API."""
    from fastapi import HTTPException
    import routes.workspace_routes as wr

    monkeypatch.setattr(wr, "docker_workspace_available", lambda: True)
    monkeypatch.setattr(wr, "resolve_workspace_path", lambda raw: "")
    monkeypatch.setattr(wr, "get_current_user", lambda req: "admin")
    monkeypatch.setattr(wr, "owner_is_admin_or_single_user", lambda owner: True)

    router = wr.setup_workspace_routes()
    list_fn = next(r.endpoint for r in router.routes if r.path == "/api/workspace/list")

    with pytest.raises(HTTPException) as ei:
        list_fn(request=object(), workspace=r"C:\Users\Public", path="")
    assert ei.value.status_code == 400
    assert "container" in ei.value.detail.lower()


def test_workspace_mkdir_api(monkeypatch, tmp_path):
    """POST /api/workspace/mkdir creates directories within the workspace."""
    from fastapi import HTTPException
    import routes.workspace_routes as wr

    ws = tmp_path / "project"
    ws.mkdir()

    monkeypatch.setattr(wr, "get_current_user", lambda req: "admin")
    monkeypatch.setattr(wr, "owner_is_admin_or_single_user", lambda owner: True)

    router = wr.setup_workspace_routes()
    mkdir_fn = next(r.endpoint for r in router.routes if r.path == "/api/workspace/mkdir")

    from types import SimpleNamespace

    root = str(ws)
    body = SimpleNamespace(workspace=root, path="src/components")
    out = mkdir_fn(request=object(), body=body)
    assert out["path"] == "src/components"
    assert (ws / "src" / "components").is_dir()

    with pytest.raises(HTTPException) as ei:
        mkdir_fn(request=object(), body=body)
    assert ei.value.status_code == 409


def test_workspace_delete_file_api(monkeypatch, tmp_path):
    """DELETE /api/workspace/file removes files and folders within the workspace."""
    from fastapi import HTTPException
    import routes.workspace_routes as wr

    ws = tmp_path / "project"
    ws.mkdir()
    hello = ws / "hello.txt"
    hello.write_text("hi", encoding="utf-8")
    empty_dir = ws / "empty"
    empty_dir.mkdir()
    nested = ws / "nested"
    nested.mkdir()
    (nested / "a.txt").write_text("a", encoding="utf-8")

    monkeypatch.setattr(wr, "get_current_user", lambda req: "admin")
    monkeypatch.setattr(wr, "owner_is_admin_or_single_user", lambda owner: True)

    router = wr.setup_workspace_routes()
    delete_fn = next(
        r.endpoint for r in router.routes
        if r.path == "/api/workspace/file" and "DELETE" in r.methods
    )

    root = str(ws)
    delete_fn(request=object(), workspace=root, path="hello.txt", recursive=False)
    assert not hello.exists()

    delete_fn(request=object(), workspace=root, path="empty", recursive=False)
    assert not empty_dir.exists()

    delete_fn(request=object(), workspace=root, path="nested", recursive=True)
    assert not nested.exists()

    with pytest.raises(HTTPException) as ei:
        delete_fn(request=object(), workspace=root, path="nested", recursive=False)
    assert ei.value.status_code == 404


class _FakeUpload:
    def __init__(self, data: bytes, filename: str):
        self._data = data
        self.filename = filename

    async def read(self, size: int = -1):
        if size is None or size < 0:
            chunk, self._data = self._data, b""
            return chunk
        chunk, self._data = self._data[:size], self._data[size:]
        return chunk


@pytest.mark.asyncio
async def test_workspace_import_and_download_api(monkeypatch, tmp_path):
    """POST /import copies uploads into the workspace; GET /download streams them out."""
    from fastapi import HTTPException
    from starlette.responses import FileResponse
    import routes.workspace_routes as wr

    ws = tmp_path / "project"
    ws.mkdir()

    monkeypatch.setattr(wr, "get_current_user", lambda req: "admin")
    monkeypatch.setattr(wr, "owner_is_admin_or_single_user", lambda owner: True)

    router = wr.setup_workspace_routes()
    import_fn = next(r.endpoint for r in router.routes if r.path == "/api/workspace/import")
    download_fn = next(r.endpoint for r in router.routes if r.path == "/api/workspace/download")

    root = str(ws)
    payload = b"imported bytes"
    result = await import_fn(
        request=object(),
        workspace=root,
        path="",
        file=_FakeUpload(payload, "from-host.txt"),
    )
    assert result["path"] == "from-host.txt"
    assert (ws / "from-host.txt").read_bytes() == payload

    sub = ws / "subdir"
    sub.mkdir()
    await import_fn(
        request=object(),
        workspace=root,
        path="subdir",
        file=_FakeUpload(b"nested", "nested.txt"),
    )
    assert (sub / "nested.txt").read_bytes() == b"nested"

    # Path segments in the upload name are stripped — file stays inside workspace.
    result = await import_fn(
        request=object(),
        workspace=root,
        path="",
        file=_FakeUpload(b"safe", "../escape.txt"),
    )
    assert result["path"] == "escape.txt"
    assert (ws / "escape.txt").read_bytes() == b"safe"
    assert not (ws.parent / "escape.txt").exists()

    with pytest.raises(HTTPException) as ei:
        await import_fn(
            request=object(),
            workspace=root,
            path="",
            file=_FakeUpload(b"x", ".."),
        )
    assert ei.value.status_code == 400

    resp = download_fn(request=object(), workspace=root, path="from-host.txt")
    assert isinstance(resp, FileResponse)
    assert os.path.basename(resp.path) == "from-host.txt"


@pytest.mark.asyncio
async def test_subprocess_runs_with_workspace_cwd():
    """bash/python subprocesses run with cwd set to the workspace. Use the
    python tool for an OS-agnostic cwd probe (Windows cmd has no `pwd`)."""
    ws = tempfile.mkdtemp()
    res = await _direct_fallback("python", "import os; print(os.getcwd())", workspace=ws)
    assert res["exit_code"] == 0
    assert os.path.realpath(res["output"].strip()) == os.path.realpath(ws)


# --- Tools that landed after this PR, now wired into the workspace -----------

@pytest.mark.asyncio
async def test_edit_file_confined_in_workspace():
    import json
    from src.tool_execution import _do_edit_file
    ws = tempfile.mkdtemp()
    open(os.path.join(ws, "f.txt"), "w").write("foo bar")
    # Edit inside the workspace succeeds.
    res = await _do_edit_file(json.dumps(
        {"path": "f.txt", "old_string": "foo", "new_string": "baz"}), workspace=ws)
    assert res["exit_code"] == 0
    assert open(os.path.join(ws, "f.txt")).read() == "baz bar"
    # Editing outside the workspace is rejected (sibling temp dir, portable).
    outside = tempfile.mkdtemp()
    outside_file = os.path.join(outside, "f.txt")
    open(outside_file, "w").write("a")
    res = await _do_edit_file(json.dumps(
        {"path": outside_file, "old_string": "a", "new_string": "b"}), workspace=ws)
    assert res["exit_code"] == 1 and "outside the workspace" in res["error"]


@pytest.mark.asyncio
async def test_grep_and_ls_confined_in_workspace():
    import json
    ws = tempfile.mkdtemp()
    open(os.path.join(ws, "doc.txt"), "w").write("hello workspace\n")
    # grep with no path searches the workspace root and finds the match.
    res = await _direct_fallback("grep", json.dumps({"pattern": "hello"}), workspace=ws)
    assert res["exit_code"] == 0 and "doc.txt" in res["output"]
    # grep pointed outside the workspace is rejected (sibling temp dir, portable).
    outside = tempfile.mkdtemp()
    res = await _direct_fallback("grep", json.dumps({"pattern": "x", "path": outside}), workspace=ws)
    assert res["exit_code"] == 1 and "outside the workspace" in res["error"]
    # ls of the workspace lists its files; ls outside is rejected.
    res = await _direct_fallback("ls", "", workspace=ws)
    assert res["exit_code"] == 0 and "doc.txt" in res["output"]
    res = await _direct_fallback("ls", outside, workspace=ws)
    assert res["exit_code"] == 1 and "outside the workspace" in res["error"]
