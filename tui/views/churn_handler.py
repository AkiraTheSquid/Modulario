"""Churn view event handler — one outer-loop iteration per call.

The churn view groups files by churn bucket across a session/24h/7d horizon.
This handler draws the current horizon, reads a keypress, and either cycles
the horizon, scrolls, or hops to the ranked view. Returns 'break' on Ctrl-C.
"""
import curses

from core.churn import HORIZONS, HORIZON_LABELS, build_horizon_rows
from views.churn_view import draw_churn_view


def handle_churn_view(stdscr, state, view, snap, dirty, flash_msg, h):
    c_rows, s_touched, s_total = build_horizon_rows(
        HORIZONS[view.churn_horizon], snap.churn_map, snap.files_list, snap.history)
    window_size  = snap.thresholds.get('churn_window', 20)
    content_h    = max(0, h - 2 - 2)
    max_cs       = max(0, len(c_rows) - content_h)
    view.churn_scroll = max(0, min(view.churn_scroll, max_cs))

    if dirty:
        draw_churn_view(stdscr, c_rows, view.churn_scroll,
                        s_touched, s_total,
                        snap.target_dir, window_size,
                        HORIZON_LABELS[view.churn_horizon], flash_msg)

    key = stdscr.getch()
    if key == -1:
        return None

    if key == 3:
        return 'break'
    elif key == ord('L'):
        view.show_churn = False; view.churn_scroll = 0
        view.show_ranked = True; view.ranked_sort = 'loc';      view.ranked_scroll = 0
    elif key == ord('D'):
        view.show_churn = False; view.churn_scroll = 0
        view.show_ranked = True; view.ranked_sort = 'deps';     view.ranked_scroll = 0
    elif key in (ord('s'), ord('S')):
        view.show_churn = False; view.churn_scroll = 0
        view.show_ranked = True; view.ranked_sort = 'priority'; view.ranked_scroll = 0
    elif key in (ord('U'), ord('u'), ord('q'), ord('Q'), 27):
        view.show_churn   = False
        view.churn_scroll = 0
    elif key == ord('h'):
        view.churn_horizon = (view.churn_horizon + 1) % len(HORIZONS)
        view.churn_scroll  = 0
    elif key in (curses.KEY_UP, ord('k')):
        view.churn_scroll = max(0, view.churn_scroll - 1)
    elif key in (curses.KEY_DOWN, ord('j')):
        view.churn_scroll = min(max_cs, view.churn_scroll + 1)
    elif key == curses.KEY_PPAGE:
        view.churn_scroll = max(0, view.churn_scroll - (content_h - 1))
    elif key == curses.KEY_NPAGE:
        view.churn_scroll = min(max_cs, view.churn_scroll + (content_h - 1))
    elif key == ord('g'):
        view.churn_scroll = 0
    elif key == ord('G'):
        view.churn_scroll = max_cs
    elif key == curses.KEY_MOUSE:
        try:
            _, _mx, _my, _mz, bstate = curses.getmouse()
            if bstate & curses.BUTTON4_PRESSED:
                view.churn_scroll = max(0, view.churn_scroll - 3)
            elif bstate & curses.BUTTON5_PRESSED:
                view.churn_scroll = min(max_cs, view.churn_scroll + 3)
        except curses.error:
            pass
    elif state.handle_path_input(stdscr, view, key):
        return None

    with state.lock:
        state.data['dirty'] = True
    return None
