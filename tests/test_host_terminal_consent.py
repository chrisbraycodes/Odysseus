"""Tests for host terminal consent persistence."""

import unittest

from src.host_terminal_consent import _workspace_paths_match, host_terminal_enabled, save_consent, clear_consent


class HostTerminalConsentTests(unittest.TestCase):
    def setUp(self):
        clear_consent()

    def tearDown(self):
        clear_consent()

    def test_workspace_path_match_container_aliases(self):
        self.assertTrue(_workspace_paths_match("/workspace/proj", "/workspace/proj/"))
        self.assertTrue(_workspace_paths_match("/workspace", "/workspace/"))

    def test_enabled_for_matching_workspace_only(self):
        save_consent(
            accepted=True,
            unrestricted=False,
            workspace_path="/workspace/app",
        )
        self.assertTrue(host_terminal_enabled("/workspace/app"))
        self.assertFalse(host_terminal_enabled("/workspace/other"))

    def test_unrestricted_not_required_for_enable(self):
        saved = save_consent(
            accepted=True,
            unrestricted=False,
            workspace_path="/workspace/app",
        )
        self.assertFalse(saved["unrestricted"])
        self.assertTrue(host_terminal_enabled("/workspace/app"))

    def test_shell_preference_persisted(self):
        from src.host_terminal_consent import host_terminal_shell, normalize_host_shell

        save_consent(
            accepted=True,
            unrestricted=False,
            workspace_path="/workspace/app",
            shell="cmd",
        )
        self.assertEqual(host_terminal_shell(), "cmd")
        self.assertEqual(normalize_host_shell("CMD.EXE"), "cmd")
        self.assertEqual(normalize_host_shell("powershell"), "powershell")


if __name__ == "__main__":
    unittest.main()
