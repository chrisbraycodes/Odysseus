"""Tests for automatic workspace Node/dev-server preparation."""

import json
import os
import tempfile
import unittest
from unittest import mock

from src.workspace_dev import (
    dev_server_preview_url,
    dev_server_run_on_host,
    inject_dev_server_env,
    npm_deps_missing,
    prepare_node_workspace_command,
)


class WorkspaceDevTests(unittest.TestCase):
    def test_npm_deps_missing_without_node_modules(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "package.json"), "w", encoding="utf-8") as f:
                json.dump({"scripts": {"start": "react-scripts start"}}, f)
            self.assertTrue(npm_deps_missing(d))

    def test_npm_deps_present_with_bin(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "package.json"), "w", encoding="utf-8") as f:
                json.dump({"scripts": {"start": "react-scripts start"}}, f)
            os.makedirs(os.path.join(d, "node_modules", ".bin"))
            open(os.path.join(d, "node_modules", ".bin", "react-scripts"), "w").close()
            self.assertFalse(npm_deps_missing(d))

    @mock.patch("src.workspace_dev.docker_workspace_mounted", return_value=True)
    @mock.patch("src.workspace_dev.dev_exec_target", return_value="container")
    def test_inject_react_env_in_container_mode(self, *_):
        out = inject_dev_server_env("npm start")
        self.assertIn("HOST=0.0.0.0", out)
        self.assertIn("BROWSER=none", out)

    @mock.patch("src.workspace_dev.docker_workspace_mounted", return_value=True)
    @mock.patch("src.workspace_dev.dev_exec_target", return_value="host")
    def test_no_inject_in_host_mode(self, *_):
        out = inject_dev_server_env("npm start")
        self.assertEqual(out, "npm start")

    @mock.patch("src.workspace_dev.docker_workspace_mounted", return_value=True)
    @mock.patch("src.workspace_dev.dev_exec_target", return_value="container")
    def test_prepare_prepends_npm_install_in_container_mode(self, *_):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "package.json"), "w", encoding="utf-8") as f:
                json.dump({"scripts": {"start": "react-scripts start"}}, f)
            cmd, url, run_on_host = prepare_node_workspace_command("npm start", d)
            self.assertIn("npm install &&", cmd)
            self.assertIn("HOST=0.0.0.0", cmd)
            self.assertEqual(url, "http://127.0.0.1:3000/")
            self.assertFalse(run_on_host)

    @mock.patch("src.workspace_dev.docker_workspace_mounted", return_value=True)
    @mock.patch("src.workspace_dev.dev_exec_target", return_value="host")
    def test_prepare_dev_server_runs_on_host(self, *_):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "package.json"), "w", encoding="utf-8") as f:
                json.dump({"scripts": {"start": "react-scripts start"}}, f)
            cmd, url, run_on_host = prepare_node_workspace_command("npm start", d)
            self.assertEqual(cmd, "npm start")
            self.assertTrue(run_on_host)
            self.assertTrue(dev_server_run_on_host())

    @mock.patch("src.workspace_dev.docker_workspace_mounted", return_value=True)
    def test_vite_preview_port(self, _):
        url = dev_server_preview_url("vite")
        self.assertEqual(url, "http://127.0.0.1:5173/")


if __name__ == "__main__":
    unittest.main()
