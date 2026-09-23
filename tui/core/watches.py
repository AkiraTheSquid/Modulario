"""Watch loading and execution for the TUI — folder-based watch.py system."""
import json
import os
import subprocess
import sys

from core.config import THRESHOLDS_PATH

WATCH_MARKER = '# modulario:template'
SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.next', 'dist', 'build', 'venv', '.venv'}


def _normalize_rel_path(path):
    rel = str(path or '').replace('\\', '/').strip('/')
    return '' if rel in ('', '.') else rel


def _load_skip_dirs():
    if THRESHOLDS_PATH.exists():
        try:
            with open(THRESHOLDS_PATH) as f:
                data = json.load(f)
            return [_normalize_rel_path(v) for v in (data.get('skip_dirs') or []) if str(v).strip()]
        except Exception:
            pass
    return []


def _filter_walk_dirs(root, dirs, target_dir, skip_entries):
    target = os.path.realpath(target_dir)
    root = os.path.realpath(root)
    rel_root = _normalize_rel_path(os.path.relpath(root, target))
    kept = []
    skip = set(skip_entries or [])
    for dirname in dirs:
        child_rel = dirname if not rel_root else f"{rel_root}/{dirname}"
        if dirname in SKIP_DIRS or dirname.startswith('.') or dirname in skip or child_rel in skip:
            continue
        kept.append(dirname)
    return sorted(kept)


def scan_watches(target_dir):
    """Walk target_dir and return a list of watch entries for every folder with a watch.py."""
    entries = []
    target = os.path.realpath(target_dir)
    skip_dirs = _load_skip_dirs()
    for root, dirs, files in os.walk(target):
        dirs[:] = _filter_walk_dirs(root, dirs, target, skip_dirs)
        if 'watch.py' not in files:
            continue
        rel = os.path.relpath(root, target)
        if rel == '.':
            folder = '/'
            name = os.path.basename(target)
        else:
            folder = rel + '/'
            name = os.path.basename(rel)
        watch_path = os.path.join(root, 'watch.py')
        dismissed = os.path.exists(os.path.join(root, '.watch.dismissed'))
        disabled = os.path.exists(os.path.join(root, '.watch.disabled'))

        # Check if template is unfilled
        unfilled = False
        try:
            with open(watch_path, 'r') as f:
                first_line = f.readline()
            if WATCH_MARKER in first_line:
                unfilled = True
        except OSError:
            pass

        entries.append({
            'folder': folder,
            'name': name,
            'watch_file': watch_path,
            'unfilled': unfilled,
            'dismissed': dismissed,
            'disabled': disabled,
        })
    return sorted(entries, key=lambda e: e['folder'])


def run_watch(entry, target_dir, timeout=10):
    """Run a single watch entry. Returns (status, folder, reason)."""
    if entry['unfilled'] or entry['dismissed'] or entry['disabled']:
        return None
    try:
        r = subprocess.run(
            [sys.executable, entry['watch_file']],
            cwd=target_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if r.returncode == 0:
            return ('PASS', entry['folder'], '')
        else:
            reason = (r.stderr or r.stdout or 'non-zero exit').strip()
            reason = reason.splitlines()[0][:120]
            return ('FAIL', entry['folder'], reason)
    except subprocess.TimeoutExpired:
        return ('FAIL', entry['folder'], f'timed out after {timeout}s')
    except Exception as e:
        return ('FAIL', entry['folder'], str(e)[:120])


def run_watches(entries, target_dir, timeout=10):
    """Run all eligible watches. Returns list of (status, folder, reason)."""
    results = []
    for entry in entries:
        result = run_watch(entry, target_dir, timeout)
        if result:
            results.append(result)
    return results


def disable_watch(target_dir, folder):
    """Create .watch.disabled marker in folder."""
    if folder == '/':
        abs_folder = os.path.realpath(target_dir)
    else:
        abs_folder = os.path.join(os.path.realpath(target_dir), folder.rstrip('/'))
    marker = os.path.join(abs_folder, '.watch.disabled')
    with open(marker, 'w') as f:
        f.write('')


def enable_watch(target_dir, folder):
    """Remove .watch.disabled marker from folder."""
    if folder == '/':
        abs_folder = os.path.realpath(target_dir)
    else:
        abs_folder = os.path.join(os.path.realpath(target_dir), folder.rstrip('/'))
    marker = os.path.join(abs_folder, '.watch.disabled')
    if os.path.exists(marker):
        os.remove(marker)
