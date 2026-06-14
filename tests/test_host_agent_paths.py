"""Tests for host agent path containment."""

import os
import tempfile
import unittest

from src.host_agent_paths import path_under_root, resolve_under_root


class HostAgentPathTests(unittest.TestCase):
    def test_path_under_root_accepts_child(self):
        with tempfile.TemporaryDirectory() as root:
            child = os.path.join(root, "proj")
            os.makedirs(child)
            self.assertTrue(path_under_root(root, child))

    def test_path_under_root_rejects_outside(self):
        with tempfile.TemporaryDirectory() as root:
            with tempfile.TemporaryDirectory() as other:
                self.assertFalse(path_under_root(root, other))

    def test_resolve_under_root_allows_relative(self):
        with tempfile.TemporaryDirectory() as root:
            child = os.path.join(root, "app")
            os.makedirs(child)
            resolved = resolve_under_root(root, "app")
            self.assertEqual(os.path.realpath(resolved), os.path.realpath(child))

    def test_path_under_root_windows_style(self):
        root = "C:/Users/compc/Desktop"
        child = "C:/Users/compc/Desktop/Prometheus Test"
        self.assertTrue(path_under_root(root, child))

    def test_resolve_under_root_windows_style(self):
        resolved = resolve_under_root(
            "C:/Users/compc/Desktop",
            "C:/Users/compc/Desktop/Prometheus Test",
        )
        self.assertEqual(resolved, "C:/Users/compc/Desktop/Prometheus Test")

    def test_resolve_under_root_rejects_escape(self):
        with tempfile.TemporaryDirectory() as root:
            outside = os.path.join(os.path.dirname(root), "outside-odysseus-host-agent-test")
            with self.assertRaises(ValueError):
                resolve_under_root(root, outside)


if __name__ == "__main__":
    unittest.main()
