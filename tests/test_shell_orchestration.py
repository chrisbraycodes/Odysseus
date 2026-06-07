"""Tests for automatic shell orchestration."""

import json
import os
import tempfile
import unittest

from src.shell_orchestration import (
    cwd_system_note,
    detect_auto_shell_command,
    extract_shell_command,
    resolve_effective_workspace,
)


class ShellOrchestrationTests(unittest.TestCase):
    def test_extract_npm_start_from_noisy_message(self):
        cmd = extract_shell_command("this is /batman run npm start")
        self.assertEqual(cmd.lower(), "npm start")

    def test_detect_pwd(self):
        self.assertEqual(detect_auto_shell_command("pwd"), "pwd")

    def test_resolve_batman_subproject(self):
        with tempfile.TemporaryDirectory() as parent:
            batman = os.path.join(parent, "batman")
            os.mkdir(batman)
            with open(os.path.join(batman, "package.json"), "w", encoding="utf-8") as f:
                json.dump({"name": "batman"}, f)
            ws = resolve_effective_workspace(parent, "npm start in batman", "")
            self.assertTrue(ws.endswith("batman"))

    def test_cwd_note_mentions_no_pwd(self):
        note = cwd_system_note("/workspace/test workspace/batman")
        self.assertIn("do NOT run pwd", note)
        self.assertIn("batman", note)


if __name__ == "__main__":
    unittest.main()
