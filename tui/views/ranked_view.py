"""
Modulario TUI — ranked file views (LOC / DEPS / Priority score).
Imported by modulario-tui.py.

Three sort modes:
  'loc'      — files ranked by lines of code (largest first)
  'deps'     — files ranked by dependency count (most coupled first)
  'priority' — files ranked by combined score: LOC + DEPS×20 (same as mod query)
"""
import curses

_C_RED    = 1
_C_ORANGE = 2
_C_YELLOW = 3
_C_LIME   = 4
_C_GREEN  = 5
_C_HEADER = 7
_BAND_CP  = [_C_GREEN, _C_LIME, _C_YELLOW, _C_ORANGE, _C_RED]

_STATUS_CP  = {'RED': _C_RED, 'ORANGE': _C_ORANGE, 'YELLOW': _C_YELLOW,
               'LIME': _C_LIME, 'GREEN': _C_GREEN}
_STATUS_DOT = {'RED': '●', 'ORANGE': '◆', 'YELLOW': '◆', 'LIME': '◆', 'GREEN': '●'}
_STATUS_LTR = {'RED': 'R', 'ORANGE': 'O', 'YELLOW': 'Y', 'LIME': 'L', 'GREEN': 'G'}

SORT_LABELS = {
    'loc':      'LOC  —  largest files',
    'deps':     'DEPS  —  most coupled',
    'priority': 'Priority  —  combined score (LOC + DEPS×20)',
}


def _safe(win, y, x, s, attr=0):
    h, w = win.getmaxyx()
    if y >= h or x >= w:
        return
    try:
        win.addstr(y, x, s[:w - x], attr)
    except curses.error:
        pass


def _band_attr(value, bands):
    for i, b in enumerate(bands):
        if value <= b:
            return curses.color_pair(_BAND_CP[min(i, 4)])
    return curses.color_pair(_C_RED)


def _band_idx(value, bands):
    for i, b in enumerate(bands):
        if value <= b:
            return i
    return len(bands)


# ─── Data builder ─────────────────────────────────────────────────────────────

def build_ranked_rows(files, sort_key, thresholds):
    loc_bands  = thresholds.get('loc_bands',  [200, 400, 600, 800, 1000])
    deps_bands = thresholds.get('deps_bands', [3, 6, 9, 12, 15])

    rows = []
    for f in files:
        score = f['loc'] + f['deps'] * 20
        rows.append({
            'path':   f['path'],
            'loc':    f['loc'],
            'deps':   f['deps'],
            'status': f['status'],
            'score':  score,
        })

    if sort_key == 'loc':
        rows.sort(key=lambda r: (-r['loc'], r['path']))
    elif sort_key == 'deps':
        rows.sort(key=lambda r: (-r['deps'], r['path']))
    else:
        rows.sort(key=lambda r: (-r['score'], r['path']))

    return rows


# ─── Renderer ─────────────────────────────────────────────────────────────────

def draw_ranked_view(stdscr, rows, scroll, target_dir, sort_key, thresholds, flash_msg=''):
    stdscr.erase()
    h, w = stdscr.getmaxyx()

    loc_bands  = thresholds.get('loc_bands',  [200, 400, 600, 800, 1000])
    deps_bands = thresholds.get('deps_bands', [3, 6, 9, 12, 15])

    # Fixed right columns: LOC(7) + DEPS(6) + score(7) + status(5) = 25
    name_w = max(14, w - 25)

    # ── Row 0: header ─────────────────────────────────────────────────────────
    label  = SORT_LABELS.get(sort_key, sort_key)
    banner = f"  ◈ {label}  │  {len(rows)} files"
    _safe(stdscr, 0, 0, banner.ljust(w)[:w], curses.color_pair(_C_HEADER) | curses.A_BOLD)

    # ── Row 1: column header ───────────────────────────────────────────────────
    loc_h  = ('LOC'   if sort_key != 'loc'      else '[LOC]')
    dep_h  = ('DEPS'  if sort_key != 'deps'     else '[DEPS]')
    scr_h  = ('score' if sort_key != 'priority' else '[score]')
    _safe(stdscr, 1, 0,
          f" {'file':<{name_w - 1}}{loc_h:>7}{dep_h:>6}{scr_h:>8}  st",
          curses.A_DIM)

    # ── Rows 2..h-3: file rows ─────────────────────────────────────────────────
    content_h = max(0, h - 4)
    visible   = rows[scroll: scroll + content_h]

    for i, row in enumerate(visible):
        y      = 2 + i
        status = row['status']
        sattr  = curses.color_pair(_STATUS_CP[status])
        bold   = curses.A_BOLD if status in ('RED', 'ORANGE') else 0

        name = row['path']
        if len(name) > name_w - 2:
            name = '…' + name[-(name_w - 3):]
        _safe(stdscr, y, 0, f" {name:<{name_w - 1}}", sattr | bold if sort_key == 'priority' else bold)

        loc_attr  = _band_attr(row['loc'],  loc_bands)
        deps_attr = _band_attr(row['deps'], deps_bands)

        if sort_key == 'loc':
            loc_attr |= curses.A_BOLD
        elif sort_key == 'deps':
            deps_attr |= curses.A_BOLD

        scr_attr = sattr | (curses.A_BOLD if sort_key == 'priority' else 0)

        _safe(stdscr, y, name_w,      f"{row['loc']:>7}",   loc_attr)
        _safe(stdscr, y, name_w + 7,  f"{row['deps']:>6}",  deps_attr)
        _safe(stdscr, y, name_w + 13, f"{row['score']:>8}", scr_attr)
        _safe(stdscr, y, name_w + 21,
              f"  {_STATUS_DOT[status]} {_STATUS_LTR[status]}", sattr | bold)

    # ── Row h-2: legend ────────────────────────────────────────────────────────
    _safe(stdscr, h - 2, 0,
          f" {target_dir}   score = LOC + DEPS×20 ".ljust(w)[:w],
          curses.color_pair(_C_HEADER) | curses.A_BOLD)

    # ── Row h-1: footer ────────────────────────────────────────────────────────
    end_row  = min(scroll + content_h, len(rows))
    pos_info = f" {min(scroll + 1, max(len(rows), 1))}–{end_row}/{len(rows)} "
    hints    = " ↑↓/jk  PgUp/PgDn  g/G  L=loc  D=deps  S=score  U=churn  b=back  q=quit"
    if flash_msg:
        _safe(stdscr, h - 1, 0, flash_msg.center(w)[:w], curses.A_BOLD)
    else:
        _safe(stdscr, h - 1, 0, (hints + pos_info.rjust(w - len(hints)))[:w], curses.A_DIM)

    stdscr.refresh()
