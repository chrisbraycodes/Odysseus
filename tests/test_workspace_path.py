"""Tests for workspace path resolution."""

import os
import unittest
from unittest import mock

from src.workspace_path import display_workspace_path, resolve_workspace_path


class WorkspacePathTests(unittest.TestCase):
    @mock.patch("src.workspace_path.docker_workspace_available", return_value=True)
    def test_posix_under_workspace(self, _):
        with mock.patch("os.path.isdir", return_value=True), mock.patch(
            "os.path.realpath", side_effect=lambda p: p
        ):
            p = resolve_workspace_path("/workspace/test workspace")
            self.assertEqual(p, "/workspace/test workspace")

    @mock.patch("src.workspace_path.docker_workspace_available", return_value=True)
    def test_windows_desktop_maps_to_workspace(self, _):
        raw = r"C:\Users\compc\Desktop\test workspace"
        expected = "/workspace/test workspace"

        def _isdir(p):
            return p.replace("\\", "/") in (expected, "/workspace")

        with mock.patch("os.path.isdir", side_effect=_isdir), mock.patch(
            "os.path.realpath", side_effect=lambda p: p.replace("\\", "/")
        ):
            self.assertEqual(resolve_workspace_path(raw), expected)

    @mock.patch("src.workspace_path.docker_workspace_available", return_value=True)
    def test_display_workspace_path_in_container(self, _):
        with mock.patch("os.path.realpath", side_effect=lambda p: p.replace("\\", "/")):
            shown = display_workspace_path("/workspace/Odysseus/odysseus")
        self.assertEqual(shown, "/workspace/Odysseus/odysseus")


if __name__ == "__main__":
    unittest.main()
