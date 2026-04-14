"""Stopgate settings page — toggle features + edit max LOC.

Triggered by 'e' from the main view. Reads/writes configs/stopgate.json via
scripts/stopgate_config.py (shared with the stop hook).
"""
import curses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import stopgate_config

_C_HEADER = 7
_C_GREEN  = 5
_C_RED    = 1

_FEATURE_LABELS = [
    ("watches",         "Watches              ", "Run `mod watch run` and block on failures"),
    ("cycles",          "Circular imports     ", "Block when state.json reports import cycles"),
    ("unfilled_docs",   "Unfilled folder docs ", "Block when touched folders have template README.md"),
    ("oversized_files", "Oversized files      ", "Block when any file exceeds the Max LOC limit"),
]


def _safe(win, y, x, s, attr=0):
    h, w = win.getmaxyx()
    if y >= h or x >= w:
        return
    try:
        win.addstr(y, x, s[:w - x], attr)
    except curses.error:
        pass


def _prompt_int(stdscr, label, current):
    h, w = stdscr.getmaxyx()
    prompt = f"{label} (current: {current}): "
    y = h - 1
    _safe(stdscr, y, 0, " " * (w - 1))
    _safe(stdscr, y, 0, prompt, curses.A_BOLD)
    curses.echo()
    curses.curs_set(1)
    try:
        raw = stdscr.getstr(y, len(prompt), 10).decode("utf-8", "replace")
    except Exception:
        raw = ""
    curses.noecho()
    curses.curs_set(0)
    raw = raw.strip()
    if not raw:
        return None
    if not raw.isdigit() or int(raw) <= 0:
        return False
    return int(raw)


def draw_settings_view(stdscr, cfg, cursor, flash_msg=""):
    stdscr.erase()
    h, w = stdscr.getmaxyx()

    banner = "  ◈ Stopgate Settings"
    _safe(stdscr, 0, 0, banner.ljust(w)[:w],
          curses.color_pair(_C_HEADER) | curses.A_BOLD)
    _safe(stdscr, 1, 0, "  Features that can block Claude from ending its turn.", curses.A_DIM)

    y = 3
    for idx, (key, label, desc) in enumerate(_FEATURE_LABELS):
        on = cfg["enabled"].get(key, True)
        mark = "[X]" if on else "[ ]"
        color = curses.color_pair(_C_GREEN) if on else curses.color_pair(_C_RED)
        attr = curses.A_REVERSE if idx == cursor else 0
        _safe(stdscr, y, 2, mark, color | attr | curses.A_BOLD)
        _safe(stdscr, y, 6, label, attr | curses.A_BOLD)
        _safe(stdscr, y, 28, desc, attr | curses.A_DIM)
        y += 1

    y += 1
    loc_idx = len(_FEATURE_LABELS)
    attr = curses.A_REVERSE if cursor == loc_idx else 0
    _safe(stdscr, y, 2, f"Max LOC per file: {cfg['loc_limit']}", attr | curses.A_BOLD)
    _safe(stdscr, y, 40, "(Enter to edit)", attr | curses.A_DIM)

    footer = "  ↑↓ move   Space/Enter toggle   Enter on LOC edits   e/q/Esc close"
    _safe(stdscr, h - 2, 0, footer.ljust(w)[:w], curses.A_DIM)
    if flash_msg:
        _safe(stdscr, h - 1, 0, flash_msg[:w - 1], curses.color_pair(_C_HEADER) | curses.A_BOLD)

    stdscr.refresh()


def run_settings_loop(stdscr):
    cfg = stopgate_config.load()
    cursor = 0
    total = len(_FEATURE_LABELS) + 1
    flash = ""
    while True:
        draw_settings_view(stdscr, cfg, cursor, flash)
        flash = ""
        key = stdscr.getch()
        if key == -1:
            continue
        if key in (ord('q'), ord('Q'), ord('e'), ord('E'), 27, 3):
            return
        if key in (curses.KEY_UP, ord('k')):
            cursor = (cursor - 1) % total
        elif key in (curses.KEY_DOWN, ord('j')):
            cursor = (cursor + 1) % total
        elif key in (ord(' '), 10, 13, curses.KEY_ENTER):
            if cursor < len(_FEATURE_LABELS):
                feat = _FEATURE_LABELS[cursor][0]
                cfg["enabled"][feat] = not cfg["enabled"].get(feat, True)
                stopgate_config.save(cfg)
                flash = f"{feat} → {'enabled' if cfg['enabled'][feat] else 'disabled'}"
            else:
                val = _prompt_int(stdscr, "Max LOC", cfg["loc_limit"])
                if val is None:
                    flash = ""
                elif val is False:
                    flash = "Invalid — enter a positive integer"
                else:
                    cfg["loc_limit"] = val
                    stopgate_config.save(cfg)
                    flash = f"Max LOC → {val}"
