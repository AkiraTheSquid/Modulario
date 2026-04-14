"""Single-line text prompt for the Modulario TUI.

Draws a prompt at the bottom of the screen, reads keys until Enter/Esc, and
returns the typed string (or None on cancel). Used by the main view for LOC
and DEPS interval edits and by any other feature that needs a quick blocking
text input — centralized here so keyboard/cursor setup is handled once.
"""
import curses

from views.display import safe_addstr


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
