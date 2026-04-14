"""
Modulario TUI — file watcher and clipboard export utilities.
Imported by modulario-tui.py.
"""
import os
import subprocess
import threading


# ─── File system watcher ──────────────────────────────────────────────────────

class StateWatcher:
    def __init__(self, path, callback):
        self._path = path
        self._cb   = callback
        self._stop = threading.Event()

    def start(self):
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
            watcher = self

            class _H(FileSystemEventHandler):
                def on_modified(self, event):
                    if event.src_path == watcher._path:
                        watcher._cb()

            self._observer = Observer()
            self._observer.schedule(_H(), os.path.dirname(self._path) or '.')
            self._observer.start()
        except ImportError:
            t = threading.Thread(target=self._poll, daemon=True)
            t.start()

    def _poll(self):
        while not self._stop.is_set():
            try:
                mtime = os.path.getmtime(self._path)
                if mtime != getattr(self, '_last_mtime', None):
                    self._last_mtime = mtime
                    self._cb()
            except FileNotFoundError:
                pass
            self._stop.wait(1.0)

    def stop(self):
        self._stop.set()
        if hasattr(self, '_observer'):
            try:
                self._observer.stop()
            except Exception:
                pass

    def update_path(self, path):
        self._path = path
        try:
            self._last_mtime = os.path.getmtime(path)
        except FileNotFoundError:
            self._last_mtime = None


# ─── Clipboard export ─────────────────────────────────────────────────────────

_STATUS_ORDER  = ['RED', 'ORANGE', 'YELLOW', 'LIME', 'GREEN']
_FILE_DOT      = {'RED': '●', 'ORANGE': '◆', 'YELLOW': '◆', 'LIME': '◆', 'GREEN': '●'}
_STATUS_LETTER = {'RED': 'R', 'ORANGE': 'O', 'YELLOW': 'Y', 'LIME': 'L', 'GREEN': 'G'}
_DIAG_STATUS   = ['GREEN', 'LIME', 'YELLOW', 'ORANGE', 'RED']
def _churn_pct(session_m1):
    return f"{session_m1:.0%}" if session_m1 > 0 else '—'


def _folder_loc_label(metric):
    return str(metric.get('loc', 0)) if metric else '—'


def _folder_out_label(metric):
    return str(metric.get('out_refs', 0)) if metric else '—'


def _folder_bug_label(metric):
    bug_pct = (metric or {}).get('bug_pct')
    return f"{bug_pct:.0f}%" if bug_pct is not None else '—'


def build_text_dump(rows, summary, last_updated, target_dir, thresholds, cell_counts,
                    violations=None, churn_map=None, folder_metrics=None,
                    activity=None):
    churn_map = churn_map or {}
    folder_metrics = folder_metrics or {}
    activity = activity or {'files': {}}
    lines = []
    ts = last_updated[:19].replace('T', ' ') if last_updated else '—'
    lines.append(f"[Modulario] {target_dir}  {ts}")

    parts = [f"{_FILE_DOT[s]} {_STATUS_LETTER[s]} {summary.get(s.lower(), 0)}" for s in _STATUS_ORDER]
    parts.append(f"Total: {summary.get('total', 0)}")
    lines.append('  '.join(parts))
    lines.append('')

    lines.append(f"{'file':<52} {'CHG':>10} {'LOC':>6} {'DEPS':>5}  status")
    for row in rows:
        if row[0] == 'dir':
            depth, name, dir_path = row[1], row[2], row[3]
            metric = folder_metrics.get(dir_path, {})
            label = f"{'  ' * depth}{name}/"
            added_total = 0
            removed_total = 0
            changed = False
            for rel_path, info in activity.get('files', {}).items():
                if rel_path.startswith(dir_path):
                    if info.get('changed'):
                        added_total += info.get('added', 0)
                        removed_total += info.get('removed', 0)
                        changed = True
            chg_label = f"+{added_total}/-{removed_total}" if changed else '—'
            lines.append(
                f"{label:<52} {chg_label:>10} {_folder_loc_label(metric):>6} {_folder_out_label(metric):>5}  folder"
            )
        else:
            _, depth, name, f = row
            status      = f['status']
            name_field  = ('  ' * depth + name)[:51]
            info = activity.get('files', {}).get(f['path'], {})
            chg_label = f"+{info.get('added', 0)}/-{info.get('removed', 0)}" if info.get('changed') else '—'
            lines.append(
                f"{name_field:<52} {chg_label:>10} {f['loc']:>6} {f['deps']:>5}"
                f"  {_FILE_DOT[status]} {_STATUS_LETTER[status]}"
            )
    lines.append('')

    loc_bands = thresholds.get('loc_bands', [150, 300, 450, 600, 750])
    dep_bands = thresholds.get('deps_bands', [4, 8, 12, 16, 20])
    N, cell_w, rlabel_w = 5, 10, 9

    header = ' ' * rlabel_w
    for ci in range(N):
        lbl = f"DEPS≤{dep_bands[ci]}" if ci < len(dep_bands) else ''
        header += f"{lbl:^{cell_w}}"
    lines.append(header)

    for ri in range(N):
        lbl  = f"LOC≤{loc_bands[ri]}" if ri < len(loc_bands) else ''
        line = f"{lbl:<{rlabel_w}}"
        for ci in range(N - ri):
            d      = ri + ci
            status = _DIAG_STATUS[d] if d < len(_DIAG_STATUS) else 'RED'
            count  = cell_counts[ri][ci] if ri < len(cell_counts) and ci < len(cell_counts[ri]) else 0
            cell   = f"{_FILE_DOT[status]} {_STATUS_LETTER[status]} {count}"
            line  += f"{cell:^{cell_w}}"
        lines.append(line)

    cycles = (violations or {}).get('cycles', [])
    priv   = (violations or {}).get('private', [])
    if cycles or priv:
        lines.append('')
        lines.append('Boundary violations:')
        for c in cycles:
            files = c['files']
            lines.append(f"  [CYCLE] {files[0]}")
            for f in files[1:]:
                lines.append(f"       → {f}")
        for p in priv:
            lines.append(f"  [PRIV]  {p['importer']} imports {p['member']} from {p['module']}")

    return '\n'.join(lines)


def copy_to_clipboard(text):
    for cmd in (['xclip', '-selection', 'clipboard'],
                ['xsel', '--clipboard', '--input'],
                ['pbcopy']):
        try:
            proc = subprocess.run(cmd, input=text, text=True, capture_output=True, timeout=3)
            if proc.returncode == 0:
                return True, f"Copied! ({cmd[0]})"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return False, "No clipboard tool — install xclip or xsel"
