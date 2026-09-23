import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tui'))

from core.git_worktree import (
    _dirtiness_level,
    collect_worktree,
    empty_worktree,
    make_checkpoint,
    parse_porcelain,
    scope_progress,
)
from core.tui_state import TuiState
from views.git_worktree_view import (
    _format_table_row,
    _overall_progress,
    worktree_panel_height,
)
from git_test_utils import init_git_repo


class GitWorktreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        init_git_repo(self.repo, {
            'feature_a/a.py': 'value = 1\n',
            'feature_b/b.py': 'value = 1\n',
        })

    def tearDown(self):
        self.temp.cleanup()

    def test_collects_status_counts_and_feature_scopes(self):
        (self.repo / 'feature_a/a.py').write_text('value = 2\n', encoding='utf-8')
        (self.repo / 'feature_b/b.py').write_text('value = 2\n', encoding='utf-8')
        subprocess.run(
            ['git', '-C', str(self.repo), 'add', 'feature_b/b.py'], check=True
        )
        new_path = self.repo / 'feature_c/new.py'
        new_path.parent.mkdir()
        new_path.write_text('new = True\n', encoding='utf-8')

        worktree = collect_worktree(str(self.repo))

        self.assertTrue(worktree['available'])
        self.assertEqual(worktree['total'], 3)
        self.assertEqual(worktree['staged'], 1)
        self.assertEqual(worktree['unstaged'], 1)
        self.assertEqual(worktree['untracked'], 1)
        self.assertEqual(worktree['conflicts'], 0)
        self.assertEqual(
            {scope['name'] for scope in worktree['scopes']},
            {'feature_a', 'feature_b', 'feature_c'},
        )

    def test_scope_progress_compares_current_to_checkpoint(self):
        (self.repo / 'feature_a/a.py').write_text('value = 2\n', encoding='utf-8')
        (self.repo / 'feature_b/b.py').write_text('value = 2\n', encoding='utf-8')
        baseline = make_checkpoint(collect_worktree(str(self.repo)), label='start')
        subprocess.run(
            ['git', '-C', str(self.repo), 'restore', 'feature_a/a.py'], check=True
        )

        rows = scope_progress(collect_worktree(str(self.repo)), baseline)
        by_name = {row['name']: row for row in rows}

        self.assertEqual(by_name['feature_a']['total'], 0)
        self.assertEqual(by_name['feature_a']['baseline'], 1)
        self.assertEqual(by_name['feature_a']['cleaned'], 1)
        self.assertEqual(by_name['feature_b']['cleaned'], 0)

    def test_scope_progress_includes_critic_suggestions(self):
        worktree = empty_worktree('/repo')
        worktree.update({
            'available': True,
            'critic': {
                'scopes': {
                    'feature_a': {'minimal_refactors': 2, 'major_refactors': 1},
                },
            },
        })

        rows = scope_progress(worktree, {'scopes': {}})

        self.assertEqual(rows[0]['name'], 'feature_a')
        self.assertEqual(rows[0]['minimal_refactors'], 2)
        self.assertEqual(rows[0]['major_refactors'], 1)

    def test_non_repo_reports_unavailable(self):
        with tempfile.TemporaryDirectory() as target:
            worktree = collect_worktree(target)
        self.assertFalse(worktree['available'])
        self.assertEqual(worktree['level'], 'UNAVAILABLE')

    def test_porcelain_parser_handles_rename_spaces_and_conflict(self):
        entries = parse_porcelain(
            b'R  feature/new name.py\0feature/old name.py\0'
            b'UU feature/conflict.py\0?? feature/new file.py\0'
        )

        self.assertEqual(entries[0]['path'], 'feature/new name.py')
        self.assertEqual(entries[0]['original_path'], 'feature/old name.py')
        self.assertTrue(entries[0]['staged'])
        self.assertTrue(entries[1]['conflict'])
        self.assertFalse(entries[1]['staged'])
        self.assertFalse(entries[1]['unstaged'])
        self.assertTrue(entries[2]['untracked'])

    def test_dirtiness_boundaries(self):
        self.assertEqual(_dirtiness_level(0, 0, 0), 'CLEAN')
        self.assertEqual(_dirtiness_level(10, 0, 1), 'LIGHT')
        self.assertEqual(_dirtiness_level(11, 0, 1), 'MODERATE')
        self.assertEqual(_dirtiness_level(50, 0, 5), 'MODERATE')
        self.assertEqual(_dirtiness_level(51, 0, 5), 'HEAVY')
        self.assertEqual(_dirtiness_level(12, 0, 6), 'HEAVY')
        self.assertEqual(_dirtiness_level(1, 1, 1), 'CRITICAL')

    def test_unavailable_poll_preserves_checkpoint(self):
        state = TuiState('/tmp/missing-state.json')
        checkpoint = {'repo_root': '/repo', 'label': 'start', 'total': 4, 'scopes': {}}
        with state.lock:
            state.data['target_dir'] = '/repo'
            state.data['git_checkpoint'] = checkpoint
        with mock.patch('core.tui_state.collect_worktree') as collect:
            collect.return_value = empty_worktree('/repo', 'git timeout')
            state.refresh_git()

        self.assertIs(state.data['git_checkpoint'], checkpoint)

    def test_available_new_repo_resets_checkpoint(self):
        state = TuiState('/tmp/missing-state.json')
        with state.lock:
            state.data['target_dir'] = '/new-repo'
            state.data['git_checkpoint'] = {
                'repo_root': '/old-repo', 'label': 'start', 'total': 4, 'scopes': {}
            }
        next_worktree = empty_worktree('/new-repo')
        next_worktree.update({
            'available': True, 'repo_root': '/new-repo', 'branch': 'main',
            'level': 'CLEAN',
        })
        with mock.patch('core.tui_state.collect_worktree', return_value=next_worktree):
            state.refresh_git()

        self.assertEqual(state.data['git_checkpoint']['repo_root'], '/new-repo')

    def test_manual_checkpoint_during_outage_preserves_baseline(self):
        state = TuiState('/tmp/missing-state.json')
        checkpoint = {'repo_root': '/repo', 'label': 'start', 'total': 4, 'scopes': {}}
        with state.lock:
            state.data['git_worktree'] = empty_worktree('/repo', 'git timeout')
            state.data['git_checkpoint'] = checkpoint

        saved = state.checkpoint_git(label='later')

        self.assertFalse(saved)
        self.assertIs(state.data['git_checkpoint'], checkpoint)

    def test_panel_progress_and_height_are_bounded(self):
        worktree = empty_worktree('/repo')
        worktree.update({
            'available': True,
            'repo_root': '/repo',
            'total': 12,
            'scopes': [
                {'name': 'a', 'total': 7, 'staged': 0, 'unstaged': 7,
                 'untracked': 0, 'conflicts': 0},
                {'name': 'b', 'total': 5, 'staged': 0, 'unstaged': 5,
                 'untracked': 0, 'conflicts': 0},
            ],
        })
        checkpoint = {'repo_root': '/repo', 'total': 20, 'scopes': {'a': 10, 'b': 10}}

        self.assertEqual(_overall_progress(worktree, checkpoint), (20, 8))
        self.assertEqual(worktree_panel_height(worktree, checkpoint, 24), 4)
        self.assertEqual(
            worktree_panel_height(worktree, checkpoint, 10, max_rows=1), 1
        )
        self.assertEqual(
            worktree_panel_height(worktree, checkpoint, 10, max_rows=0), 0
        )
        self.assertEqual(
            worktree_panel_height(empty_worktree('/repo'), checkpoint, 24), 1
        )

    def test_table_header_and_rows_share_divider_columns(self):
        width = 110
        header = _format_table_row(
            width, 'Git main HEAVY', 'dirty', 'start', 'cleaned', 'staged',
            'modified', 'new', 'conflicts', 'quick fix', 'major ref', header=True,
        )
        total = _format_table_row(
            width, 'TOTAL', 273, 273, 0, 0, 158, 115, 0, 2, 1,
        )
        scope = _format_table_row(
            width, 'shared', 237, 237, 0, 0, 144, 93, 0, 2, 1,
        )
        divider_positions = lambda row: [i for i, char in enumerate(row) if char == '│']

        self.assertEqual(divider_positions(header), divider_positions(total))
        self.assertEqual(divider_positions(total), divider_positions(scope))
        self.assertIn('dirty', header)
        self.assertIn('start', header)
        self.assertIn('modified', header)
        self.assertIn('conflicts', header)
        self.assertIn('quick fix', header)
        self.assertIn('major ref', header)

    def test_narrow_table_keeps_conflict_and_critic_columns(self):
        width = 80
        header = _format_table_row(
            width, 'Git main HEAVY', 'dirty', 'start', 'cleaned', 'staged',
            'modified', 'new', 'conflicts', 'quick fix', 'major ref', header=True,
        )
        row = _format_table_row(
            width, 'TOTAL', 273, 273, 0, 0, 158, 115, 2, 3, 1,
        )

        positions = lambda value: [
            index for index, char in enumerate(value) if char == '│'
        ]
        self.assertEqual(positions(header), positions(row))
        self.assertIn('conflicts', header)
        self.assertIn('quick', header)
        self.assertIn('major', header)
        self.assertTrue(header.endswith('major …'))


if __name__ == '__main__':
    unittest.main()
