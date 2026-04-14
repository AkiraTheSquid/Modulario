from pathlib import Path


def snapshot_files(files):
    return {f['path']: {'loc': f['loc'], 'deps': f.get('deps', 0)} for f in files}


def _format_delta(delta):
    return {'added': max(delta, 0), 'removed': max(-delta, 0)}


def activity_from_diff(baseline, files, label=None):
    baseline = baseline or {}
    rows = {f['path']: f for f in files}
    files_changed = {}
    latest_changed = None
    for path in sorted(set(baseline) | set(rows)):
        prev = baseline.get(path, {})
        prev_loc = prev.get('loc', 0)
        curr = rows.get(path)
        curr_loc = curr['loc'] if curr else 0
        delta = curr_loc - prev_loc
        if delta == 0:
            continue
        metric = {
            'read_loc': curr_loc if curr else None,
            'changed': True,
        }
        metric.update(_format_delta(delta))
        if curr:
            metric['deps'] = curr.get('deps', 0)
        files_changed[path] = metric
        latest_changed = path
    if not files_changed:
        return empty_activity(label=label)
    return {
        'files': files_changed,
        'latest_changed': latest_changed,
        'label': label,
    }


def empty_activity(label=None):
    return {'files': {}, 'latest_changed': None, 'label': label}


def build_activity_folder_metrics(activity):
    metrics = {}
    for rel_path, stats in (activity or {}).get('files', {}).items():
        parts = Path(rel_path).parts
        prefix = ''
        for part in parts[:-1]:
            prefix = prefix + part + '/'
            metric = metrics.setdefault(prefix, {'added': 0, 'removed': 0, 'changed': False})
            if stats.get('changed'):
                metric['added'] += stats.get('added', 0)
                metric['removed'] += stats.get('removed', 0)
                metric['changed'] = True
    return metrics


def activity_open_dirs(activity):
    forced = set()
    for rel_path in (activity or {}).get('files', {}):
        prefix = ''
        for part in Path(rel_path).parts[:-1]:
            prefix = prefix + part + '/'
            forced.add(prefix)
    return forced


def activity_closed_dirs(files, activity):
    touched_files = set((activity or {}).get('files', {}).keys())
    if not touched_files:
        return set()

    touched_dirs = activity_open_dirs(activity)
    closed = set()
    for f in files:
        parts = Path(f['path']).parts
        prefix = ''
        for part in parts[:-1]:
            prefix = prefix + part + '/'
            if prefix not in touched_dirs:
                closed.add(prefix)
    return closed

