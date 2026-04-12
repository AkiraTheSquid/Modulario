"""
Modulario — churn history tracking and aggregation.
Imported by modulario-analyze.py.
"""
import json
from datetime import datetime


def _parse_ts(ts):
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def compute_churn_score(sessions_touched, days_touched, span_days, thresholds):
    """Weight churn across distinct days higher than same-day revision loops."""
    scoring = thresholds.get('churn_scoring', {})
    day_weight = scoring.get('day_weight', 1.0)
    span_weight = scoring.get('span_day_weight', 0.2)
    same_day_repeat_weight = scoring.get('same_day_repeat_weight', 0.25)

    same_day_retouches = max(0, sessions_touched - days_touched)
    score = (
        days_touched * day_weight
        + span_days * span_weight
        + same_day_retouches * same_day_repeat_weight
    )
    return round(score, 2), same_day_retouches


def churn_rule_for_path(path, thresholds):
    rules = thresholds.get('churn_file_rules', {})
    return rules.get(path, {}) if isinstance(rules, dict) else {}


def churn_paths(output_path):
    """Return (history_jsonl_path, churn_json_path) for a given state file path."""
    stem = output_path[:-5] if output_path.endswith('.json') else output_path
    return stem + '_history.jsonl', stem + '_churn.json'


def append_history(history_path, ts, old_files, new_files):
    """Append LOC deltas to history.jsonl.

    Skips if old_files is empty (first run — no baseline yet).
    Only records files that already existed and changed LOC; new file
    additions are never counted as churn.
    """
    if not old_files:
        return
    old_loc = {f['path']: f['loc'] for f in old_files}
    changed = []
    deltas  = {}
    for f in new_files:
        old = old_loc.get(f['path'])
        if old is not None and f['loc'] != old:
            changed.append(f['path'])
            deltas[f['path']] = f['loc'] - old
    if not changed:
        return
    line = json.dumps({'ts': ts, 'changed': changed, 'deltas': deltas})
    try:
        with open(history_path, 'a', encoding='utf-8') as fh:
            fh.write(line + '\n')
    except OSError:
        pass


def get_churn_status(path, sessions_touched, relative_churn, churn_score, thresholds):
    ct = thresholds.get('churn_thresholds', {'high': 5, 'critical': 8})
    rule = churn_rule_for_path(path, thresholds)
    if rule.get('disabled'):
        return 'DISABLED'

    high     = rule.get('high', ct.get('high', 5))
    critical = rule.get('critical', ct.get('critical', 8))
    med      = max(1, high // 2)
    if churn_score >= critical or abs(relative_churn) >= 0.75:
        return 'CRITICAL'
    if churn_score >= high:
        return 'HIGH'
    if churn_score >= med or sessions_touched >= high:
        return 'MEDIUM'
    return 'LOW'


def update_churn(churn_path, history_path, new_files, thresholds):
    """Recompute per-file churn aggregates from the last window_size history entries."""
    window_size = thresholds.get('churn_window', 20)

    records = []
    try:
        with open(history_path, 'r', encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass

    window = records[-window_size:] if len(records) > window_size else records

    # Identify current session (most recent entries within session_gap_hours)
    gap_secs = thresholds.get('session_gap_hours', 4) * 3600
    session_entries = []
    if records:
        session_entries = [records[-1]]
        for i in range(len(records) - 2, -1, -1):
            try:
                t_next = datetime.fromisoformat(records[i + 1]['ts'])
                t_cur  = datetime.fromisoformat(records[i]['ts'])
                if (t_next - t_cur).total_seconds() > gap_secs:
                    break
            except (ValueError, AttributeError):
                break
            session_entries.append(records[i])
    session_worked_map = {}
    for entry in session_entries:
        for path, delta in entry.get('deltas', {}).items():
            session_worked_map[path] = session_worked_map.get(path, 0) + abs(delta)

    sessions_touched_map = {}
    total_delta_map      = {}
    last_touched_map     = {}
    first_touched_map    = {}
    touched_days_map     = {}
    for record in window:
        ts = record.get('ts', '')
        ts_dt = _parse_ts(ts)
        for path in record.get('changed', []):
            sessions_touched_map[path] = sessions_touched_map.get(path, 0) + 1
            if ts > last_touched_map.get(path, ''):
                last_touched_map[path] = ts
            if path not in first_touched_map or ts < first_touched_map[path]:
                first_touched_map[path] = ts
            if ts_dt is not None:
                touched_days_map.setdefault(path, set()).add(ts_dt.date().isoformat())
        for path, delta in record.get('deltas', {}).items():
            total_delta_map[path] = total_delta_map.get(path, 0) + delta

    loc_map     = {f['path']: f['loc'] for f in new_files}
    files_churn = {}
    for path, loc in loc_map.items():
        st  = sessions_touched_map.get(path, 0)
        td  = total_delta_map.get(path, 0)
        sw  = session_worked_map.get(path, 0)
        rc  = td / loc if loc > 0 else 0.0
        sm1 = round(sw / max(loc, 1), 3)
        days_touched = len(touched_days_map.get(path, set()))
        first_dt = _parse_ts(first_touched_map.get(path, ''))
        last_dt = _parse_ts(last_touched_map.get(path, ''))
        span_days = 0
        if first_dt is not None and last_dt is not None:
            span_days = max(0, (last_dt.date() - first_dt.date()).days)
        churn_score, same_day_retouches = compute_churn_score(st, days_touched, span_days, thresholds)
        files_churn[path] = {
            'sessions_touched': st,
            'days_touched':     days_touched,
            'touch_span_days':  span_days,
            'same_day_retouches': same_day_retouches,
            'churn_score':      churn_score,
            'total_loc_delta':  td,
            'relative_churn':   round(rc, 4),
            'session_m1':       sm1,
            'last_touched':     last_touched_map.get(path, ''),
            'churn_status':     get_churn_status(path, st, rc, churn_score, thresholds),
            'churn_rule':       churn_rule_for_path(path, thresholds),
        }

    session_files_touched = len(session_worked_map)

    churn_data = {
        'window_size':           window_size,
        'last_updated':          datetime.now().isoformat(),
        'session_files_touched': session_files_touched,
        'files':                 files_churn,
    }
    try:
        with open(churn_path, 'w', encoding='utf-8') as fh:
            json.dump(churn_data, fh, indent=2)
    except OSError:
        pass

    return churn_data
