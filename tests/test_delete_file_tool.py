"""Tests for delete_file agent tool."""

import os
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_delete_file_removes_file_in_workspace(tmp_path):
    from src.tool_execution import _do_delete_file

    target = tmp_path / "package.json"
    target.write_text('{"name": "test"}', encoding="utf-8")
    ws = str(tmp_path)

    with patch("src.tool_execution._resolve_tool_path_in_workspace", side_effect=lambda _ws, p: str(tmp_path / p)):
        result = await _do_delete_file('{"path": "package.json"}', workspace=ws)

    assert result["exit_code"] == 0
    assert result.get("deleted") is True
    assert not target.exists()


@pytest.mark.asyncio
async def test_delete_file_non_empty_dir_requires_recursive(tmp_path):
    from src.tool_execution import _do_delete_file

    sub = tmp_path / "nested"
    sub.mkdir()
    (sub / "a.txt").write_text("x", encoding="utf-8")
    ws = str(tmp_path)

    with patch("src.tool_execution._resolve_tool_path_in_workspace", side_effect=lambda _ws, p: str(tmp_path / p)):
        result = await _do_delete_file('{"path": "nested"}', workspace=ws)

    assert result["exit_code"] == 1
    assert "recursive" in result["error"].lower()
    assert sub.exists()


@pytest.mark.asyncio
async def test_delete_file_recursive_removes_directory(tmp_path):
    from src.tool_execution import _do_delete_file

    sub = tmp_path / "nested"
    sub.mkdir()
    (sub / "a.txt").write_text("x", encoding="utf-8")
    ws = str(tmp_path)

    with patch("src.tool_execution._resolve_tool_path_in_workspace", side_effect=lambda _ws, p: str(tmp_path / p)):
        result = await _do_delete_file('{"path": "nested", "recursive": true}', workspace=ws)

    assert result["exit_code"] == 0
    assert not sub.exists()
