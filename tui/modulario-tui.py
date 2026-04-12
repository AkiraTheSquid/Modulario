#!/usr/bin/env python3
"""
Modulario TUI — compact, scrollable structural health monitor.

Usage:
  python3 modulario-tui.py --state /path/to/state.json

Keys (main view):
  ↑↓ / j k     scroll one line
  PgUp / PgDn  scroll one page
  g / G        jump to top / bottom
  m            hide/show triangle matrix
  l            set LOC interval
  d            set DEPS interval
  c            copy to clipboard
  r            force re-analysis (re-reads state file)
  U            open Churn Analytics view
  drop folder  switch the live target directory
  A            toggle request-activity columns + auto-open changed paths
  q / Ctrl-C   quit

Keys (churn view):
  ↑↓ / j k     scroll
  h            cycle horizon: Session → 24h → 7d
  U / q        return to main view
  Ctrl-C       quit
"""
import argparse
import curses
import hashlib
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime as _dt
from pathlib import Path

from churn import build_horizon_rows, HORIZONS, HORIZON_LABELS
from churn_view import draw_churn_view
from ranked_view import build_ranked_rows, draw_ranked_view
from activity import (activity_from_diff,
                      build_activity_folder_metrics, empty_activity, snapshot_files)
from config import (ANALYZER_PATH, CURRENT_TARGET, STATE_DIR, THRESHOLDS_PATH,
                    MODULARIO_DIR)
from display import (CHROME_ROWS, FOOTER_ROWS, MATRIX_ROWS, draw_main_view,
                     draw_watch_view, safe_addstr, setup_colors, violation_rows)
from utils import StateWatcher, build_text_dump, copy_to_clipboard
from watches import scan_watches, run_watches, run_watch, disable_watch, enable_watch


def state_path_for(target_dir):
    digest = hashlib.md5(os.path.realpath(target_dir).encode()).hexdigest()[:12]
    return STATE_DIR / f'{digest}.json'


def set_current_target(target):
    CURRENT_TARGET.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_TARGET.write_text(target + '\n')


def analyze_target(target_dir, state_path_str):
    target = os.path.realpath(os.path.expanduser(target_dir))
    if not os.path.isdir(target):
        return False, f"Not a directory: {target}"

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    set_current_target(target)
    result = subprocess.run(
        [sys.executable, str(ANALYZER_PATH),
         '--target', target,
         '--output', state_path_str,
         '--thresholds', str(THRESHOLDS_PATH)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "analysis failed"
        return False, detail
    return True, target


# ─── Data loading ──────────────────────────────────────────────────────────────

def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def build_folder_metrics(files, import_graph):
    metrics = {}
    file_paths = {f['path'] for f in files}

    def _ensure(folder):
        return metrics.setdefault(folder, {
            'loc': 0,
            'out_targets': set(),
        })

    for f in files:
        parts = Path(f['path']).parts
        prefix = ''
        for part in parts[:-1]:
            prefix = prefix + part + '/'
            _ensure(prefix)['loc'] += f['loc']

    for src, targets in (import_graph or {}).items():
        src_parts = Path(src).parts
        if len(src_parts) < 2:
            continue
        for dep in targets:
            if dep not in file_paths:
                continue
            for depth in range(1, len(src_parts)):
                folder = '/'.join(src_parts[:depth]) + '/'
                if dep.startswith(folder):
                    continue
                _ensure(folder)['out_targets'].add(dep)

    result = {}
    for folder, metric in metrics.items():
        result[folder] = {
            'loc': metric.get('loc', 0),
            'out_refs': len(metric.get('out_targets', set())),
        }
    return result


def _churn_path(state_path_str):
    stem = state_path_str[:-5] if state_path_str.endswith('.json') else state_path_str
    return stem + '_churn.json'


def _history_path(state_path_str):
    stem = state_path_str[:-5] if state_path_str.endswith('.json') else state_path_str
    return stem + '_history.jsonl'


def load_churn(state_path_str):
    try:
        with open(_churn_path(state_path_str)) as f:
            return json.load(f).get('files', {})
    except Exception:
        return {}


def load_history(state_path_str):
    """Load all history entries from _history.jsonl, oldest first."""
    entries = []
    try:
        with open(_history_path(state_path_str), 'r', encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass
    return entries


# ─── Tree flattening ───────────────────────────────────────────────────────────

def all_dir_paths(files):
    """Return the set of all directory path strings present in the file tree."""
    dirs = set()
    for f in files:
        parts = Path(f['path']).parts
        prefix = ''
        for part in parts[:-1]:
            prefix = prefix + part + '/'
            dirs.add(prefix)
    return dirs


def flatten_tree(files, collapsed=None):
    collapsed = collapsed or set()
    tree = {}
    for f in files:
        parts = Path(f['path']).parts
        node = tree
        for p in parts[:-1]:
            node = node.setdefault(('d', p), {})
        node[('f', parts[-1])] = f
    rows = []
    _flatten_node(tree, rows, 0, '', collapsed)
    return rows


def _flatten_node(node, rows, depth, path_prefix, collapsed):
    dirs  = sorted(k for k in node if k[0] == 'd')
    files = sorted(k for k in node if k[0] == 'f')
    for k in dirs:
        dir_name = k[1]
        dir_path = path_prefix + dir_name + '/'
        is_coll  = dir_path in collapsed
        rows.append(('dir', depth, dir_name, dir_path, is_coll))
        if not is_coll:
            _flatten_node(node[k], rows, depth + 1, dir_path, collapsed)
    for k in files:
        rows.append(('file', depth, k[1], node[k]))


# ─── Interval editing
# ─── Interval editing ──────────────────────────────────────────────────────────

def prompt_input(stdscr, prompt):
    h, w = stdscr.getmaxyx()
    stdscr.timeout(-1)
    curses.curs_set(1)
    buf = ''
    try:
        while True:
            safe_addstr(stdscr, h - 1, 0, ' ' * (w - 1), 0)
            safe_addstr(stdscr, h - 1, 0, (prompt + buf)[:w - 1], curses.A_BOLD)
            stdscr.refresh()
            ch = stdscr.getch()
            if ch in (10, 13):
                return buf
            elif ch in (27, 3):
                return None
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                buf = buf[:-1]
            elif 32 <= ch < 127:
                buf += chr(ch)
    finally:
        stdscr.timeout(150)
        curses.curs_set(0)


def apply_interval(field, interval, target_dir, state_path_str):
    bands = [interval * (i + 1) for i in range(5)]
    try:
        data = {}
        if THRESHOLDS_PATH.exists():
            with open(THRESHOLDS_PATH) as f:
                data = json.load(f)
        data[field] = bands
        with open(THRESHOLDS_PATH, 'w') as f:
            json.dump(data, f, indent=2)
            f.write('\n')
        ok, _ = analyze_target(target_dir, state_path_str)
        return ok
    except Exception:
        return False


# ─── Main TUI loop ─────────────────────────────────────────────────────────────

def run_tui(stdscr, state_path_str):
    curses.curs_set(0)
    setup_colors()
    curses.mousemask(curses.ALL_MOUSE_EVENTS)
    stdscr.timeout(150)

    current_state_path = os.path.abspath(state_path_str)

    lock = threading.Lock()
    data = {
        'rows': [], 'files': [], 'summary': {}, 'last_updated': '', 'target_dir': '',
        'thresholds': {}, 'cell_counts': [[0] * 5 for _ in range(5)],
        'violations': {}, 'churn_map': {}, 'history': [],
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

    def rebuild_rows_locked():
        collapsed = set(data['collapsed'])
        valid_dirs = all_dir_paths(data['files'])
        collapsed &= valid_dirs
        data['rows'] = flatten_tree(data['files'], collapsed)

    def reanalyze():
        """Re-run the analyzer then reload — called on manual R keypress."""
        with lock:
            data['flash'] = ('Analyzing...', time.time() + 9999)  # pin until done
            data['dirty'] = True

        def _run():
            target = data.get('target_dir', '')
            if target and os.path.isdir(target):
                subprocess.run(
                    [sys.executable, str(ANALYZER_PATH),
                     '--target', target,
                     '--output', str(current_state_path),
                     '--thresholds', str(THRESHOLDS_PATH)],
                    capture_output=True,
                )
            reload()

        threading.Thread(target=_run, daemon=True).start()

    def reload():
        state = load_state(current_state_path)
        if state:
            t = state.get('thresholds', {})
            with lock:
                data['files']        = state.get('files', [])
                data['summary']      = state.get('summary', {})
                data['last_updated'] = state.get('last_updated', '')
                data['target_dir']   = state.get('target_dir', current_state_path)
                data['thresholds']   = t
                data['cell_counts']  = _cell_counts(state.get('files', []), t)
                data['violations']   = state.get('violations', {})
                ig = state.get('import_graph', {})
                data['import_graph'] = ig
                fan_in: dict[str, int] = {}
                for deps in ig.values():
                    for dep in deps:
                        fan_in[dep] = fan_in.get(dep, 0) + 1
                data['fan_in_map']   = fan_in
                data['churn_map']    = load_churn(current_state_path)
                data['history']      = load_history(current_state_path)
                target = data['target_dir']
                watch_entries = scan_watches(target) if target else []
                data['watch_entries'] = watch_entries
                data['watch_results'] = run_watches(watch_entries, target) if watch_entries else []
                data['folder_metrics'] = build_folder_metrics(
                    state.get('files', []), state.get('import_graph', {})
                )
                if data['checkpoint_files'] is None:
                    data['checkpoint_files'] = snapshot_files(state.get('files', []))
                    label = state.get('last_updated')
                    if not label:
                        label = _dt.now().strftime('%Y-%m-%d %H:%M:%S')
                    data['checkpoint_label'] = label
                data['activity'] = activity_from_diff(
                    data['checkpoint_files'], data['files'], label=data.get('checkpoint_label')
                )
                data['activity_folders'] = build_activity_folder_metrics(data['activity'])
                rebuild_rows_locked()
                data['dirty']        = True
                data['flash']        = ('Refreshed', time.time())

    def switch_target(raw_path):
        nonlocal current_state_path, scroll, churn_scroll, ranked_scroll

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

        current_state_path = os.path.abspath(next_state_path)
        state_watcher.update_path(current_state_path)
        with lock:
            data['collapsed'] = all_dir_paths(data['files'])
        reload()
        with lock:
            data['collapsed'] = all_dir_paths(data['files'])
            rebuild_rows_locked()
            data['flash']     = (f"Switched to {detail}", time.time())
            data['dirty']     = True
        scroll = 0
        churn_scroll = 0
        ranked_scroll = 0
        return True, detail

    scroll        = 0
    show_matrix   = True

    reload()
    with lock:
        data['collapsed'] = all_dir_paths(data['files'])
        rebuild_rows_locked()
        data['dirty']     = True

    state_watcher = StateWatcher(current_state_path, reload)
    state_watcher.start()

    show_churn    = False
    churn_scroll  = 0
    churn_horizon = 0
    show_ranked   = False
    ranked_sort   = 'loc'
    ranked_scroll = 0
    show_watches  = False
    watch_scroll  = 0
    watch_cursor  = 0

    def handle_path_input(key):
        if key not in (ord('/'), ord('~'), ord('.'), ord('"'), ord("'")):
            return False
        prefix = chr(key)
        raw = prompt_input(stdscr, "Open folder: " + prefix)
        if raw is not None:
            ok, detail = switch_target(prefix + raw)
            if not ok and detail:
                with lock:
                    data['flash'] = (detail, time.time())
        with lock:
            data['dirty'] = True
        return True

    try:
        while True:
            h, w = stdscr.getmaxyx()

            with lock:
                rows         = data['rows']
                files_list   = data['files']
                summary      = data['summary']
                last_updated = data['last_updated']
                target_dir   = data['target_dir']
                thresholds   = data['thresholds']
                cell_counts  = data['cell_counts']
                violations   = data['violations']
                folder_metrics = data['folder_metrics']
                churn_map    = data['churn_map']
                history      = data['history']
                activity     = data['activity']
                activity_folders = data['activity_folders']
                fan_in_map   = data['fan_in_map']
                watch_results = data['watch_results']
                dirty        = data['dirty']
                flash        = data['flash']
                if dirty:
                    data['dirty'] = False

            flash_msg = ''
            if flash:
                elapsed = time.time() - flash[1]
                if elapsed < 2.0:
                    flash_msg = flash[0]
                    dirty = True
                else:
                    with lock:
                        data['flash'] = None
                        data['dirty'] = True

            # ── RANKED VIEW ────────────────────────────────────────────────────
            if show_ranked:
                r_rows    = build_ranked_rows(files_list, ranked_sort, thresholds)
                content_h = max(0, h - 4)
                max_rs    = max(0, len(r_rows) - content_h)
                ranked_scroll = max(0, min(ranked_scroll, max_rs))

                if dirty:
                    draw_ranked_view(stdscr, r_rows, ranked_scroll,
                                     target_dir, ranked_sort, thresholds, flash_msg)

                key = stdscr.getch()
                if key == -1:
                    with lock: data['dirty'] = True
                    continue
                if key == 3:
                    break
                elif key in (ord('u'), ord('U')):
                    show_ranked   = False; ranked_scroll = 0
                    show_churn    = True;  churn_scroll  = 0
                elif key in (ord('b'), ord('B'), 27):
                    show_ranked   = False
                    ranked_scroll = 0
                elif key in (ord('q'), ord('Q')):
                    break
                elif key == ord('L'):
                    ranked_sort = 'loc';      ranked_scroll = 0
                elif key == ord('D'):
                    ranked_sort = 'deps';     ranked_scroll = 0
                elif key in (ord('s'), ord('S')):
                    ranked_sort = 'priority'; ranked_scroll = 0
                elif key in (curses.KEY_UP, ord('k')):
                    ranked_scroll = max(0, ranked_scroll - 1)
                elif key in (curses.KEY_DOWN, ord('j')):
                    ranked_scroll = min(max_rs, ranked_scroll + 1)
                elif key == curses.KEY_PPAGE:
                    ranked_scroll = max(0, ranked_scroll - (content_h - 1))
                elif key == curses.KEY_NPAGE:
                    ranked_scroll = min(max_rs, ranked_scroll + (content_h - 1))
                elif key == ord('g'):
                    ranked_scroll = 0
                elif key == ord('G'):
                    ranked_scroll = max_rs
                elif key == curses.KEY_MOUSE:
                    try:
                        _, _mx, _my, _mz, bstate = curses.getmouse()
                        if bstate & curses.BUTTON4_PRESSED:
                            ranked_scroll = max(0, ranked_scroll - 3)
                        elif bstate & curses.BUTTON5_PRESSED:
                            ranked_scroll = min(max_rs, ranked_scroll + 3)
                    except curses.error:
                        pass
                elif handle_path_input(key):
                    continue
                with lock: data['dirty'] = True
                continue

            # ── CHURN VIEW ─────────────────────────────────────────────────────
            if show_churn:
                c_rows, s_touched, s_total = build_horizon_rows(
                    HORIZONS[churn_horizon], churn_map, files_list, history)
                window_size  = thresholds.get('churn_window', 20)
                content_h    = max(0, h - 2 - 2)
                max_cs       = max(0, len(c_rows) - content_h)
                churn_scroll = max(0, min(churn_scroll, max_cs))

                if dirty:
                    draw_churn_view(stdscr, c_rows, churn_scroll,
                                    s_touched, s_total,
                                    target_dir, window_size,
                                    HORIZON_LABELS[churn_horizon], flash_msg)

                key = stdscr.getch()
                if key == -1:
                    continue

                if key == 3:
                    break
                elif key == ord('L'):
                    show_churn = False; churn_scroll = 0
                    show_ranked = True; ranked_sort = 'loc';      ranked_scroll = 0
                elif key == ord('D'):
                    show_churn = False; churn_scroll = 0
                    show_ranked = True; ranked_sort = 'deps';     ranked_scroll = 0
                elif key in (ord('s'), ord('S')):
                    show_churn = False; churn_scroll = 0
                    show_ranked = True; ranked_sort = 'priority'; ranked_scroll = 0
                elif key in (ord('U'), ord('u'), ord('b'), ord('B'), 27):
                    show_churn   = False
                    churn_scroll = 0
                elif key in (ord('q'), ord('Q')):
                    break
                elif key == ord('h'):
                    churn_horizon = (churn_horizon + 1) % len(HORIZONS)
                    churn_scroll  = 0
                elif key in (curses.KEY_UP, ord('k')):
                    churn_scroll = max(0, churn_scroll - 1)
                elif key in (curses.KEY_DOWN, ord('j')):
                    churn_scroll = min(max_cs, churn_scroll + 1)
                elif key == curses.KEY_PPAGE:
                    churn_scroll = max(0, churn_scroll - (content_h - 1))
                elif key == curses.KEY_NPAGE:
                    churn_scroll = min(max_cs, churn_scroll + (content_h - 1))
                elif key == ord('g'):
                    churn_scroll = 0
                elif key == ord('G'):
                    churn_scroll = max_cs
                elif key == curses.KEY_MOUSE:
                    try:
                        _, _mx, _my, _mz, bstate = curses.getmouse()
                        if bstate & curses.BUTTON4_PRESSED:
                            churn_scroll = max(0, churn_scroll - 3)
                        elif bstate & curses.BUTTON5_PRESSED:
                            churn_scroll = min(max_cs, churn_scroll + 3)
                    except curses.error:
                        pass
                elif handle_path_input(key):
                    continue

                with lock:
                    data['dirty'] = True
                continue

            # ── WATCH VIEW ─────────────────────────────────────────────────────
            if show_watches:
                target_dir_w = data.get('target_dir', '')
                with lock:
                    entries = list(data.get('watch_entries', []))
                content_h = max(0, h - 4)
                max_ws = max(0, len(entries) - content_h)
                watch_scroll = max(0, min(watch_scroll, max_ws))
                watch_cursor = max(0, min(watch_cursor, len(entries) - 1)) if entries else 0

                if dirty:
                    draw_watch_view(stdscr, entries, watch_results,
                                    watch_scroll, watch_cursor, flash_msg)

                key = stdscr.getch()
                if key == -1:
                    continue

                if key == 3:
                    break
                elif key in (ord('w'), ord('W'), ord('q'), ord('Q'), 27):
                    show_watches = False
                    watch_scroll = 0
                    watch_cursor = 0
                elif key in (curses.KEY_UP, ord('k')):
                    watch_cursor = max(0, watch_cursor - 1)
                    if watch_cursor < watch_scroll:
                        watch_scroll = watch_cursor
                elif key in (curses.KEY_DOWN, ord('j')):
                    watch_cursor = min(len(entries) - 1, watch_cursor + 1) if entries else 0
                    if watch_cursor >= watch_scroll + content_h:
                        watch_scroll = watch_cursor - content_h + 1
                elif key == curses.KEY_PPAGE:
                    watch_cursor = max(0, watch_cursor - (content_h - 1))
                    watch_scroll = max(0, watch_scroll - (content_h - 1))
                elif key == curses.KEY_NPAGE:
                    watch_cursor = min(len(entries) - 1, watch_cursor + (content_h - 1)) if entries else 0
                    watch_scroll = min(max_ws, watch_scroll + (content_h - 1))
                elif key == ord('g'):
                    watch_cursor = 0
                    watch_scroll = 0
                elif key == ord('G'):
                    watch_cursor = len(entries) - 1 if entries else 0
                    watch_scroll = max_ws
                elif key in (ord('d'), ord(' ')) and entries:
                    # Toggle enable/disable
                    entry = entries[watch_cursor]
                    folder = entry['folder']
                    if entry['disabled']:
                        enable_watch(target_dir_w, folder)
                        with lock:
                            data['flash'] = (f"Enabled watch for '{folder}'", time.time())
                    else:
                        disable_watch(target_dir_w, folder)
                        with lock:
                            data['flash'] = (f"Disabled watch for '{folder}'", time.time())
                    # Rescan
                    with lock:
                        data['watch_entries'] = scan_watches(target_dir_w)
                        data['dirty'] = True
                elif key == ord('R'):
                    def _run_all_w():
                        with lock:
                            data['flash'] = ('Running all watches...', time.time() + 9999)
                            data['dirty'] = True
                        td = data.get('target_dir', '')
                        ents = scan_watches(td) if td else []
                        results = run_watches(ents, td)
                        with lock:
                            data['watch_entries'] = ents
                            data['watch_results'] = results
                            fails = sum(1 for s, _, _ in results if s == 'FAIL')
                            if not results:
                                data['flash'] = ('No enabled watches', time.time())
                            elif fails:
                                data['flash'] = (f'Watches: {fails} FAIL / {len(results)} total', time.time())
                            else:
                                data['flash'] = (f'Watches: {len(results)} PASS', time.time())
                            data['dirty'] = True
                    threading.Thread(target=_run_all_w, daemon=True).start()
                elif key in (10, 13, curses.KEY_ENTER) and entries:
                    entry = entries[watch_cursor]
                    def _run_single_w(e=entry):
                        with lock:
                            data['flash'] = (f"Running '{e['folder']}'...", time.time() + 9999)
                            data['dirty'] = True
                        result = run_watch(e, data.get('target_dir', ''))
                        with lock:
                            if result:
                                existing = {f: (s, r) for s, f, r in data['watch_results']}
                                existing[result[1]] = (result[0], result[2])
                                data['watch_results'] = [(s, f, r) for f, (s, r) in existing.items()]
                                data['flash'] = (f"'{e['folder']}': {result[0]}" + (f" — {result[2]}" if result[2] else ''), time.time())
                            else:
                                data['flash'] = (f"'{e['folder']}': skipped (unfilled/disabled)", time.time())
                            data['dirty'] = True
                    threading.Thread(target=_run_single_w, daemon=True).start()
                elif key == curses.KEY_MOUSE:
                    try:
                        _, _mx, _my, _mz, bstate = curses.getmouse()
                        if bstate & curses.BUTTON4_PRESSED:
                            watch_cursor = max(0, watch_cursor - 3)
                            watch_scroll = max(0, watch_scroll - 3)
                        elif bstate & curses.BUTTON5_PRESSED:
                            watch_cursor = min(len(entries) - 1, watch_cursor + 3) if entries else 0
                            watch_scroll = min(max_ws, watch_scroll + 3)
                    except curses.error:
                        pass

                with lock:
                    data['dirty'] = True
                continue

            # ── MAIN VIEW ──────────────────────────────────────────────────────
            matrix_rows = MATRIX_ROWS if show_matrix else 0
            viol_rows   = violation_rows(violations, watch_results)
            content_h   = max(0, h - CHROME_ROWS - matrix_rows - viol_rows - FOOTER_ROWS)
            max_scroll  = max(0, len(rows) - content_h)
            scroll      = max(0, min(scroll, max_scroll))

            if dirty:
                draw_main_view(stdscr, rows, scroll, summary, last_updated, target_dir, thresholds,
                               cell_counts, flash_msg, show_matrix, violations, churn_map, folder_metrics,
                               activity=activity, activity_folders=activity_folders, fan_in_map=fan_in_map, watch_results=watch_results)

            key = stdscr.getch()
            if key == -1:
                continue

            if key in (ord('q'), ord('Q'), 27, 3):
                break
            elif key in (ord('U'), ord('u')):
                show_churn   = True
                churn_scroll = 0
            elif key == ord('L'):
                show_ranked = True; ranked_sort = 'loc';      ranked_scroll = 0
            elif key == ord('D'):
                show_ranked = True; ranked_sort = 'deps';     ranked_scroll = 0
            elif key == ord('s'):
                show_ranked = True; ranked_sort = 'priority'; ranked_scroll = 0
            elif key == ord('S'):
                with lock:
                    data['checkpoint_files'] = snapshot_files(data['files'])
                    stamp = _dt.now().strftime('%Y-%m-%d %H:%M:%S')
                    data['checkpoint_label'] = stamp
                    activity = activity_from_diff(data['checkpoint_files'], data['files'], label=stamp)
                    data['activity'] = activity
                    data['activity_folders'] = build_activity_folder_metrics(activity)
                    data['flash'] = (f"Checkpoint saved at {stamp}", time.time())
                    data['dirty'] = True
                continue
            elif key in (curses.KEY_UP, ord('k')):
                scroll = max(0, scroll - 1)
            elif key in (curses.KEY_DOWN, ord('j')):
                scroll = min(max_scroll, scroll + 1)
            elif key == curses.KEY_PPAGE:
                scroll = max(0, scroll - (content_h - 1))
            elif key == curses.KEY_NPAGE:
                scroll = min(max_scroll, scroll + (content_h - 1))
            elif key == ord('g'):
                scroll = 0
            elif key == ord('G'):
                scroll = max_scroll
            elif key == ord('t'):
                with lock:
                    data['collapsed'] = set()
                    rebuild_rows_locked()
                    data['dirty']     = True
            elif key == ord('a'):
                with lock:
                    data['collapsed'] = all_dir_paths(data['files'])
                    rebuild_rows_locked()
                    data['dirty']     = True
            elif key in (ord('m'), ord('M')):
                show_matrix = not show_matrix
            elif key in (ord('w'), ord('W')):
                show_watches = True
                watch_scroll = 0
                watch_cursor = 0
                with lock:
                    data['dirty'] = True
                continue
            elif key in (ord('r'), ord('R'), curses.KEY_F5, 18):
                reanalyze()
                continue
            elif key in (ord('l'), ord('d')):
                field  = 'loc_bands' if key == ord('l') else 'deps_bands'
                label  = 'LOC'       if key == ord('l') else 'DEPS'
                with lock:
                    _td = data['target_dir']
                    _th = data['thresholds']
                default_bands = [150, 300, 450, 600, 750] if key == ord('l') else [3, 6, 9, 12, 15]
                cur_interval  = _th.get(field, default_bands)[0]
                val = prompt_input(stdscr, f"{label} interval (current: {cur_interval}): ")
                if val and val.strip().isdigit() and int(val.strip()) > 0:
                    n  = int(val.strip())
                    ok = apply_interval(field, n, _td, current_state_path)
                    msg = f"{label} interval → {n}" if ok else "Failed to save"
                elif val is None or val.strip() == '':
                    msg = ''
                else:
                    msg = "Invalid — enter a positive integer"
                reload()
                if msg:
                    with lock:
                        data['flash'] = (msg, time.time())
                with lock:
                    data['dirty'] = True
                continue
            elif key == curses.KEY_MOUSE:
                try:
                    _, mx, my, mz, bstate = curses.getmouse()
                    if bstate & curses.BUTTON4_PRESSED:
                        scroll = max(0, scroll - 3)
                        with lock: data['dirty'] = True
                    elif bstate & curses.BUTTON5_PRESSED:
                        scroll = min(max_scroll, scroll + 3)
                        with lock: data['dirty'] = True
                    elif bstate & curses.BUTTON1_CLICKED:
                        row_idx = my - CHROME_ROWS + scroll
                        if 0 <= row_idx < len(rows):
                            clicked = rows[row_idx]
                            if clicked[0] == 'dir':
                                _, depth, name, dir_path, is_coll = clicked
                                tri_x = 1 + depth * 2
                                if mx == tri_x:
                                    with lock:
                                        if dir_path in data['collapsed']:
                                            data['collapsed'].discard(dir_path)
                                        else:
                                            data['collapsed'].add(dir_path)
                                        rebuild_rows_locked()
                                        data['dirty'] = True
                except curses.error:
                    pass
            elif key == ord('c'):
                with lock:
                    rebuild_rows_locked()
                    _rows = list(data['rows']); _summary = data['summary']
                    _lu   = data['last_updated']; _td = data['target_dir']
                    _th   = data['thresholds']; _cc = data['cell_counts']
                    _viol = data['violations']; _cm = data['churn_map']; _fm = data['folder_metrics']
                    _activity = data['activity']
                text = build_text_dump(_rows, _summary, _lu, _td, _th, _cc, _viol, _cm, _fm, _activity)
                ok, msg = copy_to_clipboard(text)
                with lock:
                    data['flash'] = (msg, time.time())
                    data['dirty'] = True
                continue
            elif handle_path_input(key):
                continue

            with lock:
                data['dirty'] = True

    finally:
        state_watcher.stop()


# ─── Entry point ───────────────────────────────────────────────────────────────

def main():
    default_state = str(
        Path(__file__).resolve().parent.parent / 'data' / 'state.json'
    )
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', default=default_state)
    args = parser.parse_args()

    state_path = os.path.abspath(args.state)
    if not os.path.exists(state_path):
        print(f"State file not found: {state_path}")
        sys.exit(1)

    curses.wrapper(run_tui, state_path)


if __name__ == '__main__':
    main()
