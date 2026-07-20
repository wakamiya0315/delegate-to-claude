from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "skill" / "delegate-to-claude" / "scripts" / "delegate.py"


FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path

if "--version" in sys.argv:
    print("2.1.215 (Claude Code)")
    raise SystemExit(0)

capture = os.environ.get("FAKE_CAPTURE_ARGS")
if capture:
    Path(capture).write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
capture_settings = os.environ.get("FAKE_CAPTURE_SETTINGS")
if capture_settings:
    settings_path = Path(sys.argv[sys.argv.index("--settings") + 1])
    Path(capture_settings).write_text(settings_path.read_text(encoding="utf-8"), encoding="utf-8")
worker_prompt = sys.stdin.read()
capture_prompt = os.environ.get("FAKE_CAPTURE_PROMPT")
if capture_prompt:
    Path(capture_prompt).write_text(worker_prompt, encoding="utf-8")
marker = os.environ.get("FAKE_RUN_MARKER")
if marker:
    Path(marker).write_text("called", encoding="utf-8")

mode = os.environ.get("FAKE_CLAUDE_MODE", "success")
if mode == "sleep":
    time.sleep(5)
if mode == "failure":
    print("required sandbox unavailable", file=sys.stderr)
    raise SystemExit(7)
if mode == "malformed":
    print("not-json")
    raise SystemExit(0)

edit_file = os.environ.get("FAKE_EDIT_FILE")
if edit_file:
    path = Path(edit_file)
    path.write_text(path.read_text(encoding="utf-8") + "worker edit\n", encoding="utf-8")

structured = {
    "status": "completed",
    "summary": "WORKER_BODY_SECRET completed the task",
    "changed_files": ["worker-reported.txt"],
    "tests": [
        {"command": "python3 -m unittest", "outcome": "passed", "details": "WORKER_TEST_SECRET"}
    ],
    "concerns": [],
    "recommended_next_action": "Inspect the diff."
}
print(json.dumps({
    "type": "result",
    "subtype": "success",
    "structured_output": structured,
    "total_cost_usd": 0.012,
    "duration_ms": 40,
    "num_turns": 2,
    "usage": {"input_tokens": 10, "output_tokens": 20},
    "session_id": "SECRET_SESSION_ID"
}))
'''


class DelegateLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.run_cmd(["git", "init", "-q"], cwd=self.repo)
        self.run_cmd(["git", "config", "user.email", "tests@example.invalid"], cwd=self.repo)
        self.run_cmd(["git", "config", "user.name", "Tests"], cwd=self.repo)
        self.target = self.repo / "target.txt"
        self.target.write_text("baseline\n", encoding="utf-8")
        self.run_cmd(["git", "add", "target.txt"], cwd=self.repo)
        self.run_cmd(["git", "commit", "-qm", "baseline"], cwd=self.repo)

        self.task = self.base / "task.md"
        self.task.write_text(
            textwrap.dedent(
                """\
                # Goal
                TASK_BODY_SECRET make the bounded change.
                # Allowed scope
                target.txt only.
                # Acceptance criteria
                Preserve the baseline and append one line.
                # Required checks
                Inspect target.txt.
                # Existing user changes to preserve
                None.
                # Forbidden actions
                No network, commit, or push.
                """
            ),
            encoding="utf-8",
        )
        self.fake = self.base / "claude"
        self.fake.write_text(FAKE_CLAUDE, encoding="utf-8")
        self.fake.chmod(0o755)
        self.cache = self.base / "cache"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cmd(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            cwd=cwd,
            env=env,
            check=check,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def launcher_env(self, **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        env.pop("CODEX_SANDBOX", None)
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_CODE_CHILD_SESSION", None)
        env.update(
            {
                "DELEGATE_TO_CLAUDE_BIN": str(self.fake),
                "DELEGATE_TO_CLAUDE_TESTING": "1",
                "DELEGATE_TO_CLAUDE_CACHE_DIR": str(self.cache),
                "DELEGATE_TO_CLAUDE_SESSION_ENV_ROOT": str(self.base / "session-env"),
            }
        )
        env.update(extra)
        return env

    def invoke(
        self,
        *,
        mode: str = "edit",
        effort: str = "medium",
        env: dict[str, str] | None = None,
        dry_run: bool = False,
        quick_prompt: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(LAUNCHER),
            "--cwd",
            str(self.repo),
        ]
        if quick_prompt is None:
            command.extend(["--task-file", str(self.task)])
        else:
            command.extend(["--prompt", quick_prompt])
        command.extend(["--mode", mode, "--effort", effort])
        if dry_run:
            command.append("--dry-run")
        return self.run_cmd(command, env=env or self.launcher_env(), check=False)

    def test_success_normalizes_git_changes_and_redacts_receipt(self) -> None:
        args_file = self.base / "args.json"
        result = self.invoke(
            env=self.launcher_env(
                FAKE_EDIT_FILE=str(self.target), FAKE_CAPTURE_ARGS=str(args_file)
            )
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["changed_files"], ["target.txt"])
        self.assertTrue(any("differed" in item for item in payload["concerns"]))

        args = json.loads(args_file.read_text(encoding="utf-8"))
        self.assertIn("--disable-slash-commands", args)
        self.assertIn("--strict-mcp-config", args)
        self.assertIn("--json-schema", args)
        self.assertEqual(args[args.index("--model") + 1], "sonnet")
        self.assertEqual(args[args.index("--max-turns") + 1], "12")

        receipt_text = (self.cache / "runs.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("TASK_BODY_SECRET", receipt_text)
        self.assertNotIn("WORKER_BODY_SECRET", receipt_text)
        self.assertNotIn("WORKER_TEST_SECRET", receipt_text)
        self.assertNotIn("SECRET_SESSION_ID", receipt_text)
        receipt = json.loads(receipt_text)
        self.assertEqual(receipt["input_style"], "strict")
        self.assertEqual(receipt["changed_files"], ["target.txt"])
        self.assertEqual(receipt["tests"], [{"command": "python3 -m unittest", "outcome": "passed"}])
        self.assertEqual(receipt["usage"]["num_turns"], 2)

    def test_nonzero_sandbox_failure_is_not_accepted(self) -> None:
        result = self.invoke(env=self.launcher_env(FAKE_CLAUDE_MODE="failure"))
        self.assertEqual(result.returncode, 3)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "failed")
        self.assertIn("sandbox failed", payload["concerns"][0])

    def test_malformed_json_is_rejected(self) -> None:
        result = self.invoke(env=self.launcher_env(FAKE_CLAUDE_MODE="malformed"))
        self.assertEqual(result.returncode, 3)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "failed")
        self.assertIn("malformed JSON", payload["concerns"][0])

    def test_timeout_terminates_worker(self) -> None:
        result = self.invoke(
            env=self.launcher_env(
                FAKE_CLAUDE_MODE="sleep",
                DELEGATE_TO_CLAUDE_TIMEOUT_SECONDS="0.1",
            )
        )
        self.assertEqual(result.returncode, 124)
        payload = json.loads(result.stdout)
        self.assertIn("timed out", payload["concerns"][0])

    def test_dry_run_does_not_start_worker_or_write_receipt(self) -> None:
        marker = self.base / "worker-called"
        result = self.invoke(
            env=self.launcher_env(FAKE_RUN_MARKER=str(marker)), dry_run=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())
        self.assertFalse((self.cache / "runs.jsonl").exists())

    def test_review_mode_fails_if_worker_changes_repository(self) -> None:
        result = self.invoke(
            mode="review", env=self.launcher_env(FAKE_EDIT_FILE=str(self.target))
        )
        self.assertEqual(result.returncode, 3)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "failed")
        self.assertIn("target.txt", payload["changed_files"])
        self.assertTrue(any("Review mode changed" in item for item in payload["concerns"]))

    def test_nested_supervisor_disables_bash_and_claude_sandbox(self) -> None:
        args_file = self.base / "nested-args.json"
        settings_file = self.base / "nested-settings.json"
        result = self.invoke(
            env=self.launcher_env(
                CODEX_SANDBOX="seatbelt",
                FAKE_CAPTURE_ARGS=str(args_file),
                FAKE_CAPTURE_SETTINGS=str(settings_file),
                FAKE_EDIT_FILE=str(self.target),
            )
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(any("Bash was disabled" in item for item in payload["concerns"]))
        self.assertTrue(all(test["outcome"] == "not_run" for test in payload["tests"]))

        args = json.loads(args_file.read_text(encoding="utf-8"))
        self.assertNotIn("Bash", args[args.index("--tools") + 1].split(","))
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        self.assertEqual(settings["sandbox"], {"enabled": False})

    def test_quick_prompt_synthesizes_safe_brief_and_redacts_receipt(self) -> None:
        prompt_file = self.base / "worker-prompt.txt"
        result = self.invoke(
            quick_prompt="QUICK_TASK_SECRET add a focused parser and its tests.",
            env=self.launcher_env(FAKE_CAPTURE_PROMPT=str(prompt_file)),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        worker_prompt = prompt_file.read_text(encoding="utf-8")
        for heading in (
            "# Goal",
            "# Allowed scope",
            "# Acceptance criteria",
            "# Required checks",
            "# Existing user changes to preserve",
            "# Forbidden actions",
        ):
            self.assertIn(heading, worker_prompt)
        self.assertIn("QUICK_TASK_SECRET", worker_prompt)
        self.assertIn("minimal implementation", worker_prompt)
        self.assertIn("Do not add or update dependencies", worker_prompt)
        self.assertIn("(clean working tree)", worker_prompt)

        receipt_text = (self.cache / "runs.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("QUICK_TASK_SECRET", receipt_text)
        receipt = json.loads(receipt_text)
        self.assertEqual(receipt["input_style"], "quick")
        self.assertEqual(receipt["model"], "sonnet")

    def test_quick_prompt_rejects_empty_text(self) -> None:
        result = self.invoke(quick_prompt="   ")

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertIn("must not be empty", payload["concerns"][0])

    def test_quick_prompt_rejects_more_than_32_kib(self) -> None:
        result = self.invoke(quick_prompt="x" * (32 * 1024 + 1))

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertIn("32 KiB", payload["concerns"][0])

    def test_prompt_and_task_file_are_mutually_exclusive(self) -> None:
        result = self.run_cmd(
            [
                sys.executable,
                str(LAUNCHER),
                "--cwd",
                str(self.repo),
                "--task-file",
                str(self.task),
                "--prompt",
                "do the work",
                "--mode",
                "edit",
                "--effort",
                "medium",
            ],
            env=self.launcher_env(),
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("not allowed with argument", result.stderr)


if __name__ == "__main__":
    unittest.main()
