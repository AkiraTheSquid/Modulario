"""
Modulario TUI — session churn analytics view.
Imported by modulario-tui.py.
"""
import curses

# Colour pair indices — must match setup_colors() in modulario-tui.py
_C_RED    = 1
_C_ORANGE = 2
_C_YELLOW = 3
_C_HEADER = 7


def _safe(win, y, x, s, attr=0):
    h, w = win.getmaxyx()
    if y >= h or x >= w:
        return
    try:
        win.addstr(y, x, s[:w - x], attr)
    except curses.error:
        pass


def _session_attr(session_m1):
    """Return color attribute scaled to session churn rate."""
    if session_m1 >= 0.75:
        return curses.color_pair(_C_RED)    | curses.A_BOLD
    if session_m1 >= 0.50:
        return curses.color_pair(_C_RED)
    if session_m1 >= 0.25:
        return curses.color_pair(_C_ORANGE)
    if session_m1 >= 0.10:
        return curses.color_pair(_C_YELLOW)
    return curses.A_DIM


def draw_churn_view(stdscr, rows, scroll, files_touched, total_files,
                    target_dir, window_size, horizon_label='Session', flash_msg=''):
    stdscr.erase()
    h, w = stdscr.getmaxyx()

    # Column layout: session(8) + touches(8) = 16 fixed; file takes the rest
    name_w = max(14, w - 16)

    # ── Row 0: header ─────────────────────────────────────────────────────────
    spread_pct = f"{files_touched / max(total_files, 1):.0%}"
    banner = (f"  ◈ {horizon_label} Churn"
              f"  │  {files_touched}/{total_files} files touched"
              f"  ({spread_pct} of project)")
    _safe(stdscr, 0, 0, banner.ljust(w)[:w],
          curses.color_pair(_C_HEADER) | curses.A_BOLD)

    # ── Row 1: column header ───────────────────────────────────────────────────
    _safe(stdscr, 1, 0,
          f" {'file':<{name_w - 1}}{'session':>8}{'touches':>8}",
          curses.A_DIM)

    # ── Rows 2..h-3: file rows ─────────────────────────────────────────────────
    content_h = max(0, h - 2 - 2)
    visible   = rows[scroll: scroll + content_h]

    if not rows:
        _safe(stdscr, 3, 2,
              "No data yet — run the analyzer at least once to populate.",
              curses.A_DIM)
    else:
        for i, row in enumerate(visible):
            y   = 2 + i
            m1  = row['churn_pct']
            st  = row['sessions_touched']
            attr = _session_attr(m1)

            name = row['path']
            if len(name) > name_w - 2:
                name = '…' + name[-(name_w - 3):]
            _safe(stdscr, y, 0, f" {name:<{name_w - 1}}", attr)

            # Session churn %
            pct_s = f"{m1:>7.0%}" if m1 > 0 else f"{'—':>7}"
            _safe(stdscr, y, name_w, pct_s, attr)

            # Cross-session touch count — orange if ≥ half the window
            if st > 0:
                st_attr = (curses.color_pair(_C_ORANGE) | curses.A_BOLD) \
                          if st >= window_size // 2 else curses.A_DIM
                _safe(stdscr, y, name_w + 8, f"{st:>7}", st_attr)
            else:
                _safe(stdscr, y, name_w + 8, f"{'—':>7}", curses.A_DIM)

    # ── Row h-2: legend bar ────────────────────────────────────────────────────
    bar = (f" {target_dir}"
           f"   churn = lines worked ({horizon_label.lower()}) ÷ file size"
           f"   touches = sessions in last {window_size} runs ")
    _safe(stdscr, h - 2, 0, bar.ljust(w)[:w],
          curses.color_pair(_C_HEADER) | curses.A_BOLD)

    # ── Row h-1: footer ────────────────────────────────────────────────────────
    end_row  = min(scroll + content_h, len(rows))
    pos_info = f" {min(scroll + 1, max(len(rows), 1))}–{end_row}/{len(rows)} "
    hints    = " ↑↓/jk  PgUp/PgDn  g/G  h=horizon  L/D/S=ranked  b=back  q=quit"
    if flash_msg:
        _safe(stdscr, h - 1, 0, flash_msg.center(w)[:w], curses.A_BOLD)
    else:
        _safe(stdscr, h - 1, 0, (hints + pos_info.rjust(w - len(hints)))[:w], curses.A_DIM)

    stdscr.refresh()
