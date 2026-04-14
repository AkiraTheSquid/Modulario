"""Main view event handler — one outer-loop iteration per call.

Draws the triangle matrix + tree of files with their LOC/DEPS bands, then
reads a single keypress and dispatches it: navigation, view switches,
threshold editing, clipboard copy, bug report, checkpoint, settings page,
reanalyze, or folder switch via a drop-in path prompt.
"""
import curses
import time
from datetime import datetime as _dt

from core.activity import activity_from_diff, build_activity_folder_metrics, snapshot_files
from core.bug_report import build_bug_report
from core.state_io import apply_interval
from core.tree import all_dir_paths
from core.utils import build_text_dump, copy_to_clipboard
from views.display import (CHROME_ROWS, FOOTER_ROWS, MATRIX_ROWS, draw_main_view,
                           violation_rows)
from views.input_prompt import prompt_input
from views.settings_page import run_settings_loop


def handle_main_view(stdscr, state, view, snap, dirty, flash_msg, h):
    data = state.data
    lock = state.lock

    matrix_rows = MATRIX_ROWS if view.show_matrix else 0
    viol_rows   = violation_rows(snap.violations, snap.watch_results)
    content_h   = max(0, h - CHROME_ROWS - matrix_rows - viol_rows - FOOTER_ROWS)
    max_scroll  = max(0, len(snap.rows) - content_h)
    view.scroll = max(0, min(view.scroll, max_scroll))

    if dirty:
        draw_main_view(stdscr, snap.rows, view.scroll, snap.summary, snap.last_updated,
                       snap.target_dir, snap.thresholds, snap.cell_counts, flash_msg,
                       view.show_matrix, snap.violations, snap.churn_map, snap.folder_metrics,
                       activity=snap.activity, activity_folders=snap.activity_folders,
                       fan_in_map=snap.fan_in_map, watch_results=snap.watch_results)

    key = stdscr.getch()
    if key == -1:
        return None

    if key in (ord('q'), ord('Q'), 27, 3):
        return 'break'
    elif key in (ord('U'), ord('u')):
        view.show_churn = True
        view.churn_scroll = 0
    elif key == ord('L'):
        view.show_ranked = True; view.ranked_sort = 'loc';      view.ranked_scroll = 0
    elif key == ord('D'):
        view.show_ranked = True; view.ranked_sort = 'deps';     view.ranked_scroll = 0
    elif key == ord('s'):
        view.show_ranked = True; view.ranked_sort = 'priority'; view.ranked_scroll = 0
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
        return None
    elif key in (curses.KEY_UP, ord('k')):
        view.scroll = max(0, view.scroll - 1)
    elif key in (curses.KEY_DOWN, ord('j')):
        view.scroll = min(max_scroll, view.scroll + 1)
    elif key == curses.KEY_PPAGE:
        view.scroll = max(0, view.scroll - (content_h - 1))
    elif key == curses.KEY_NPAGE:
        view.scroll = min(max_scroll, view.scroll + (content_h - 1))
    elif key == ord('g'):
        view.scroll = 0
    elif key == ord('G'):
        view.scroll = max_scroll
    elif key == ord('t'):
        with lock:
            data['collapsed'] = set()
            state.rebuild_rows_locked()
            data['dirty'] = True
    elif key == ord('a'):
        with lock:
            data['collapsed'] = all_dir_paths(data['files'])
            state.rebuild_rows_locked()
            data['dirty'] = True
    elif key in (ord('m'), ord('M')):
        view.show_matrix = not view.show_matrix
    elif key in (ord('w'), ord('W')):
        view.show_watches = True
        view.watch_scroll = 0
        view.watch_cursor = 0
        with lock:
            data['dirty'] = True
        return None
    elif key in (ord('r'), ord('R'), curses.KEY_F5, 18):
        state.reanalyze()
        return None
    elif key in (ord('l'), ord('d')):
        _handle_interval_edit(stdscr, state, key)
        return None
    elif key == curses.KEY_MOUSE:
        _handle_main_mouse(state, view, max_scroll)
    elif key == ord('c'):
        _handle_copy(state)
        return None
    elif key in (ord('b'), ord('B')):
        _handle_bug_copy(state)
        return None
    elif key in (ord('e'), ord('E')):
        run_settings_loop(stdscr)
        with lock:
            data['dirty'] = True
        return None
    elif state.handle_path_input(stdscr, view, key):
        return None

    with lock:
        data['dirty'] = True
    return None


def _handle_interval_edit(stdscr, state, key):
    data = state.data
    lock = state.lock
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
        ok = apply_interval(field, n, _td, state.current_state_path)
        msg = f"{label} interval → {n}" if ok else "Failed to save"
    elif val is None or val.strip() == '':
        msg = ''
    else:
        msg = "Invalid — enter a positive integer"
    state.reload()
    if msg:
        with lock:
            data['flash'] = (msg, time.time())
    with lock:
        data['dirty'] = True


def _handle_main_mouse(state, view, max_scroll):
    data = state.data
    lock = state.lock
    try:
        _, mx, my, _mz, bstate = curses.getmouse()
        if bstate & curses.BUTTON4_PRESSED:
            view.scroll = max(0, view.scroll - 3)
            with lock: data['dirty'] = True
        elif bstate & curses.BUTTON5_PRESSED:
            view.scroll = min(max_scroll, view.scroll + 3)
            with lock: data['dirty'] = True
        elif bstate & curses.BUTTON1_CLICKED:
            with lock:
                rows = list(data['rows'])
            row_idx = my - CHROME_ROWS + view.scroll
            if 0 <= row_idx < len(rows):
                clicked = rows[row_idx]
                if clicked[0] == 'dir':
                    _, depth, _name, dir_path, _is_coll = clicked
                    tri_x = 1 + depth * 2
                    if mx == tri_x:
                        with lock:
                            if dir_path in data['collapsed']:
                                data['collapsed'].discard(dir_path)
                            else:
                                data['collapsed'].add(dir_path)
                            state.rebuild_rows_locked()
                            data['dirty'] = True
    except curses.error:
        pass


def _handle_copy(state):
    data = state.data
    lock = state.lock
    with lock:
        state.rebuild_rows_locked()
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


def _handle_bug_copy(state):
    data = state.data
    lock = state.lock
    with lock:
        _viol = data['violations']
        _watch = data['watch_results']
        _td = data['target_dir']
    text = build_bug_report(_td, _viol, _watch)
    ok, msg = copy_to_clipboard(text)
    with lock:
        data['flash'] = (f"Bugs copied — {msg}" if ok else msg, time.time())
        data['dirty'] = True
