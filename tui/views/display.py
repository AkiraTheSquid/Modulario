import curses

MATRIX_ROWS = 6
CHROME_ROWS = 2
FOOTER_ROWS = 2

C_RED    = 1
C_ORANGE = 2
C_YELLOW = 3
C_LIME   = 4
C_GREEN  = 5
C_CYAN   = 6
C_HEADER = 7

FILE_CPAIR    = {'RED': C_RED, 'ORANGE': C_ORANGE, 'YELLOW': C_YELLOW, 'LIME': C_LIME, 'GREEN': C_GREEN}
FILE_DOT      = {'RED': '●', 'ORANGE': '◆', 'YELLOW': '◆', 'LIME': '◆', 'GREEN': '●'}
STATUS_LETTER = {'RED': 'R', 'ORANGE': 'O', 'YELLOW': 'Y', 'LIME': 'L', 'GREEN': 'G'}
DIAG_STATUS   = ['GREEN', 'LIME', 'YELLOW', 'ORANGE', 'RED']


def setup_colors():
    curses.use_default_colors()
    curses.init_pair(C_RED,    curses.COLOR_RED,    -1)
    curses.init_pair(C_YELLOW, curses.COLOR_YELLOW, -1)
    curses.init_pair(C_GREEN,  curses.COLOR_GREEN,  -1)
    curses.init_pair(C_CYAN,   curses.COLOR_CYAN,   -1)
    curses.init_pair(C_HEADER, curses.COLOR_BLACK,  curses.COLOR_CYAN)
    if curses.COLORS >= 256:
        curses.init_pair(C_ORANGE, 208, -1)
        curses.init_pair(C_LIME,   154, -1)
    else:
        curses.init_pair(C_ORANGE, curses.COLOR_YELLOW, -1)
        curses.init_pair(C_LIME,   curses.COLOR_GREEN,  -1)


def safe_addstr(win, y, x, s, attr=0):
    h, w = win.getmaxyx()
    if y >= h or x >= w:
        return
    try:
        win.addstr(y, x, s[:w - x], attr)
    except curses.error:
        pass


def violation_rows(violations, watch_results=None):
    n = len(violations.get('cycles', [])) + len(violations.get('private', []))
    if watch_results:
        n += sum(1 for s, _, _ in watch_results if s == 'FAIL')
    return 0 if n == 0 else 1 + min(n, 6)


def _activity_change_label(stats):
    if not stats or not stats.get('changed'):
        return f"{'—':>10}", curses.A_DIM
    added = stats.get('added', 0)
    removed = stats.get('removed', 0)
    magnitude = added + removed
    label = f"+{added}/-{removed}"
    if magnitude >= 200:
        attr = curses.color_pair(C_RED) | curses.A_BOLD
    elif magnitude >= 50:
        attr = curses.color_pair(C_ORANGE)
    else:
        attr = curses.color_pair(C_CYAN)
    return f"{label:>10}"[:10], attr


def _band_attr(value, bands):
    palette = [C_GREEN, C_LIME, C_YELLOW, C_ORANGE, C_RED]
    for i, b in enumerate(bands):
        if value <= b:
            return curses.color_pair(palette[min(i, 4)])
    return curses.color_pair(C_RED)


def _churn_display(session_m1):
    if session_m1 >= 0.75:
        return f"{session_m1:>5.0%}", curses.color_pair(C_RED)    | curses.A_BOLD
    if session_m1 >= 0.50:
        return f"{session_m1:>5.0%}", curses.color_pair(C_RED)
    if session_m1 >= 0.25:
        return f"{session_m1:>5.0%}", curses.color_pair(C_ORANGE)
    if session_m1 >= 0.10:
        return f"{session_m1:>5.0%}", curses.color_pair(C_YELLOW)
    if session_m1 > 0:
        return f"{session_m1:>5.0%}", curses.A_DIM
    return f"{'—':>5}", curses.A_DIM


def _age_label(seconds):
    if seconds is None or seconds < 0:
        return f"{'—':>5}", curses.A_DIM
    minutes = int(seconds // 60)
    if minutes < 60:
        m = max(1, minutes)
        return f"{m}m".rjust(5), curses.color_pair(C_GREEN)
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h".rjust(5), curses.color_pair(C_LIME)
    days = hours // 24
    if days < 7:
        return f"{days}d".rjust(5), curses.color_pair(C_YELLOW)
    weeks = days // 7
    if weeks < 4:
        return f"{weeks}w".rjust(5), curses.color_pair(C_ORANGE)
    months = max(1, days // 30)
    return f"{months}M".rjust(5), curses.color_pair(C_RED) | curses.A_BOLD


def _fan_attr(n):
    if n >= 6:
        return curses.color_pair(C_RED) | curses.A_BOLD
    if n >= 3:
        return curses.color_pair(C_ORANGE)
    if n >= 1:
        return curses.color_pair(C_YELLOW)
    return curses.A_DIM


def draw_main_view(stdscr, rows, scroll_offset, summary, last_updated, target_dir, thresholds,
                   cell_counts, flash_msg='', show_matrix=True, violations=None, churn_map=None,
                   folder_metrics=None, activity=None, activity_folders=None,
                   fan_in_map=None, watch_results=None):
    fan_in_map = fan_in_map or {}
    violations = violations or {}
    churn_map  = churn_map  or {}
    watch_results = watch_results or []
    folder_metrics = folder_metrics or {}
    activity = activity or {'files': {}}
    activity_folders = activity_folders or {}
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    ts = last_updated[:19].replace('T', ' ') if last_updated else '—'

    x = 1
    for s in FILE_CPAIR:
        count = summary.get(s.lower(), 0)
        seg = f" {FILE_DOT[s]} {STATUS_LETTER[s]} {count}  "
        if x + len(seg) >= w:
            break
        safe_addstr(stdscr, 0, x, seg, curses.color_pair(FILE_CPAIR[s]) | curses.A_BOLD)
        x += len(seg)

    name_w = max(18, w - 47)
    safe_addstr(stdscr, 1, 0,
                f" {'file':<{name_w - 1}}{'CHG':>10}{'LOC':>6}{'DEPS':>5}{'USED':>5}{'DOC':>6}{'WATCH':>6}  "[:w],
                curses.A_DIM)
    chg_x    = name_w
    loc_x    = name_w + 10
    deps_x   = name_w + 16
    fan_x    = name_w + 21
    doc_x    = name_w + 26
    watch_x  = name_w + 32
    status_x = name_w + 38

    _loc_bands  = thresholds.get('loc_bands',  [150, 300, 450, 600, 750])
    _deps_bands = thresholds.get('deps_bands', [4, 8, 12, 16, 20])

    matrix_rows = MATRIX_ROWS if show_matrix else 0
    viol_rows   = violation_rows(violations, watch_results)
    content_h   = max(0, h - CHROME_ROWS - matrix_rows - viol_rows - FOOTER_ROWS)
    visible     = rows[scroll_offset: scroll_offset + content_h]

    for i, row in enumerate(visible):
        y = i + CHROME_ROWS
        if row[0] == 'dir':
            _, depth, name, dir_path, is_coll = row
            indicator = '▶' if is_coll else '▼'
            metric = folder_metrics.get(dir_path, {})
            activity_metric = activity_folders.get(dir_path, {})
            name_field = (f"{'  ' * depth}{indicator} {name}/")[: name_w - 1]
            dir_attr = curses.color_pair(C_CYAN) | curses.A_DIM
            safe_addstr(stdscr, y, 0, f" {name_field:<{name_w - 1}}", dir_attr)
            chg_label, chg_attr = _activity_change_label(activity_metric)
            safe_addstr(stdscr, y, chg_x, chg_label, chg_attr)
            safe_addstr(stdscr, y, loc_x, f"{metric.get('loc', 0):>6}",
                        curses.color_pair(C_CYAN) | curses.A_BOLD)
            out_refs = metric.get('out_refs', 0)
            if out_refs >= 10:
                out_attr = curses.color_pair(C_RED) | curses.A_BOLD
            elif out_refs >= 5:
                out_attr = curses.color_pair(C_ORANGE)
            else:
                out_attr = curses.color_pair(C_CYAN)
            safe_addstr(stdscr, y, deps_x, f"{out_refs:>5}", out_attr)
            in_refs = metric.get('in_refs', 0)
            safe_addstr(stdscr, y, fan_x, f"{in_refs:>5}", _fan_attr(in_refs))
            doc_lbl, doc_attr = _age_label(metric.get('doc_age_s'))
            watch_lbl, watch_attr = _age_label(metric.get('watch_age_s'))
            safe_addstr(stdscr, y, doc_x, f" {doc_lbl}", doc_attr)
            safe_addstr(stdscr, y, watch_x, f" {watch_lbl}", watch_attr)
            safe_addstr(stdscr, y, status_x, "  ◈ F", curses.color_pair(C_CYAN) | curses.A_DIM)
        else:
            _, depth, name, f = row
            status     = f['status']
            name_field = ('  ' * depth + name)[: name_w - 1]
            color      = curses.color_pair(FILE_CPAIR[status])
            extra      = curses.A_BOLD if status in ('RED', 'ORANGE') else 0
            activity_metric = activity.get('files', {}).get(f['path'], {})

            safe_addstr(stdscr, y, 0,           f" {name_field:<{name_w - 1}}", color | extra)
            chg_label, chg_attr = _activity_change_label(activity_metric)
            safe_addstr(stdscr, y, chg_x,       chg_label, chg_attr)
            safe_addstr(stdscr, y, loc_x,       f"{f['loc']:>6}",  _band_attr(f['loc'],  _loc_bands))
            safe_addstr(stdscr, y, deps_x,      f"{f['deps']:>5}", _band_attr(f['deps'], _deps_bands))
            fan = fan_in_map.get(f['path'], 0)
            safe_addstr(stdscr, y, fan_x,       f"{fan:>5}", _fan_attr(fan))
            safe_addstr(stdscr, y, doc_x,       f"{'—':>6}", curses.A_DIM)
            safe_addstr(stdscr, y, watch_x,     f"{'—':>6}", curses.A_DIM)
            safe_addstr(stdscr, y, status_x,    f"  {FILE_DOT[status]} {STATUS_LETTER[status]}", color | extra)

    if show_matrix:
        loc_bands = thresholds.get('loc_bands', [150, 300, 450, 600, 750])
        dep_bands = thresholds.get('deps_bands', [4, 8, 12, 16, 20])
        N, cell_w, rlabel_w = 5, 8, 9

        safe_addstr(stdscr, CHROME_ROWS + content_h, 0, ' ' * rlabel_w, curses.A_DIM)
        for ci in range(N):
            lbl      = f"DEPS≤{dep_bands[ci]}" if ci < len(dep_bands) else ''
            col_attr = curses.color_pair(FILE_CPAIR[DIAG_STATUS[ci]])
            safe_addstr(stdscr, CHROME_ROWS + content_h, rlabel_w + ci * cell_w, f"{lbl:^{cell_w}}", col_attr)

        for ri in range(N):
            y        = CHROME_ROWS + content_h + 1 + ri
            lbl      = f"LOC≤{loc_bands[ri]}" if ri < len(loc_bands) else ''
            row_attr = curses.color_pair(FILE_CPAIR[DIAG_STATUS[ri]])
            safe_addstr(stdscr, y, 0, f"{lbl:<{rlabel_w}}", row_attr)
            for ci in range(N - ri):
                d      = ri + ci
                status = DIAG_STATUS[d] if d < len(DIAG_STATUS) else 'RED'
                count  = cell_counts[ri][ci] if ri < len(cell_counts) and ci < len(cell_counts[ri]) else 0
                cell   = f" {FILE_DOT.get(status, '?')} {count}"
                safe_addstr(stdscr, y, rlabel_w + ci * cell_w, f"{cell:<{cell_w}}",
                            curses.color_pair(FILE_CPAIR.get(status, 0)) | curses.A_BOLD)

    if viol_rows > 0:
        viol_y    = CHROME_ROWS + content_h + matrix_rows
        all_items = []
        for c in violations.get('cycles', []):
            chain = ' → '.join(c['files'])
            all_items.append(('cycle', f"[CYCLE] {chain}"))
        for p in violations.get('private', []):
            all_items.append(('cycle', f"[PRIV]  {p['importer'].split('/')[-1]} imports {p['member']} from {p['module']}"))
        for status, name, reason in (watch_results or []):
            if status == 'FAIL':
                all_items.append(('watch_fail', f"[WATCH] {name}: FAIL — {reason}"))

        sep = '─' * (w - 2)
        safe_addstr(stdscr, viol_y, 0, f" {sep[:w - 2]} "[:w],
                    curses.color_pair(C_RED) | curses.A_BOLD)
        for idx, (kind, text) in enumerate(all_items[:6]):
            attr = curses.color_pair(C_RED) | curses.A_BOLD
            safe_addstr(stdscr, viol_y + 1 + idx, 0, f" {text}"[:w], attr)

    activity_note = ''
    if activity and activity.get('files'):
        label = activity.get('label') or 'checkpoint'
        activity_note = f" │ activity: changes since {label}"
    footer = f" {target_dir}  {ts}  │ dirs: LOC=recursive, DEPS=OUT refs" + activity_note + ' '
    safe_addstr(stdscr, h - 2, 0, footer.ljust(w)[:w],
                curses.color_pair(C_HEADER) | curses.A_BOLD)

    end_row  = min(scroll_offset + content_h, len(rows))
    pos_info = f" {scroll_offset + 1}–{end_row}/{len(rows)} "
    hints    = " ↑↓/jk  PgUp/PgDn  g/G  r/R refresh  m matrix  w watch  l loc  d dep  U churn  L/D/S ranked  t expand  a collapse  c copy  b bugs  q quit"
    if flash_msg:
        safe_addstr(stdscr, h - 1, 0, flash_msg.center(w)[:w], curses.A_BOLD)
    else:
        safe_addstr(stdscr, h - 1, 0, (hints + pos_info.rjust(w - len(hints)))[:w], curses.A_DIM)

    stdscr.refresh()


def draw_watch_view(stdscr, entries, watch_results, scroll, cursor, flash_msg=''):
    """Full-screen watch management view — folder-based."""
    stdscr.erase()
    h, w = stdscr.getmaxyx()

    # Header
    total = len(entries)
    enabled = sum(1 for e in entries if not e['unfilled'] and not e['disabled'] and not e['dismissed'])
    disabled = sum(1 for e in entries if e['disabled'])
    unfilled = sum(1 for e in entries if e['unfilled'])
    fails = sum(1 for s, _, _ in watch_results if s == 'FAIL')
    passes = sum(1 for s, _, _ in watch_results if s == 'PASS')
    header = f" Watches: {total} folders  enabled:{enabled}  disabled:{disabled}  unfilled:{unfilled}  PASS:{passes}  FAIL:{fails} "
    safe_addstr(stdscr, 0, 0, header.ljust(w)[:w], curses.color_pair(C_HEADER) | curses.A_BOLD)

    # Dynamic column layout — folder gets remaining space
    state_w  = 10  # " dismissed" / "  disabled" / "  unfilled" / "   enabled"
    result_w = 8   # "    FAIL" / "    PASS" / "       —"
    right_fixed = state_w + result_w  # 18 chars for state+result on the right
    folder_w = max(20, w - right_fixed - 2)  # -2 for left padding + gap
    state_x  = folder_w + 1
    result_x = state_x + state_w
    detail_x = result_x + result_w

    # Column headers
    safe_addstr(stdscr, 1, 0,
                f" {'folder':<{folder_w}}{'state':>{state_w}}{'result':>{result_w}}"[:w],
                curses.A_DIM)

    content_h = max(0, h - 4)
    result_map = {folder: (status, reason) for status, folder, reason in watch_results}

    visible = entries[scroll:scroll + content_h]
    for i, entry in enumerate(visible):
        y = i + 2
        folder = entry['folder']
        is_cursor = (scroll + i) == cursor
        row_attr = curses.A_REVERSE if is_cursor else 0

        folder_str = f" {folder:<{folder_w}}"[:folder_w + 1]

        # State column
        if entry['dismissed']:
            state_str = ' dismissed'
            state_attr = curses.A_DIM
        elif entry['disabled']:
            state_str = '  disabled'
            state_attr = curses.color_pair(C_YELLOW)
        elif entry['unfilled']:
            state_str = '  unfilled'
            state_attr = curses.color_pair(C_ORANGE)
        else:
            state_str = '   enabled'
            state_attr = curses.color_pair(C_GREEN)

        # Result column
        run_status, reason = result_map.get(folder, ('', ''))
        if run_status == 'FAIL':
            result_str = '    FAIL'
            result_attr = curses.color_pair(C_RED) | curses.A_BOLD
        elif run_status == 'PASS':
            result_str = '    PASS'
            result_attr = curses.color_pair(C_GREEN) | curses.A_BOLD
        else:
            result_str = '       —'
            result_attr = curses.A_DIM

        detail = reason if run_status == 'FAIL' else ''

        safe_addstr(stdscr, y, 0, ' ' * w, row_attr)
        safe_addstr(stdscr, y, 0, folder_str, row_attr | curses.A_BOLD)
        safe_addstr(stdscr, y, state_x, state_str, state_attr | (curses.A_REVERSE if is_cursor else 0))
        safe_addstr(stdscr, y, result_x, result_str, result_attr | (curses.A_REVERSE if is_cursor else 0))
        if detail and detail_x < w:
            safe_addstr(stdscr, y, detail_x, f"  {detail}"[:w - detail_x], row_attr | curses.A_DIM)

    # Footer
    end_row = min(scroll + content_h, total)
    pos_info = f" {scroll + 1}–{end_row}/{total} "
    safe_addstr(stdscr, h - 2, 0, ' ' * w, curses.color_pair(C_HEADER) | curses.A_BOLD)
    safe_addstr(stdscr, h - 2, 0, pos_info, curses.color_pair(C_HEADER) | curses.A_BOLD)

    hints = " ↑↓/jk scroll  d/Space toggle enable  Enter run selected  R run all  w/q back"
    if flash_msg:
        safe_addstr(stdscr, h - 1, 0, flash_msg.center(w)[:w], curses.A_BOLD)
    else:
        safe_addstr(stdscr, h - 1, 0, hints[:w], curses.A_DIM)

    stdscr.refresh()
