"""Shared mutable state + state-file I/O orchestration for the Modulario TUI.

`TuiState` owns the thread-shared `data` dict, the `lock`, and the pointers to
the current state.json path + StateWatcher. It exposes the operations that the
view handlers need — reload from disk, re-run the analyzer, switch to a new
target directory — so the handlers don't each need to manage threading, state
paths, or analyzer invocation details.

`ViewState` is a small namespace for view-local scroll/cursor/mode flags.
Keeping it separate from TuiState means view handlers can own their own
transient state without touching the shared dict guarded by the lock.
"""
import os
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime as _dt
from types import SimpleNamespace

from core.activity import (activity_from_diff, build_activity_folder_metrics,
                           empty_activity, snapshot_files)
from core.config import ANALYZER_PATH, THRESHOLDS_PATH
from core.state_io import (analyze_target, load_history, load_state,
                           state_path_for)
from core.tree import all_dir_paths, build_folder_metrics, flatten_tree
from core.utils import StateWatcher
from core.watches import run_watches, scan_watches
from views.input_prompt import prompt_input


def _fresh_data():
    return {
        'rows': [], 'files': [], 'summary': {}, 'last_updated': '', 'target_dir': '',
        'thresholds': {}, 'cell_counts': [[0] * 5 for _ in range(5)],
        'violations': {}, 'history': [],
        'import_graph': {}, 'fan_in_map': {}, 'folder_metrics': {},
        'activity': empty_activity(), 'activity_folders': {},
        'watch_entries': [], 'watch_results': [],
        'checkpoint_files': None, 'checkpoint_label': None,
        'dirty': True, 'flash': None, 'collapsed': set(),
    }


def _cell_counts(files, thresholds):
    loc_bands = thresholds.get('loc_bands', [150, 300, 450, 600, 750])
    dep_bands = thresholds.get('deps_bands', [4, 8, 12, 16, 20])
    N = 5

    def band(v, bs):
        for i, b in enumerate(bs):
            if v <= b: return i
        return len(bs)

    counts = [[0] * N for _ in range(N)]
    for f in files:
        r = min(band(f['loc'], loc_bands), N - 1)
        c = min(band(f['deps'], dep_bands), N - 1)
        if r + c <= N - 1:
            counts[r][c] += 1
    return counts


def fresh_view_state():
    return SimpleNamespace(
        scroll=0, show_matrix=True,
        show_ranked=False, ranked_sort='loc', ranked_scroll=0,
        show_watches=False, watch_scroll=0, watch_cursor=0,
    )


class TuiState:
    def __init__(self, state_path_str):
        self.current_state_path = os.path.abspath(state_path_str)
        self.lock = threading.Lock()
        self.data = _fresh_data()
        self.state_watcher = None

    def start_watcher(self):
        self.state_watcher = StateWatcher(self.current_state_path, self.reload)
        self.state_watcher.start()

    def stop_watcher(self):
        if self.state_watcher:
            self.state_watcher.stop()

    def rebuild_rows_locked(self):
        collapsed = set(self.data['collapsed'])
        collapsed &= all_dir_paths(self.data['files'])
        self.data['rows'] = flatten_tree(self.data['files'], collapsed)

    def reload(self):
        state = load_state(self.current_state_path)
        if not state:
            return
        t = state.get('thresholds', {})
        with self.lock:
            data = self.data
            data['files']        = state.get('files', [])
            data['summary']      = state.get('summary', {})
            data['last_updated'] = state.get('last_updated', '')
            data['target_dir']   = state.get('target_dir', self.current_state_path)
            data['thresholds']   = t
            data['cell_counts']  = _cell_counts(state.get('files', []), t)
            data['violations']   = state.get('violations', {})
            ig = state.get('import_graph', {})
            data['import_graph'] = ig
            fan_in = {}
            for deps in ig.values():
                for dep in deps:
                    fan_in[dep] = fan_in.get(dep, 0) + 1
            data['fan_in_map']   = fan_in
            data['history']      = load_history(self.current_state_path)
            target = data['target_dir']
            watch_entries = scan_watches(target) if target else []
            data['watch_entries'] = watch_entries
            data['watch_results'] = run_watches(watch_entries, target) if watch_entries else []
            data['folder_metrics'] = build_folder_metrics(
                state.get('files', []), state.get('import_graph', {}), target_dir=target
            )
            if data['checkpoint_files'] is None:
                data['checkpoint_files'] = snapshot_files(state.get('files', []))
                label = state.get('last_updated') or _dt.now().strftime('%Y-%m-%d %H:%M:%S')
                data['checkpoint_label'] = label
            data['activity'] = activity_from_diff(
                data['checkpoint_files'], data['files'], label=data.get('checkpoint_label')
            )
            data['activity_folders'] = build_activity_folder_metrics(data['activity'])
            self.rebuild_rows_locked()
            data['dirty'] = True
            data['flash'] = ('Refreshed', time.time())

    def reanalyze(self):
        """Re-run the analyzer then reload — called on manual R keypress."""
        with self.lock:
            self.data['flash'] = ('Analyzing...', time.time() + 9999)
            self.data['dirty'] = True

        def _run():
            target = self.data.get('target_dir', '')
            if target and os.path.isdir(target):
                subprocess.run(
                    [sys.executable, str(ANALYZER_PATH),
                     '--target', target,
                     '--output', str(self.current_state_path),
                     '--thresholds', str(THRESHOLDS_PATH)],
                    capture_output=True,
                )
            self.reload()

        threading.Thread(target=_run, daemon=True).start()

    def switch_target(self, raw_path, view):
        candidate = raw_path.strip()
        if not candidate:
            return False, ''

        try:
            parsed = shlex.split(candidate)
            if len(parsed) == 1:
                candidate = parsed[0]
        except ValueError:
            pass

        target = os.path.realpath(os.path.expanduser(candidate))
        if not os.path.isdir(target):
            return False, f"Not a directory: {candidate}"

        next_state_path = str(state_path_for(target))
        ok, detail = analyze_target(target, next_state_path)
        if not ok:
            return False, f"Switch failed: {detail}"

        self.current_state_path = os.path.abspath(next_state_path)
        if self.state_watcher:
            self.state_watcher.update_path(self.current_state_path)
        with self.lock:
            self.data['collapsed'] = all_dir_paths(self.data['files'])
        self.reload()
        with self.lock:
            self.data['collapsed'] = all_dir_paths(self.data['files'])
            self.rebuild_rows_locked()
            self.data['flash'] = (f"Switched to {detail}", time.time())
            self.data['dirty'] = True
        view.scroll = 0
        view.ranked_scroll = 0
        return True, detail

    def handle_path_input(self, stdscr, view, key):
        if key not in (ord('/'), ord('~'), ord('.'), ord('"'), ord("'")):
            return False
        prefix = chr(key)
        raw = prompt_input(stdscr, "Open folder: " + prefix)
        if raw is not None:
            ok, detail = self.switch_target(prefix + raw, view)
            if not ok and detail:
                with self.lock:
                    self.data['flash'] = (detail, time.time())
        with self.lock:
            self.data['dirty'] = True
        return True
