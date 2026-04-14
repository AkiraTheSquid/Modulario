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

This file is intentionally thin: it wires argparse → curses.wrapper → a
single event loop that snapshots shared state, computes a flash message,
and dispatches to one of four view handlers. All the heavy lifting lives
in core/tui_state.py, core/*.py, and views/*_handler.py.
"""
import argparse
import curses
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from core.tree import all_dir_paths
from core.tui_state import TuiState, fresh_view_state
from views.churn_handler import handle_churn_view
from views.display import setup_colors
from views.main_handler import handle_main_view
from views.ranked_handler import handle_ranked_view
from views.watch_handler import handle_watch_view


_SNAP_KEYS = (
    'rows', 'summary', 'last_updated', 'target_dir', 'thresholds',
    'cell_counts', 'violations', 'folder_metrics', 'churn_map', 'history',
    'activity', 'activity_folders', 'fan_in_map', 'watch_results',
)


def _snapshot(state):
    """Lock once per frame, copy the fields view handlers read."""
    with state.lock:
        data = state.data
        snap = SimpleNamespace(files_list=data['files'], **{k: data[k] for k in _SNAP_KEYS})
        dirty = data['dirty']
        flash = data['flash']
        if dirty:
            data['dirty'] = False
    return snap, dirty, flash


def _compute_flash(state, flash):
    if not flash:
        return '', False
    elapsed = time.time() - flash[1]
    if elapsed < 2.0:
        return flash[0], True
    with state.lock:
        state.data['flash'] = None
        state.data['dirty'] = True
    return '', False


def run_tui(stdscr, state_path_str):
    curses.curs_set(0)
    setup_colors()
    curses.mousemask(curses.ALL_MOUSE_EVENTS)
    stdscr.timeout(150)

    state = TuiState(state_path_str)
    view  = fresh_view_state()

    state.reload()
    with state.lock:
        state.data['collapsed'] = all_dir_paths(state.data['files'])
        state.rebuild_rows_locked()
        state.data['dirty'] = True

    state.start_watcher()

    try:
        while True:
            h, _w = stdscr.getmaxyx()
            snap, dirty, flash = _snapshot(state)
            flash_msg, force_dirty = _compute_flash(state, flash)
            if force_dirty:
                dirty = True

            if view.show_ranked:
                handler = handle_ranked_view
            elif view.show_churn:
                handler = handle_churn_view
            elif view.show_watches:
                handler = handle_watch_view
            else:
                handler = handle_main_view

            if handler(stdscr, state, view, snap, dirty, flash_msg, h) == 'break':
                break
    finally:
        state.stop_watcher()


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
