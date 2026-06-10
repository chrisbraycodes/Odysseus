"""Plan execution: auto-shell extraction, verification, false-completion recovery."""

import json
import os
import tempfile
import unittest

from src.plan_execution import (
    detect_false_completion_command,
    extract_next_plan_shell_command,
    resolve_scaffold_cwd,
    verify_plan_checkmarks,
)


class PlanExecutionTests(unittest.TestCase):
    def test_extract_npx_from_unchecked_plan(self):
        plan = "- [x] done\n- [ ] Run `npx create-react-app batman`\n- [ ] cd batman"
        self.assertEqual(
            extract_next_plan_shell_command(plan),
            "npx create-react-app batman",
        )

    def test_detect_false_completion_from_prose(self):
        prose = "Ran `npx create-react-app batman` to scaffold the app."
        self.assertEqual(
            detect_false_completion_command(prose),
            "npx create-react-app batman",
        )

    def test_verify_rejects_cra_without_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = "- [x] Run npx create-react-app batman\n- [ ] cd batman"
            ok, err = verify_plan_checkmarks(plan, tmp)
            self.assertFalse(ok)
            self.assertIn("package.json", err)

    def test_verify_accepts_cra_when_folder_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = os.path.join(tmp, "batman")
            os.mkdir(proj)
            with open(os.path.join(proj, "package.json"), "w", encoding="utf-8") as f:
                json.dump({"name": "batman"}, f)
            plan = "- [x] Run npx create-react-app batman\n- [ ] cd batman"
            ok, err = verify_plan_checkmarks(plan, tmp)
            self.assertTrue(ok)
            self.assertEqual(err, "")

    def test_resolve_scaffold_cwd_redirects_data_to_workspace(self):
        from src.constants import DATA_DIR

        if not os.path.isdir("/workspace"):
            self.skipTest("/workspace not mounted")
        resolved = resolve_scaffold_cwd(None, DATA_DIR)
        self.assertEqual(resolved, "/workspace")


if __name__ == "__main__":
    unittest.main()
