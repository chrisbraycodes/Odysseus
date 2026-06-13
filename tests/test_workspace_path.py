"""Tests for workspace path resolution."""

import os
import unittest
from unittest import mock

from src.workspace_path import (
    container_path_to_host,
    display_workspace_path,
    host_path_to_container,
    resolve_workspace_path,
)


class WorkspacePathTests(unittest.TestCase):
    @mock.patch("src.workspace_path.docker_workspace_available", return_value=True)
    @mock.patch("src.workspace_path.workspace_host_root", return_value="")
    def test_posix_under_workspace(self, *_):
        with mock.patch("os.path.isdir", return_value=True), mock.patch(
            "os.path.realpath", side_effect=lambda p: p
        ):
            p = resolve_workspace_path("/workspace/test workspace")
            self.assertEqual(p, "/workspace/test workspace")

    @mock.patch("src.workspace_path.docker_workspace_available", return_value=True)
    @mock.patch("src.workspace_path.workspace_host_root", return_value="")
    def test_windows_desktop_maps_to_workspace(self, *_):
        raw = r"C:\Users\alice\Desktop\test workspace"
        expected = "/workspace/test workspace"

        def _isdir(p):
            return p.replace("\\", "/") in (expected, "/workspace")

        with mock.patch("os.path.isdir", side_effect=_isdir), mock.patch(
            "os.path.realpath", side_effect=lambda p: p.replace("\\", "/")
        ):
            self.assertEqual(resolve_workspace_path(raw), expected)

    @mock.patch("src.workspace_path.docker_workspace_available", return_value=True)
    @mock.patch("src.workspace_path.workspace_host_root", return_value=r"C:\Projects")
    def test_host_root_maps_to_container(self, *_):
        raw = r"C:\Projects\my-app"
        expected = "/workspace/my-app"

        def _isdir(p):
            norm = p.replace("\\", "/")
            return norm in (expected, "/workspace")

        with mock.patch("os.path.isdir", side_effect=_isdir), mock.patch(
            "os.path.realpath", side_effect=lambda p: p.replace("\\", "/")
        ):
            self.assertEqual(resolve_workspace_path(raw), expected)
            self.assertEqual(
                host_path_to_container(r"C:\Projects\my-app"),
                expected,
            )

    @mock.patch("src.workspace_path.docker_workspace_available", return_value=True)
    @mock.patch("src.workspace_path.workspace_host_root", return_value=r"C:\Projects")
    def test_container_maps_to_host(self, *_):
        with mock.patch("os.path.realpath", side_effect=lambda p: p.replace("\\", "/")):
            self.assertEqual(
                container_path_to_host("/workspace/my-app"),
                os.path.join(r"C:\Projects", "my-app"),
            )

    @mock.patch("src.workspace_path.docker_workspace_available", return_value=True)
    def test_display_workspace_path_in_container(self, _):
        with mock.patch("os.path.realpath", side_effect=lambda p: p.replace("\\", "/")):
            shown = display_workspace_path("/workspace/Odysseus/odysseus")
        self.assertEqual(shown, "/workspace/Odysseus/odysseus")


if __name__ == "__main__":
    unittest.main()
