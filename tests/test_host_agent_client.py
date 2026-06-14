"""Tests for host agent client path mapping."""

import unittest
from unittest import mock

from src.host_agent_client import host_path_for_agent, workspace_host_paths


class HostAgentClientPathTests(unittest.TestCase):
    def test_host_path_for_agent_preserves_drive(self):
        self.assertEqual(
            host_path_for_agent(r"C:\Users\compc\Desktop\Prometheus Test"),
            "C:/Users/compc/Desktop/Prometheus Test",
        )

    @mock.patch("src.host_agent_client.container_path_to_host", return_value=r"C:\Users\compc\Desktop\Prometheus Test")
    def test_workspace_host_paths_no_container_realpath(self, _):
        container, host = workspace_host_paths("/workspace/Prometheus Test")
        self.assertEqual(container, "/workspace/Prometheus Test")
        self.assertEqual(host, "C:/Users/compc/Desktop/Prometheus Test")
        self.assertNotIn("/app/C:", host)


if __name__ == "__main__":
    unittest.main()
