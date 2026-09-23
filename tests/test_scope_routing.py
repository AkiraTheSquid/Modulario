import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import hook_scope
import session_scope
from project_registry import find_project_for_path, rank_projects


class ProjectRegistryTests(unittest.TestCase):
    def test_deepest_project_wins(self):
        projects = [
            {"target_dir": "/work", "name": "root", "display_name": "Root", "priority": 0},
            {"target_dir": "/work/nested", "name": "nested", "display_name": "Nested", "priority": 0},
        ]
        found = find_project_for_path("/work/nested/src/a.py", projects)
        self.assertEqual(found["name"], "nested")

    def test_history_ranks_before_cold_priority(self):
        projects = [
            {"target_dir": "/a", "display_name": "A", "priority": 100},
            {"target_dir": "/b", "display_name": "B", "priority": 1},
        ]
        ranked = rank_projects(projects, {"/b": {"uses": 2, "last_used": 10}})
        self.assertEqual(ranked[0]["target_dir"], "/b")


class HookScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.target = Path(self.temp.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.target)], check=True)
        subprocess.run(["git", "-C", str(self.target), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.target), "config", "user.name", "Test"], check=True)
        (self.target / "app.py").write_text("value = 1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.target), "add", "app.py"], check=True)
        subprocess.run(["git", "-C", str(self.target), "commit", "-qm", "init"], check=True)
        self.projects = [{
            "target_dir": str(self.target),
            "name": "test",
            "display_name": "Test",
            "priority": 0,
        }]

    def tearDown(self):
        self.temp.cleanup()

    def _payload(self, tool_input=None, tool_name="Bash"):
        return {
            "session_id": "session-1",
            "turn_id": "turn-1",
            "tool_use_id": "tool-1",
            "model": "gpt-test",
            "cwd": str(self.target),
            "tool_name": tool_name,
            "tool_input": tool_input or {"cmd": "true"},
        }

    def test_apply_patch_paths_are_extracted(self):
        payload = self._payload(
            {"patch": "*** Begin Patch\n*** Update File: app.py\n*** End Patch"},
            tool_name="apply_patch",
        )
        self.assertEqual(hook_scope.extract_direct_paths(payload), [str(self.target / "app.py")])

    def test_git_snapshot_detects_change_to_already_dirty_file(self):
        path = self.target / "app.py"
        path.write_text("value = 2\n", encoding="utf-8")
        payload = self._payload()
        hook_scope.capture_before(payload, self.projects)
        path.write_text("value = 3\n", encoding="utf-8")
        changes = hook_scope.collect_changes(payload, self.projects)
        self.assertEqual(changes[str(self.target)], [str(path)])

    def test_tool_workdir_routes_bash_when_session_cwd_is_elsewhere(self):
        payload = self._payload({"cmd": "true", "workdir": str(self.target)})
        payload["cwd"] = "/tmp"
        selected = hook_scope.candidate_projects(payload, self.projects)
        self.assertEqual([p["target_dir"] for p in selected], [str(self.target)])

    def test_outside_project_edit_is_ignored(self):
        payload = self._payload({"file_path": "/tmp/not-registered.py"}, tool_name="Write")
        self.assertEqual(hook_scope.collect_changes(payload, self.projects), {})


class SessionScopeTests(unittest.TestCase):
    def test_provider_isolation_and_one_hop_graph(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "project"
            target.mkdir()
            for rel in ("pkg/a.py", "pkg/b.py", "core/c.py", "far/d.py"):
                path = target / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            state_path = Path(temp) / "state.json"
            state_path.write_text("{}", encoding="utf-8")
            session_scope.update_session_scope(
                state_path, target, [target / "pkg/b.py"], "same", "codex"
            )
            self.assertIsNone(session_scope.load_session_scope(state_path, "same", "claude"))
            entry = session_scope.load_session_scope(state_path, "same", "codex")
            files, folders = session_scope.compute_graph_scope(
                entry["files"],
                {
                    "pkg/a.py": ["pkg/b.py"],
                    "pkg/b.py": ["core/c.py"],
                    "far/d.py": ["pkg/a.py"],
                },
            )
            self.assertEqual(files, {"pkg/a.py", "pkg/b.py", "core/c.py"})
            self.assertNotIn("far/d.py", files)
            self.assertTrue({"", "pkg", "core"} <= folders)


if __name__ == "__main__":
    unittest.main()
