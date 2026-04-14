"""
Modulario TUI — churn computation across time horizons.
Imported by modulario-tui.py and churn_view.py.
"""
from datetime import datetime, timedelta

HORIZONS       = ['session', 'daily', 'weekly']
HORIZON_LABELS = ['Session', '24h', '7d']


def _horizon_worked_map(history, hours, now=None):
    """Sum abs LOC deltas per file for history entries within `hours` of now."""
    if now is None:
        now = datetime.now()
    cutoff = now - timedelta(hours=hours)
    worked = {}
    for entry in history:
        try:
            ts = datetime.fromisoformat(entry['ts'])
        except (ValueError, KeyError):
            continue
        if ts >= cutoff:
            for path, delta in entry.get('deltas', {}).items():
                worked[path] = worked.get(path, 0) + abs(delta)
    return worked


def build_horizon_rows(horizon, churn_map, files_list, history=None, now=None):
    """
    Build rows for the churn view sorted by churn rate (highest first).
    Returns (rows, files_touched, total_files).

    Each row: {path, loc, churn_pct, sessions_touched}
      churn_pct        — lines worked in horizon / current LOC  (0.33 = 33%)
      sessions_touched — how many of the last N runs touched this file
    """
    history = history or []
    if horizon == 'daily':
        worked_map = _horizon_worked_map(history, 24, now)
    elif horizon == 'weekly':
        worked_map = _horizon_worked_map(history, 24 * 7, now)
    else:
        worked_map = None  # session — use pre-computed session_m1

    rows = []
    for f in files_list:
        path = f['path']
        loc  = max(f['loc'], 1)
        cd   = churn_map.get(path, {})
        if worked_map is None:
            pct = cd.get('session_m1', 0.0)
        else:
            pct = worked_map.get(path, 0) / loc
        rows.append({
            'path':             path,
            'loc':              f['loc'],
            'churn_pct':        pct,
            'sessions_touched': cd.get('sessions_touched', 0),
        })
    rows.sort(key=lambda r: (-r['churn_pct'], r['path']))
    files_touched = sum(1 for r in rows if r['churn_pct'] > 0)
    return rows, files_touched, len(rows)
