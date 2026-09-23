import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

from critic_store import (
    critic_notes_path,
    critic_summary,
    load_critic_notes,
    record_review,
)
from run_critic import (
    CRITIC_ACTIVE_ENV,
    MAX_UNTRACKED_FILE_CHARS,
    REVIEW_TIMEOUT_SECONDS,
    _run_fable,
    collect_diff,
    critic_for,
    main as critic_main,
)
from git_test_utils import init_git_repo


def _review_json(minimal_title='Extract horizon helper', major_title='Unify horizon UI'):
    return json.dumps({
        'summary': 'Feature review complete.',
        'correctness_findings': [],
        'features': [{
            'name': 'Daily goals',
            'category': 'goals',
            'subcategory': 'daily horizon',
            'purpose': 'Render and edit daily long-term-goal milestones.',
            'paths': ['shared/goals/daily.js'],
            'duplicates': [{
                'with': 'Weekly goals',
                'why': 'Both implement the same milestone CRUD and date navigation.',
                'paths': ['shared/goals/daily.js', 'shared/goals/weekly.js'],
            }],
            'minimal_refactors': [{
                'title': minimal_title,
                'why': 'Removes local duplicate milestone formatting.',
                'reason_category': 'performance',
                'paths': ['shared/goals/daily.js'],
            }],
            'major_refactors': [{
                'title': major_title,
                'why': 'All horizon tabs could share one configurable rendering layer.',
                'paths': ['shared/goals/daily.js', 'shared/goals/weekly.js'],
            }],
        }],
    })


class CriticStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.target = self.base / 'target'
        self.target.mkdir()
        self.data_dir = self.base / 'critic-data'

    def tearDown(self):
        self.temp.cleanup()

    def test_records_feature_taxonomy_duplication_and_reasons(self):
        raw = '```json\n' + _review_json() + '\n```'

        review = record_review(
            self.target, 'codex', 'fable', raw,
            scope='working diff', data_dir=self.data_dir,
        )
        stored = load_critic_notes(self.target, data_dir=self.data_dir)

        feature = review['features'][0]
        self.assertEqual(feature['category'], 'goals')
        self.assertEqual(feature['subcategory'], 'daily horizon')
        self.assertIn('Render and edit', feature['purpose'])
        self.assertIn('same milestone CRUD', feature['duplicates'][0]['why'])
        self.assertIn('why', feature['minimal_refactors'][0])
        self.assertEqual(feature['minimal_refactors'][0]['reason_category'], 'performance')
        self.assertIn('why', feature['major_refactors'][0])
        self.assertEqual(stored['providers']['codex']['critic'], 'fable')
        self.assertTrue(critic_notes_path(self.target, self.data_dir).exists())

    def test_summary_deduplicates_same_suggestion_across_critics(self):
        record_review(
            self.target, 'claude', 'codex', _review_json(), data_dir=self.data_dir
        )
        record_review(
            self.target, 'codex', 'fable', _review_json(), data_dir=self.data_dir
        )

        summary = critic_summary(self.target, data_dir=self.data_dir)

        self.assertEqual(summary['minimal_refactors'], 1)
        self.assertEqual(summary['major_refactors'], 1)
        self.assertEqual(summary['scopes']['shared']['minimal_refactors'], 1)
        self.assertEqual(summary['providers'], ['claude', 'codex'])

    def test_latest_review_replaces_one_author_only(self):
        record_review(
            self.target, 'claude', 'codex', _review_json(), data_dir=self.data_dir
        )
        record_review(
            self.target, 'codex', 'fable', _review_json(), data_dir=self.data_dir
        )
        replacement = json.dumps({
            'summary': 'clean',
            'correctness_findings': [],
            'features': [{
                'name': 'Daily goals',
                'category': 'goals',
                'subcategory': 'daily horizon',
                'purpose': 'Render daily milestones.',
                'paths': ['shared/goals/daily.js'],
                'duplicates': [],
                'minimal_refactors': [],
                'major_refactors': [],
            }],
        })
        record_review(
            self.target, 'codex', 'fable', replacement, data_dir=self.data_dir
        )

        stored = load_critic_notes(self.target, data_dir=self.data_dir)

        self.assertEqual(len(stored['providers']['claude']['features']), 1)
        self.assertEqual(
            stored['providers']['codex']['features'][0]['minimal_refactors'], []
        )

    def test_scoped_review_preserves_unrelated_feature_notes(self):
        initial = json.loads(_review_json())
        initial['features'].append({
            'name': 'Settings',
            'category': 'settings',
            'subcategory': 'preferences',
            'purpose': 'Edit user preferences.',
            'paths': ['shared/settings/view.js'],
            'duplicates': [],
            'minimal_refactors': [],
            'major_refactors': [],
        })
        record_review(
            self.target, 'codex', 'fable', json.dumps(initial), data_dir=self.data_dir
        )
        replacement = json.loads(_review_json(minimal_title='Use shared formatter'))

        record_review(
            self.target, 'codex', 'fable', json.dumps(replacement),
            reviewed_paths=['shared/goals'], data_dir=self.data_dir,
        )
        features = load_critic_notes(
            self.target, data_dir=self.data_dir
        )['providers']['codex']['features']

        self.assertEqual({feature['name'] for feature in features}, {'Daily goals', 'Settings'})
        daily = next(feature for feature in features if feature['name'] == 'Daily goals')
        self.assertEqual(daily['minimal_refactors'][0]['title'], 'Use shared formatter')

    def test_scoped_review_preserves_pathless_feature_documentation(self):
        initial = json.loads(_review_json())
        initial['features'].append({
            'name': 'Conceptual feature',
            'category': 'architecture',
            'subcategory': 'shared behavior',
            'purpose': 'Documents cross-cutting behavior without one owning path.',
            'paths': [],
            'duplicates': [],
            'minimal_refactors': [],
            'major_refactors': [],
        })
        record_review(
            self.target, 'codex', 'fable', json.dumps(initial), data_dir=self.data_dir
        )

        record_review(
            self.target, 'codex', 'fable', _review_json(),
            reviewed_paths=['shared/goals'], data_dir=self.data_dir,
        )
        features = load_critic_notes(
            self.target, data_dir=self.data_dir
        )['providers']['codex']['features']

        self.assertIn('Conceptual feature', {feature['name'] for feature in features})

    def test_scoped_review_keeps_unreviewed_parts_of_multi_path_feature(self):
        initial = json.loads(_review_json())
        feature = initial['features'][0]
        feature['paths'] = ['shared/goals/daily.js', 'shared/goals/weekly.js']
        feature['minimal_refactors'][0]['paths'] = ['shared/goals/weekly.js']
        record_review(
            self.target, 'codex', 'fable', json.dumps(initial), data_dir=self.data_dir
        )
        replacement = json.loads(_review_json())
        replacement['features'][0]['minimal_refactors'] = []

        record_review(
            self.target, 'codex', 'fable', json.dumps(replacement),
            reviewed_paths=['shared/goals/daily.js'], data_dir=self.data_dir,
        )
        daily = load_critic_notes(
            self.target, data_dir=self.data_dir
        )['providers']['codex']['features'][0]

        self.assertEqual(
            daily['paths'], ['shared/goals/daily.js', 'shared/goals/weekly.js']
        )
        self.assertEqual(
            daily['minimal_refactors'][0]['paths'], ['shared/goals/weekly.js']
        )

    def test_scoped_review_removes_renamed_feature_and_keeps_new_name(self):
        record_review(
            self.target, 'codex', 'fable', _review_json(), data_dir=self.data_dir
        )
        renamed = json.loads(_review_json())
        renamed['features'][0]['name'] = 'Today goals'

        record_review(
            self.target, 'codex', 'fable', json.dumps(renamed),
            reviewed_paths=['shared/goals/daily.js'], data_dir=self.data_dir,
        )
        features = load_critic_notes(
            self.target, data_dir=self.data_dir
        )['providers']['codex']['features']

        self.assertEqual([feature['name'] for feature in features], ['Today goals'])

    def test_scoped_review_preserves_unrelated_correctness_findings(self):
        initial = json.loads(_review_json())
        initial['correctness_findings'] = [
            {
                'severity': 'urgent',
                'title': 'Daily bug',
                'why': 'Daily behavior fails.',
                'paths': ['shared/goals/daily.js'],
            },
            {
                'severity': 'high',
                'title': 'Settings bug',
                'why': 'Settings behavior fails.',
                'paths': ['shared/settings/view.js'],
            },
        ]
        record_review(
            self.target, 'codex', 'fable', json.dumps(initial), data_dir=self.data_dir
        )

        record_review(
            self.target, 'codex', 'fable', _review_json(),
            reviewed_paths=['shared/goals'], data_dir=self.data_dir,
        )
        findings = load_critic_notes(
            self.target, data_dir=self.data_dir
        )['providers']['codex']['correctness_findings']

        self.assertEqual([finding['title'] for finding in findings], ['Settings bug'])
        self.assertEqual(findings[0]['severity'], 'high')

    def test_dot_directory_paths_are_preserved(self):
        value = json.loads(_review_json())
        value['features'][0]['paths'] = ['./.github/workflows/check.yml']

        review = record_review(
            self.target, 'codex', 'fable', json.dumps(value), data_dir=self.data_dir
        )

        self.assertEqual(review['features'][0]['paths'], ['.github/workflows/check.yml'])

    def test_unsafe_scoped_paths_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'no safe repo-relative paths'):
            record_review(
                self.target, 'codex', 'fable', _review_json(),
                reviewed_paths=['/outside/repo.py'], data_dir=self.data_dir,
            )

    def test_summary_cache_reuses_unchanged_notes_file(self):
        record_review(
            self.target, 'codex', 'fable', _review_json(), data_dir=self.data_dir
        )
        import critic_store

        with mock.patch(
            'critic_store.load_critic_notes', wraps=critic_store.load_critic_notes
        ) as load:
            first = critic_summary(self.target, data_dir=self.data_dir)
            second = critic_summary(self.target, data_dir=self.data_dir)
            first['minimal_refactors'] = 999
            third = critic_summary(self.target, data_dir=self.data_dir)

        self.assertEqual(second, third)
        self.assertIsNot(first, second)
        self.assertEqual(third['minimal_refactors'], 1)
        self.assertEqual(load.call_count, 1)

    def test_scoped_review_merges_overlapping_same_title_suggestion(self):
        initial = json.loads(_review_json())
        initial['features'][0]['paths'] = [
            'shared/goals/daily.js', 'shared/goals/weekly.js',
        ]
        initial['features'][0]['minimal_refactors'][0]['paths'] = [
            'shared/goals/daily.js', 'shared/goals/weekly.js',
        ]
        record_review(
            self.target, 'codex', 'fable', json.dumps(initial), data_dir=self.data_dir
        )

        record_review(
            self.target, 'codex', 'fable', json.dumps(initial),
            reviewed_paths=['shared/goals/daily.js'], data_dir=self.data_dir,
        )
        feature = load_critic_notes(
            self.target, data_dir=self.data_dir
        )['providers']['codex']['features'][0]

        self.assertEqual(len(feature['minimal_refactors']), 1)
        self.assertEqual(
            feature['minimal_refactors'][0]['paths'],
            ['shared/goals/weekly.js', 'shared/goals/daily.js'],
        )

    def test_invalid_output_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'no valid feature JSON'):
            record_review(
                self.target, 'codex', 'fable', 'not json', data_dir=self.data_dir
            )

    def test_incomplete_output_cannot_replace_existing_notes(self):
        record_review(
            self.target, 'codex', 'fable', _review_json(), data_dir=self.data_dir
        )

        with self.assertRaisesRegex(ValueError, 'missing summary'):
            record_review(
                self.target, 'codex', 'fable', '{"features": []}',
                data_dir=self.data_dir,
            )

        stored = load_critic_notes(self.target, data_dir=self.data_dir)
        self.assertEqual(len(stored['providers']['codex']['features']), 1)

    def test_scoped_review_accumulates_duplicate_current_feature_keys(self):
        current = json.loads(_review_json())
        duplicate = dict(current['features'][0])
        duplicate['paths'] = ['shared/goals/weekly.js']
        duplicate['minimal_refactors'] = [{
            'title': 'Extract weekly helper',
            'why': 'Removes weekly duplication.',
            'reason_category': 'duplication',
            'paths': ['shared/goals/weekly.js'],
        }]
        current['features'].append(duplicate)

        review = record_review(
            self.target, 'codex', 'fable', json.dumps(current),
            reviewed_paths=['shared/goals'], data_dir=self.data_dir,
        )

        self.assertEqual(len(review['features']), 1)
        self.assertEqual(
            review['features'][0]['paths'],
            ['shared/goals/daily.js', 'shared/goals/weekly.js'],
        )
        self.assertEqual(len(review['features'][0]['minimal_refactors']), 2)


class CriticDiffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        init_git_repo(self.repo, {'tracked.py': 'value = 1\n'})

    def tearDown(self):
        self.temp.cleanup()

    def test_collect_diff_includes_tracked_and_untracked(self):
        (self.repo / 'tracked.py').write_text('value = 2\n', encoding='utf-8')
        (self.repo / 'new.py').write_text('created = True\n', encoding='utf-8')

        diff, scope = collect_diff(self.repo, [])

        self.assertIn('tracked.py', diff)
        self.assertIn('new.py', diff)
        self.assertIn('+created = True', diff)
        self.assertIn('untracked', scope)

    def test_absolute_file_scope_is_normalized_repo_relative(self):
        from run_critic import normalize_requested_paths

        paths = normalize_requested_paths(self.repo, [self.repo / 'tracked.py'])

        self.assertEqual(paths, ['tracked.py'])

    def test_root_file_scope_includes_untracked_files(self):
        (self.repo / 'new.py').write_text('created = True\n', encoding='utf-8')

        diff, _scope = collect_diff(self.repo, ['--files', '.'])

        self.assertIn('new.py', diff)

    def test_large_untracked_file_read_is_bounded(self):
        path = self.repo / 'large.txt'
        path.write_text('x' * (MAX_UNTRACKED_FILE_CHARS + 50), encoding='utf-8')

        with self.assertRaisesRegex(ValueError, 'exceeds critic limit'):
            collect_diff(self.repo, ['--files', 'large.txt'])

    def test_reviewer_always_uses_other_model_family(self):
        self.assertEqual(critic_for('codex'), 'fable')
        self.assertEqual(critic_for('claude'), 'codex')

    def test_nested_critic_run_is_blocked_before_repo_or_model_work(self):
        stderr = io.StringIO()
        with mock.patch.dict('os.environ', {CRITIC_ACTIVE_ENV: '1'}):
            with mock.patch('sys.stderr', stderr):
                result = critic_main(['codex', '--files', 'tracked.py'])

        self.assertEqual(result, 5)
        self.assertEqual(stderr.getvalue(), 'critic: nested critic run blocked\n')

    def test_fable_reviewer_has_no_tools(self):
        with mock.patch('run_critic.subprocess.run') as run:
            _run_fable(self.repo, 'payload', {})

        command = run.call_args.args[0]
        self.assertEqual(command[command.index('--tools') + 1], '')
        self.assertEqual(run.call_args.kwargs['timeout'], REVIEW_TIMEOUT_SECONDS)

    def test_reviewer_runs_in_disposable_directory_with_recursion_lock(self):
        model_result = mock.Mock(returncode=0, stdout=_review_json(), stderr='')
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch('run_critic._repo_root', return_value=self.repo):
            with mock.patch(
                'run_critic.collect_diff', return_value=('diff', 'working diff')
            ):
                with mock.patch('run_critic._run_fable', return_value=model_result) as run:
                    with mock.patch(
                        'run_critic.record_review',
                        return_value=json.loads(_review_json()),
                    ):
                        with mock.patch('sys.stdout', stdout), mock.patch(
                            'sys.stderr', stderr
                        ):
                            result = critic_main(['codex', '--files', 'tracked.py'])

        review_dir, _payload, env = run.call_args.args
        self.assertEqual(result, 0)
        self.assertNotEqual(review_dir, self.repo)
        self.assertFalse(review_dir.exists())
        self.assertEqual(env[CRITIC_ACTIVE_ENV], '1')
        self.assertEqual(env['MODULARIO_CRITIC_AUTHOR'], 'codex')
        self.assertEqual(env['MODULARIO_CRITIC_REVIEWER'], 'fable')


if __name__ == '__main__':
    unittest.main()
