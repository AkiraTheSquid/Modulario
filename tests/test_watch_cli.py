import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cli_watch

watch_spec = importlib.util.spec_from_file_location(
    "modulario_watch_runner_cli", ROOT / "scripts" / "modulario-watch-runner.py"
)
watch_runner = importlib.util.module_from_spec(watch_spec)
watch_spec.loader.exec_module(watch_runner)


class WatchRunnerOutputTests(unittest.TestCase):
    def test_failure_reason_prefers_explicit_fail_line(self):
        reason = watch_runner._failure_reason(
            "Traceback (most recent call last):\nFAIL check_contract: missing export\n",
            "",
        )
        self.assertEqual(reason, "check_contract: missing export")

    def test_failure_reason_uses_last_traceback_line(self):
        reason = watch_runner._failure_reason(
            "Traceback (most recent call last):\nAssertionError: missing export\n",
            "",
        )
        self.assertEqual(reason, "AssertionError: missing export")

    def test_summary_counts_results(self):
        results = [("PASS", "good/", ""), ("FAIL", "bad/", "broken")]
        self.assertEqual(watch_runner.format_summary(results), "2 watches ran: 1 PASS, 1 FAIL")

    def test_summary_is_opt_in_so_hook_output_stays_quiet(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            (target / "watch.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
            base = [
                sys.executable,
                str(ROOT / "scripts" / "modulario-watch-runner.py"),
                "--target",
                str(target),
                "--print-plain",
            ]
            quiet = subprocess.run(base, capture_output=True, text=True, check=False)
            shown = subprocess.run(
                [*base, "--show-summary"], capture_output=True, text=True, check=False
            )
        self.assertEqual((quiet.returncode, quiet.stdout), (0, ""))
        self.assertEqual(shown.returncode, 0)
        self.assertEqual(shown.stdout.strip(), "1 watch ran: 1 PASS, 0 FAIL")

    def test_hook_json_passes_through_and_merges_failures(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            watch = target / "watch.py"
            command = [
                sys.executable,
                str(ROOT / "scripts" / "modulario-watch-runner.py"),
                "--target",
                str(target),
            ]
            payload = {
                "_scope_folders": [""],
                "hookSpecificOutput": {"additionalContext": "base context"},
            }
            watch.write_text("raise SystemExit(0)\n", encoding="utf-8")
            passed = subprocess.run(
                command,
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                check=False,
            )
            watch.write_text(
                "import sys\nprint('FAIL check_contract: broke', file=sys.stderr)\n"
                "raise SystemExit(1)\n",
                encoding="utf-8",
            )
            failed = subprocess.run(
                command,
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                check=False,
            )
        passed_json = json.loads(passed.stdout)
        failed_json = json.loads(failed.stdout)
        self.assertEqual(passed.returncode, 0)
        self.assertNotIn("_scope_folders", passed_json)
        self.assertEqual(
            passed_json["hookSpecificOutput"]["additionalContext"], "base context"
        )
        self.assertEqual(failed.returncode, 0)
        self.assertEqual(
            failed_json["hookSpecificOutput"]["additionalContext"],
            "base context\n[WATCH] /: FAIL — check_contract: broke",
        )


class WatchCliTests(unittest.TestCase):
    def test_cmd_run_propagates_runner_failure(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=1,
            stdout="1 watches ran: 0 PASS, 1 FAIL\n[WATCH] bad/: FAIL — broken\n",
            stderr="",
        )
        output = io.StringIO()
        with mock.patch.object(cli_watch.subprocess, "run", return_value=completed):
            with contextlib.redirect_stdout(output):
                with self.assertRaisesRegex(SystemExit, "1"):
                    cli_watch._cmd_run("/tmp/project")
        self.assertIn("0 PASS, 1 FAIL", output.getvalue())


if __name__ == "__main__":
    unittest.main()
