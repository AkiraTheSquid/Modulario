"""Ranked view event handler — one outer-loop iteration per call.

The ranked view lists files sorted by LOC / DEPS / priority. This handler
draws the view (when dirty), reads a single keypress, and mutates `view`
to update scroll / sort / show_* flags. Returning 'break' means the user hit
Ctrl-C; returning None means keep running the outer loop.
"""
import curses

from views.ranked_view import build_ranked_rows, draw_ranked_view


def handle_ranked_view(stdscr, state, view, snap, dirty, flash_msg, h):
    r_rows    = build_ranked_rows(snap.files_list, view.ranked_sort, snap.thresholds)
    content_h = max(0, h - 4)
    max_rs    = max(0, len(r_rows) - content_h)
    view.ranked_scroll = max(0, min(view.ranked_scroll, max_rs))

    if dirty:
        draw_ranked_view(stdscr, r_rows, view.ranked_scroll,
                         snap.target_dir, view.ranked_sort, snap.thresholds, flash_msg)

    key = stdscr.getch()
    if key == -1:
        with state.lock:
            state.data['dirty'] = True
        return None
    if key == 3:
        return 'break'
    elif key in (ord('u'), ord('U')):
        view.show_ranked   = False; view.ranked_scroll = 0
        view.show_churn    = True;  view.churn_scroll  = 0
    elif key in (ord('q'), ord('Q'), 27):
        view.show_ranked   = False
        view.ranked_scroll = 0
    elif key == ord('L'):
        view.ranked_sort = 'loc';      view.ranked_scroll = 0
    elif key == ord('D'):
        view.ranked_sort = 'deps';     view.ranked_scroll = 0
    elif key in (ord('s'), ord('S')):
        view.ranked_sort = 'priority'; view.ranked_scroll = 0
    elif key in (curses.KEY_UP, ord('k')):
        view.ranked_scroll = max(0, view.ranked_scroll - 1)
    elif key in (curses.KEY_DOWN, ord('j')):
        view.ranked_scroll = min(max_rs, view.ranked_scroll + 1)
    elif key == curses.KEY_PPAGE:
        view.ranked_scroll = max(0, view.ranked_scroll - (content_h - 1))
    elif key == curses.KEY_NPAGE:
        view.ranked_scroll = min(max_rs, view.ranked_scroll + (content_h - 1))
    elif key == ord('g'):
        view.ranked_scroll = 0
    elif key == ord('G'):
        view.ranked_scroll = max_rs
    elif key == curses.KEY_MOUSE:
        try:
            _, _mx, _my, _mz, bstate = curses.getmouse()
            if bstate & curses.BUTTON4_PRESSED:
                view.ranked_scroll = max(0, view.ranked_scroll - 3)
            elif bstate & curses.BUTTON5_PRESSED:
                view.ranked_scroll = min(max_rs, view.ranked_scroll + 3)
        except curses.error:
            pass
    elif state.handle_path_input(stdscr, view, key):
        return None
    with state.lock:
        state.data['dirty'] = True
    return None
