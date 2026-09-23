"""Git worktree snapshots and checkpoint progress for the TUI.

No curses here. This module shells out to Git, converts porcelain output into
stable data, and groups changed paths by top-level scope so views stay dumb.
"""
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from critic_store import critic_summary, scope_for_path  # noqa: E402


CONFLICT_CODES = {'DD', 'AU', 'UD', 'UA', 'DU', 'AA', 'UU'}
GIT_TIMEOUT_SECONDS = 5


def empty_worktree(target_dir='', error=''):
    return {
        'available': False,
        'target_dir': target_dir,
        'repo_root': '',
        'branch': '',
        'level': 'UNAVAILABLE',
        'total': 0,
        'staged': 0,
        'unstaged': 0,
        'untracked': 0,
        'conflicts': 0,
        'minimal_refactors': 0,
        'major_refactors': 0,
        'critic': {'available': False, 'scopes': {}, 'providers': []},
        'scopes': [],
        'entries': [],
        'error': error,
    }


def _run_git(target_dir, *args):
    try:
        result = subprocess.run(
            ['git', '-C', target_dir, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 1, b'', 'git timeout'
    except OSError:
        return 1, b'', 'git unavailable'
    return result.returncode, result.stdout, result.stderr.decode('utf-8', 'replace').strip()


def parse_porcelain(raw):
    """Parse `git status --porcelain=v1 -z` without losing spaces or renames."""
    records = raw.split(b'\0')
    entries = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        text = record.decode('utf-8', 'surrogateescape')
        if len(text) < 4:
            continue
        code = text[:2]
        path = text[3:]
        original_path = ''
        if (code[0] in 'RC' or code[1] in 'RC') and index < len(records):
            original_path = records[index].decode('utf-8', 'surrogateescape')
            index += 1

        conflict = code in CONFLICT_CODES
        untracked = code == '??'
        staged = not untracked and not conflict and code[0] not in (' ', '!', '?')
        unstaged = not untracked and not conflict and code[1] not in (' ', '!', '?')
        entries.append({
            'path': path,
            'original_path': original_path,
            'code': code,
            'scope': scope_for_path(path),
            'staged': staged,
            'unstaged': unstaged,
            'untracked': untracked,
            'conflict': conflict,
        })
    return entries


def _scope_summaries(entries):
    scopes = {}
    for entry in entries:
        scope = scopes.setdefault(entry['scope'], {
            'name': entry['scope'],
            'total': 0,
            'staged': 0,
            'unstaged': 0,
            'untracked': 0,
            'conflicts': 0,
        })
        scope['total'] += 1
        scope['staged'] += int(entry['staged'])
        scope['unstaged'] += int(entry['unstaged'])
        scope['untracked'] += int(entry['untracked'])
        scope['conflicts'] += int(entry['conflict'])
    return sorted(
        scopes.values(),
        key=lambda item: (-item['conflicts'], -item['total'], item['name']),
    )


def _dirtiness_level(total, conflicts, scope_count):
    if conflicts:
        return 'CRITICAL'
    if total == 0:
        return 'CLEAN'
    if total <= 10:
        return 'LIGHT'
    if total > 50 or scope_count >= 6:
        return 'HEAVY'
    return 'MODERATE'


def collect_worktree(target_dir):
    target = os.path.realpath(target_dir or '')
    if not target or not os.path.isdir(target):
        return empty_worktree(target, 'target unavailable')

    code, root_raw, error = _run_git(target, 'rev-parse', '--show-toplevel')
    if code:
        short_error = error if error in ('git timeout', 'git unavailable') else 'not a Git repo'
        return empty_worktree(target, short_error)
    repo_root = root_raw.decode('utf-8', 'surrogateescape').strip()

    code, branch_raw, _error = _run_git(target, 'symbolic-ref', '--quiet', '--short', 'HEAD')
    if code:
        code, branch_raw, _error = _run_git(target, 'rev-parse', '--short', 'HEAD')
    branch = branch_raw.decode('utf-8', 'replace').strip() if not code else 'detached'

    code, status_raw, error = _run_git(
        target, 'status', '--porcelain=v1', '-z', '--untracked-files=all'
    )
    if code:
        return empty_worktree(target, error or 'Git status failed')

    entries = parse_porcelain(status_raw)
    scopes = _scope_summaries(entries)
    staged = sum(1 for entry in entries if entry['staged'])
    unstaged = sum(1 for entry in entries if entry['unstaged'])
    untracked = sum(1 for entry in entries if entry['untracked'])
    conflicts = sum(1 for entry in entries if entry['conflict'])
    total = len(entries)
    critic = critic_summary(repo_root)
    return {
        'available': True,
        'target_dir': target,
        'repo_root': repo_root,
        'branch': branch,
        'level': _dirtiness_level(total, conflicts, len(scopes)),
        'total': total,
        'staged': staged,
        'unstaged': unstaged,
        'untracked': untracked,
        'conflicts': conflicts,
        'minimal_refactors': critic['minimal_refactors'],
        'major_refactors': critic['major_refactors'],
        'critic': critic,
        'scopes': scopes,
        'entries': entries,
        'error': '',
    }


def make_checkpoint(worktree, label=None):
    worktree = worktree or empty_worktree()
    return {
        'repo_root': worktree.get('repo_root', ''),
        'label': label or datetime.now().strftime('%H:%M:%S'),
        'total': worktree.get('total', 0),
        'scopes': {
            scope['name']: scope['total']
            for scope in worktree.get('scopes', [])
        },
    }


def scope_progress(worktree, checkpoint):
    """Return current/baseline counts per top-level feature scope."""
    current = {scope['name']: scope for scope in (worktree or {}).get('scopes', [])}
    baseline = (checkpoint or {}).get('scopes', {})
    critic_scopes = ((worktree or {}).get('critic') or {}).get('scopes', {})
    rows = []
    for name in set(current) | set(baseline) | set(critic_scopes):
        scope = current.get(name, {
            'name': name, 'total': 0, 'staged': 0, 'unstaged': 0,
            'untracked': 0, 'conflicts': 0,
        })
        base = baseline.get(name, 0)
        critic = critic_scopes.get(name, {})
        row = dict(scope)
        row['baseline'] = base
        row['cleaned'] = base - scope['total']
        row['minimal_refactors'] = critic.get('minimal_refactors', 0)
        row['major_refactors'] = critic.get('major_refactors', 0)
        rows.append(row)
    return sorted(
        rows,
        key=lambda item: (
            -item['conflicts'],
            -item['minimal_refactors'],
            -item['major_refactors'],
            -item['total'],
            -item['baseline'],
            item['name'],
        ),
    )
