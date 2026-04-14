"""Folder-level metrics + file-tree flattening for the Modulario TUI.

`build_folder_metrics` aggregates per-folder LOC and in/out import counts used
by the main-view folder rows. `flatten_tree` turns the state.json file list
into a scrollable sequence of (dir, file) rows, honoring the user's collapse
set so nested folders can be expanded and re-collapsed interactively.
"""
import os
import time
from pathlib import Path


def build_folder_metrics(files, import_graph, target_dir=None):
    metrics = {}
    file_paths = {f['path'] for f in files}
    now_ts = time.time()

    def _mtime_age(folder_rel, name):
        if not target_dir:
            return None
        try:
            return now_ts - os.path.getmtime(os.path.join(target_dir, folder_rel, name))
        except OSError:
            return None

    def _ensure(folder):
        return metrics.setdefault(folder, {
            'loc': 0,
            'out_targets': set(),
            'in_sources': set(),
        })

    for f in files:
        parts = Path(f['path']).parts
        prefix = ''
        for part in parts[:-1]:
            prefix = prefix + part + '/'
            _ensure(prefix)['loc'] += f['loc']

    for src, targets in (import_graph or {}).items():
        src_parts = Path(src).parts
        for dep in targets:
            if dep not in file_paths:
                continue
            dep_parts = Path(dep).parts
            for depth in range(1, len(src_parts)):
                folder = '/'.join(src_parts[:depth]) + '/'
                if dep.startswith(folder):
                    continue
                _ensure(folder)['out_targets'].add(dep)
            for depth in range(1, len(dep_parts)):
                folder = '/'.join(dep_parts[:depth]) + '/'
                if src.startswith(folder):
                    continue
                _ensure(folder)['in_sources'].add(src)

    result = {}
    for folder, metric in metrics.items():
        result[folder] = {
            'loc': metric.get('loc', 0),
            'out_refs': len(metric.get('out_targets', set())),
            'in_refs': len(metric.get('in_sources', set())),
            'doc_age_s': _mtime_age(folder, 'README.md'),
            'watch_age_s': _mtime_age(folder, 'watch.py'),
        }
    return result


def all_dir_paths(files):
    """Return the set of all directory path strings present in the file tree."""
    dirs = set()
    for f in files:
        parts = Path(f['path']).parts
        prefix = ''
        for part in parts[:-1]:
            prefix = prefix + part + '/'
            dirs.add(prefix)
    return dirs


def flatten_tree(files, collapsed=None):
    collapsed = collapsed or set()
    tree = {}
    for f in files:
        parts = Path(f['path']).parts
        node = tree
        for p in parts[:-1]:
            node = node.setdefault(('d', p), {})
        node[('f', parts[-1])] = f
    rows = []
    _flatten_node(tree, rows, 0, '', collapsed)
    return rows


def _flatten_node(node, rows, depth, path_prefix, collapsed):
    dirs  = sorted(k for k in node if k[0] == 'd')
    files = sorted(k for k in node if k[0] == 'f')
    for k in dirs:
        dir_name = k[1]
        dir_path = path_prefix + dir_name + '/'
        is_coll  = dir_path in collapsed
        rows.append(('dir', depth, dir_name, dir_path, is_coll))
        if not is_coll:
            _flatten_node(node[k], rows, depth + 1, dir_path, collapsed)
    for k in files:
        rows.append(('file', depth, k[1], node[k]))
