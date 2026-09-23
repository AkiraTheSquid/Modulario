"""Compact center panel for Git worktree cleanup progress."""
import curses

from core.git_worktree import scope_progress


_C_RED = 1
_C_ORANGE = 2
_C_YELLOW = 3
_C_LIME = 4
_C_GREEN = 5
_C_CYAN = 6

_LEVEL_COLOR = {
    'CLEAN': _C_GREEN,
    'LIGHT': _C_LIME,
    'MODERATE': _C_YELLOW,
    'HEAVY': _C_ORANGE,
    'CRITICAL': _C_RED,
}


def _safe(win, y, x, text, attr=0):
    height, width = win.getmaxyx()
    if y < 0 or y >= height or x < 0 or x >= width:
        return
    text = str(text).encode('utf-8', 'replace').decode('utf-8')
    try:
        win.addstr(y, x, text[:width - x], attr)
    except (curses.error, UnicodeError):
        pass


def worktree_panel_height(worktree, checkpoint, terminal_height, max_rows=None):
    if max_rows is not None and max_rows <= 0:
        return 0
    if not (worktree or {}).get('available'):
        desired = 1
    elif (worktree or {}).get('total', 0) == 0:
        desired = 2
    else:
        max_scope_rows = 3 if terminal_height >= 20 else 1
        desired = 2 + min(max_scope_rows, len(scope_progress(worktree, checkpoint)))
    return min(desired, max_rows) if max_rows is not None else desired


def _overall_progress(worktree, checkpoint):
    current = worktree.get('total', 0)
    baseline = (checkpoint or {}).get('total', current)
    return baseline, baseline - current


def _table_layout(width):
    """Prioritize safety/critic columns when full table cannot fit."""
    if width >= 107:
        indices = list(range(10))
        numeric = [5, 5, 7, 6, 8, 5, 9, 9, 9]
    elif width >= 60:
        indices = [0, 1, 3, 7, 8, 9]
        numeric = [5, 7, 9, 7, 7]
    else:
        indices = [0, 1, 7, 8, 9]
        numeric = [5, 3, 5, 5]
    separator_width = 3 * len(numeric)
    area_width = min(24, max(4, width - 1 - sum(numeric) - separator_width))
    return indices, [area_width, *numeric]


def _fit_cell(value, width, align='right'):
    text = str(value)
    if len(text) > width:
        text = text[:max(0, width - 1)] + '…'
    return f"{text:<{width}}" if align == 'left' else f"{text:>{width}}"


def _format_table_row(width, area, dirty_now, at_start, cleaned, staged,
                      modified, new, conflicts, quick_fixes, major_refactors,
                      header=False):
    values = [
        area, dirty_now, at_start, cleaned, staged, modified, new, conflicts,
        quick_fixes, major_refactors,
    ]
    indices, widths = _table_layout(width)
    values = [values[index] for index in indices]
    cells = []
    for index, (value, cell_width) in enumerate(zip(values, widths)):
        align = 'left' if index == 0 or header else 'right'
        cells.append(_fit_cell(value, cell_width, align=align))
    return (' ' + ' │ '.join(cells))[:width]


def _data_row(width, area, current, baseline, cleaned, counts):
    return _format_table_row(
        width,
        area,
        current,
        baseline,
        f"+{cleaned}" if cleaned > 0 else str(cleaned),
        counts.get('staged', 0),
        counts.get('unstaged', 0),
        counts.get('untracked', 0),
        counts.get('conflicts', 0),
        counts.get('minimal_refactors', 0),
        counts.get('major_refactors', 0),
    )


def draw_worktree_panel(stdscr, y, worktree, checkpoint, row_count):
    if row_count <= 0:
        return
    _height, width = stdscr.getmaxyx()
    if not worktree.get('available'):
        error = worktree.get('error') or 'not a Git repo'
        text = f" Git worktree │ unavailable — {error} "
        _safe(stdscr, y, 0, text.ljust(width), curses.color_pair(_C_CYAN) | curses.A_BOLD)
        return

    level = worktree.get('level', 'CLEAN')
    color = curses.color_pair(_LEVEL_COLOR.get(level, _C_CYAN)) | curses.A_BOLD
    baseline, progress = _overall_progress(worktree, checkpoint)
    branch = worktree.get('branch') or 'detached'
    if row_count == 1:
        compact = (
            f" Git worktree — {branch} — {level}: {worktree.get('total', 0)} dirty, "
            f"{worktree.get('unstaged', 0)} modified, "
            f"{worktree.get('untracked', 0)} new, "
            f"{worktree.get('minimal_refactors', 0)} quick, "
            f"{worktree.get('major_refactors', 0)} major "
        )
        _safe(stdscr, y, 0, compact.ljust(width), color)
        return

    header = _format_table_row(
        width,
        f"Git {branch} {level}",
        'dirty',
        'start',
        'cleaned',
        'staged',
        'modified',
        'new',
        'conflicts',
        'quick fix',
        'major ref',
        header=True,
    )
    header_attr = curses.color_pair(_C_CYAN) | curses.A_BOLD
    _safe(stdscr, y, 0, header.ljust(width), header_attr)

    total = _data_row(
        width,
        'TOTAL',
        worktree.get('total', 0),
        baseline,
        progress,
        worktree,
    )
    _safe(stdscr, y + 1, 0, total.ljust(width), color)

    rows = scope_progress(worktree, checkpoint)
    for offset, row in enumerate(rows[:max(0, row_count - 2)], start=2):
        cleaned = row['cleaned']
        if cleaned > 0:
            attr = curses.color_pair(_C_GREEN)
        elif cleaned < 0:
            attr = curses.color_pair(_C_ORANGE)
        else:
            attr = curses.A_DIM
        text = _data_row(
            width,
            row['name'],
            row['total'],
            row['baseline'],
            cleaned,
            row,
        )
        _safe(stdscr, y + offset, 0, text.ljust(width), attr)
