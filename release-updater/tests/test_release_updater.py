from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "release_updater.py"
SPEC = importlib.util.spec_from_file_location("release_updater", SCRIPT)
assert SPEC and SPEC.loader
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)


class ReleaseUpdaterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.env = mock.patch.dict(
            os.environ,
            {"MODA_RELEASE_UPDATER_STATE_DIR": self.temporary.name},
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_daily_check_is_recorded_once(self) -> None:
        state = updater.load_state()
        self.assertFalse(updater.checked_today(state))
        updater.record_check(state, status="ok")
        self.assertTrue(updater.checked_today(updater.load_state()))

    def test_checked_today_does_not_call_github_again(self) -> None:
        state = updater.load_state()
        updater.record_check(state, status="ok")
        with mock.patch.object(updater, "latest_release") as latest:
            result = updater.perform_check(Path(self.temporary.name), prompt=False)
        self.assertEqual(result["status"], "already_checked")
        latest.assert_not_called()

    def test_skip_and_unskip_version(self) -> None:
        state = updater.load_state()
        updater.save_skipped(state, "v1.2.3")
        self.assertIn("v1.2.3", updater.load_state()["skipped_tags"])
        self.assertTrue(updater.unskip("v1.2.3"))
        self.assertNotIn("v1.2.3", updater.load_state()["skipped_tags"])

    def test_skipped_release_does_not_open_prompt(self) -> None:
        state = updater.load_state()
        updater.save_skipped(state, "v1.2.3")
        release = {
            "tag_name": "v1.2.3",
            "name": "Moda 1.2.3",
            "published_at": "2026-08-08T10:00:00Z",
            "body": "Changes",
        }
        with (
            mock.patch.object(updater, "latest_release", return_value=release),
            mock.patch.object(updater, "show_release_prompt") as prompt,
        ):
            result = updater.perform_check(Path(self.temporary.name), prompt=True, force=True)
        self.assertEqual(result["status"], "skipped")
        prompt.assert_not_called()

    def test_release_summary_contains_change_body(self) -> None:
        summary = updater.release_summary(
            {
                "tag_name": "v2.0.0",
                "name": "Moda 2.0",
                "published_at": "2026-08-08T10:00:00Z",
                "body": "## Changes\n- Faster checks",
            }
        )
        self.assertIn("v2.0.0", summary)
        self.assertIn("2026-08-08", summary)
        self.assertIn("Faster checks", summary)

    def test_dirty_git_checkout_blocks_upgrade(self) -> None:
        target = Path(self.temporary.name) / "repo"
        target.mkdir()
        with (
            mock.patch.object(updater, "target_matches_repo", return_value=True),
            mock.patch.object(updater, "git", return_value=" M SKILL.md"),
        ):
            with self.assertRaises(updater.UpgradeBlocked):
                updater.upgrade_git(target, "v1.0.0")

    def test_hook_install_preserves_other_groups_and_replaces_managed_group(self) -> None:
        hooks_path = Path(self.temporary.name) / "hooks.json"
        hooks_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {"hooks": [{"type": "command", "command": "keep-me"}]},
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "python old/release_updater.py session-start",
                                    }
                                ]
                            },
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        updater.install_hook(SCRIPT, Path(self.temporary.name), hooks_path)
        payload = json.loads(hooks_path.read_text(encoding="utf-8"))
        groups = payload["hooks"]["SessionStart"]
        commands = [hook["command"] for group in groups for hook in group["hooks"]]
        self.assertIn("keep-me", commands)
        managed = [
            command
            for command in commands
            if updater.MANAGED_SCRIPT_MARKER in command
            and updater.MANAGED_COMMAND_MARKER in command
        ]
        self.assertEqual(len(managed), 1)


if __name__ == "__main__":
    unittest.main()
