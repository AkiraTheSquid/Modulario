"""Watch view event handler — one outer-loop iteration per call.

The watch view lists every folder's `watch.py` script, lets the user scroll,
toggle enable/disable, run one watch, or re-run all of them. The threading
callbacks here close over `state` so their background runs can mutate the
shared `data` dict under the lock without needing module-level refs.
"""
import curses
import threading
import time

from core.watches import disable_watch, enable_watch, run_watch, run_watches, scan_watches
from views.display import draw_watch_view


def handle_watch_view(stdscr, state, view, snap, dirty, flash_msg, h):
    data = state.data
    lock = state.lock
    target_dir_w = data.get('target_dir', '')
    with lock:
        entries = list(data.get('watch_entries', []))
    content_h = max(0, h - 4)
    max_ws = max(0, len(entries) - content_h)
    view.watch_scroll = max(0, min(view.watch_scroll, max_ws))
    view.watch_cursor = max(0, min(view.watch_cursor, len(entries) - 1)) if entries else 0

    if dirty:
        draw_watch_view(stdscr, entries, snap.watch_results,
                        view.watch_scroll, view.watch_cursor, flash_msg)

    key = stdscr.getch()
    if key == -1:
        return None
    if key == 3:
        return 'break'
    elif key in (ord('w'), ord('W'), ord('q'), ord('Q'), 27):
        view.show_watches = False
        view.watch_scroll = 0
        view.watch_cursor = 0
    elif key in (curses.KEY_UP, ord('k')):
        view.watch_cursor = max(0, view.watch_cursor - 1)
        if view.watch_cursor < view.watch_scroll:
            view.watch_scroll = view.watch_cursor
    elif key in (curses.KEY_DOWN, ord('j')):
        view.watch_cursor = min(len(entries) - 1, view.watch_cursor + 1) if entries else 0
        if view.watch_cursor >= view.watch_scroll + content_h:
            view.watch_scroll = view.watch_cursor - content_h + 1
    elif key == curses.KEY_PPAGE:
        view.watch_cursor = max(0, view.watch_cursor - (content_h - 1))
        view.watch_scroll = max(0, view.watch_scroll - (content_h - 1))
    elif key == curses.KEY_NPAGE:
        view.watch_cursor = min(len(entries) - 1, view.watch_cursor + (content_h - 1)) if entries else 0
        view.watch_scroll = min(max_ws, view.watch_scroll + (content_h - 1))
    elif key == ord('g'):
        view.watch_cursor = 0
        view.watch_scroll = 0
    elif key == ord('G'):
        view.watch_cursor = len(entries) - 1 if entries else 0
        view.watch_scroll = max_ws
    elif key in (ord('d'), ord(' ')) and entries:
        entry = entries[view.watch_cursor]
        folder = entry['folder']
        if entry['disabled']:
            enable_watch(target_dir_w, folder)
            with lock:
                data['flash'] = (f"Enabled watch for '{folder}'", time.time())
        else:
            disable_watch(target_dir_w, folder)
            with lock:
                data['flash'] = (f"Disabled watch for '{folder}'", time.time())
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
        entry = entries[view.watch_cursor]
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
                view.watch_cursor = max(0, view.watch_cursor - 3)
                view.watch_scroll = max(0, view.watch_scroll - 3)
            elif bstate & curses.BUTTON5_PRESSED:
                view.watch_cursor = min(len(entries) - 1, view.watch_cursor + 3) if entries else 0
                view.watch_scroll = min(max_ws, view.watch_scroll + 3)
        except curses.error:
            pass

    with lock:
        data['dirty'] = True
    return None
