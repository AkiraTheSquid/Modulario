import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import stop_gate

watch_spec = importlib.util.spec_from_file_location(
    "modulario_watch_runner", ROOT / "scripts" / "modulario-watch-runner.py"
)
watch_runner = importlib.util.module_from_spec(watch_spec)
watch_spec.loader.exec_module(watch_runner)


class ScopedGateTests(unittest.TestCase):
    def test_cycle_requires_scope_intersection(self):
        state = {"violations": {"cycles": [
            {"files": ["a.py", "b.py"]},
            {"files": ["x.py", "y.py"]},
        ]}}
        cycles = stop_gate._scoped_cycles(state, {"b.py"})
        self.assertEqual(cycles, [{"files": ["a.py", "b.py"]}])

    def test_oversized_file_outside_scope_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            in_scope = target / "in.py"
            unrelated = target / "unrelated.py"
            in_scope.write_text("\n".join(f"x{i} = {i}" for i in range(10)), encoding="utf-8")
            unrelated.write_text("\n".join(f"x{i} = {i}" for i in range(30)), encoding="utf-8")
            scope = {
                "target": str(target),
                "scope_files": {"in.py"},
                "state": {"files": [
                    {"path": "in.py", "abs_path": str(in_scope), "loc": 10},
                    {"path": "unrelated.py", "abs_path": str(unrelated), "loc": 30},
                ]},
            }
            self.assertEqual(stop_gate._scoped_oversized(scope, 20), [])

    def test_watch_outside_scope_does_not_run(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            good = target / "good"
            bad = target / "bad"
            good.mkdir()
            bad.mkdir()
            (good / "watch.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
            (bad / "watch.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
            results = watch_runner.scan_and_run(str(target), scope_folders={"good"})
            self.assertEqual(results, [("PASS", "good/", "")])


if __name__ == "__main__":
    unittest.main()
