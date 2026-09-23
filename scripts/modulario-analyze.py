#!/usr/bin/env python3
"""
Modulario analyzer — computes LOC and DEPS per file, writes state.json.

Usage:
  python3 modulario-analyze.py --target <dir> --output <state.json>
                                [--thresholds <thresholds.json>]
                                [--changed-file <path>]
                                [--print-summary]
                                [--check-violations]
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from cli_common import BASE_SKIP_DIRS, filter_walk_dirs
from counters import (count_loc_python, count_loc_js, count_loc_css,
                      count_deps_python, count_deps_js, count_deps_css, assign_status)
from violations import (collect_graph_data, find_violations,
                        file_in_violations, format_violation_block)
from import_alerts import build_import_alerts
from folder_templates import DOC_MARKER, WATCH_MARKER, readme_template, watch_template
from coupling_tracker import coupling_path, update_coupling, find_strong_pairs, interpret_coupling
from session_scope import compute_graph_scope, update_session_scope
import stopgate_config

SKIP_DIRS = BASE_SKIP_DIRS

SUPPORTED_EXTENSIONS = {'.py', '.js', '.ts', '.jsx', '.tsx', '.mjs', '.cjs', '.css'}


# ─── Threshold loading ────────────────────────────────────────────────────────

def load_thresholds(path):
    defaults = {
        "loc_bands":  [150, 300, 450, 600, 750],
        "deps_bands": [4, 8, 12, 16, 20],
        "violation_ignore": [],
        "skip_dirs": [],
        "coupling_window": 30,
        "coupling_min_sessions": 20,
        "coupling_min_count": 5,
        "coupling_thresholds": {
            "moderate": 0.3,
            "strong": 0.5,
            "critical": 0.7,
        },
    }
    if path and os.path.exists(path):
        try:
            with open(path) as f:
                loaded = json.load(f)
                for k in (
                    "loc_bands", "deps_bands", "violation_ignore", "skip_dirs",
                    "coupling_window", "coupling_min_sessions",
                    "coupling_min_count", "coupling_thresholds",
                ):
                    if k in loaded:
                        defaults[k] = loaded[k]
        except Exception:
            pass
    return defaults


# ─── Per-file analysis ────────────────────────────────────────────────────────

def analyze_file(filepath, thresholds, local_packages=None, files_set=None):
    ext = Path(filepath).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return None
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except Exception:
        return None

    if ext == '.py':
        loc  = count_loc_python(content)
        deps = count_deps_python(content, local_packages)
    elif ext == '.css':
        loc  = count_loc_css(content)
        deps = count_deps_css(content)
    else:
        loc  = count_loc_js(content)
        deps = count_deps_js(content)

    graph_edges        = []
    private_violations = []
    if files_set is not None:
        graph_edges, private_violations = collect_graph_data(
            content, filepath, ext, files_set, local_packages
        )

    return {
        'loc':                loc,
        'deps':               deps,
        'status':             assign_status(loc, deps, thresholds),
        'graph_edges':        graph_edges,
        'private_violations': private_violations,
    }


# ─── Directory walk ───────────────────────────────────────────────────────────

def find_local_packages(target_dir, skip_dirs=None):
    packages   = set()
    target_path = Path(target_dir).resolve()
    skip = list(skip_dirs or [])
    for root, dirs, files in os.walk(target_path):
        dirs[:] = filter_walk_dirs(root, dirs, target_path, skip)
        if any(f.endswith('.py') for f in files):
            packages.add(Path(root).name)
        for f in files:
            if f.endswith('.py'):
                packages.add(Path(f).stem)
    return packages


def walk_target(target_dir, thresholds):
    results     = []
    target_path = Path(target_dir).resolve()
    skip = list(thresholds.get('skip_dirs', []) or [])
    local_packages = find_local_packages(target_dir, skip_dirs=skip)

    files_set = set()
    for root, dirs, files in os.walk(target_path):
        dirs[:] = filter_walk_dirs(root, dirs, target_path, skip)
        for filename in files:
            fp = Path(root) / filename
            if fp.suffix.lower() in SUPPORTED_EXTENSIONS:
                files_set.add(str(fp.resolve()))

    import_graph = {}
    all_private  = []

    for root, dirs, files in os.walk(target_path):
        dirs[:] = filter_walk_dirs(root, dirs, target_path, skip)
        for filename in sorted(files):
            filepath = Path(root) / filename
            if filepath.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            stats = analyze_file(str(filepath), thresholds, local_packages, files_set)
            if stats is None:
                continue
            rel = str(filepath.relative_to(target_path))
            results.append({
                'path':      rel,
                'abs_path':  str(filepath),
                'loc':       stats['loc'],
                'deps':      stats['deps'],
                'status':    stats['status'],
                'loc_delta':  0,
                'deps_delta': 0,
            })
            if stats['graph_edges']:
                import_graph[str(filepath)] = stats['graph_edges']
            all_private.extend(stats['private_violations'])

    return results, import_graph, all_private


def merge_deltas(new_files, old_files):
    old_map = {f['path']: f for f in old_files}
    for f in new_files:
        old = old_map.get(f['path'])
        if old:
            f['loc_delta']  = f['loc']  - old['loc']
            f['deps_delta'] = f['deps'] - old['deps']
    return new_files


STATUSES = ['RED', 'ORANGE', 'YELLOW', 'LIME', 'GREEN']


def compute_summary(files):
    counts = {s: 0 for s in STATUSES}
    for f in files:
        counts[f['status']] = counts.get(f['status'], 0) + 1
    return {s.lower(): counts[s] for s in STATUSES} | {'total': len(files)}


def relativize_import_graph(import_graph, target_dir):
    target = Path(target_dir).resolve()
    rel_graph = {}
    for src_abs, targets_abs in import_graph.items():
        try:
            src_rel = str(Path(src_abs).resolve().relative_to(target))
        except ValueError:
            continue
        rel_targets = []
        for dep_abs in targets_abs:
            try:
                dep_rel = str(Path(dep_abs).resolve().relative_to(target))
            except ValueError:
                continue
            rel_targets.append(dep_rel)
        if rel_targets:
            rel_graph[src_rel] = sorted(set(rel_targets))
    return rel_graph



# ─── History helper ──────────────────────────────────────────────────────────

def _history_path_for(output_path):
    stem = output_path[:-5] if output_path.endswith('.json') else output_path
    return stem + '_history.jsonl'


def append_history(history_path, ts, old_files, new_files):
    """Append LOC deltas to history.jsonl. Used by coupling tracker."""
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


# ─── Summary for Claude (PostToolUse) ────────────────────────────────────────

def build_claude_summary(state, changed_file, thresholds, history=None,
                         coupling_data=None, modulario_dir=None, claude_cfg=None,
                         scope_files=None):
    cc = claude_cfg or {}
    files      = state['files']
    summary    = state['summary']
    violations = state.get('violations', {})
    lines      = []

    changed_name  = os.path.basename(changed_file) if changed_file else 'N/A'
    changed_stats = None
    if changed_file:
        for f in files:
            if f['abs_path'] == changed_file or f['path'].endswith(changed_file):
                changed_stats = f
                break

    red_pool = (f for f in files if f['status'] == 'RED')
    if scope_files:
        red_pool = (f for f in red_pool if f['path'] in scope_files)
    red_files = sorted(
        red_pool,
        key=lambda f: f['loc'] + f['deps'] * 20,
        reverse=True,
    )

    lines.append(f"[Modulario] PostToolUse: {changed_name}")
    lines.append("┌─ Changed file ──────────────────────────────────┐")
    if changed_stats:
        s     = changed_stats
        loc_d = f"{s['loc_delta']:+d}"  if s['loc_delta']  != 0 else " 0"
        dep_d = f"{s['deps_delta']:+d}" if s['deps_delta'] != 0 else " 0"
        lines.append(
            f"│ {s['path'][-38:]:<38}  LOC:{s['loc']:>4}({loc_d})  DEPS:{s['deps']:>3}({dep_d})  {s['status'].ljust(6)} │"
        )
    else:
        lines.append(f"│ {changed_name:<38}  (not in target directory)              │")
    if cc.get("red_hotspots", True):
        lines.append("├─ Red hotspots ──────────────────────────────────┤")
        if red_files:
            for f in red_files[:5]:
                lines.append(f"│ {f['path'][-38:]:<38}  LOC:{f['loc']:>4}  DEPS:{f['deps']:>3}                   │")
        else:
            lines.append("│ None — all files within thresholds.             │")
    lines.append("├─ Session totals ────────────────────────────────┤")
    lines.append(
        f"│ Red:{summary.get('red',0):<3} Org:{summary.get('orange',0):<3} Yel:{summary.get('yellow',0):<3}"
        f" Lim:{summary.get('lime',0):<3} Grn:{summary.get('green',0):<3} Tot:{summary['total']:<4}  │"
    )
    lines.append("└─────────────────────────────────────────────────┘")

    if changed_stats and cc.get("threshold_alerts", True):
        s          = changed_stats
        name       = os.path.basename(s['path'])
        loc_bands  = thresholds['loc_bands']
        deps_bands = thresholds['deps_bands']
        if s['loc'] > loc_bands[-1]:
            lines.append(f"[!] {name}: LOC {s['loc']} is high (> {loc_bands[-1]}). Consider splitting.")
        elif s['loc'] > loc_bands[0]:
            lines.append(f"[!] {name}: LOC {s['loc']} is medium ({loc_bands[0]+1}–{loc_bands[-1]}).")
        if s['deps'] > deps_bands[-1]:
            lines.append(f"[!] {name}: DEPS {s['deps']} is high (> {deps_bands[-1]}). Reduce dependencies.")
        elif s['deps'] > deps_bands[0]:
            lines.append(f"[!] {name}: DEPS {s['deps']} is medium ({deps_bands[0]+1}–{deps_bands[-1]}).")

    if cc.get("violation_alerts", True):
        for c in violations.get('cycles', [])[:2]:
            files = c['files']
            lines.append(f"[VIOLATION] Circular import: {files[0]}")
            for f in files[1:]:
                lines.append(f"                          → {f}")
        for p in violations.get('private', [])[:2]:
            lines.append(f"[VIOLATION] Private access: {p['importer']} imports {p['member']}")

    if coupling_data and changed_stats and cc.get("coupling_alerts", True):
        session_count = coupling_data.get('session_count', 0)
        min_sessions  = thresholds.get('coupling_min_sessions', 20)
        min_count     = thresholds.get('coupling_min_count', 5)
        path = changed_stats['path']

        if session_count >= min_sessions:
            pairs = find_strong_pairs(coupling_data, path)
            for pair in pairs[:3]:
                count  = pair['co_change_count']
                if count < min_count:
                    continue
                other      = pair['file_b'] if pair['file_a'] == path else pair['file_a']
                status     = pair['coupling_status']
                name_self  = os.path.basename(path)
                name_other = os.path.basename(other)
                lines.append(
                    f"[coupling] {name_self} ↔ {name_other}: co-changed {count}×"
                    f" across {session_count} sessions ({status})."
                    f" Ask the user whether these should be merged or given a shared interface."
                )

    return '\n'.join(lines)


def build_threshold_alerts(state, changed_file, thresholds, notify_loc=None):
    """Return hard-threshold alerts for the changed file."""
    if not changed_file:
        return []

    changed_stats = None
    for f in state['files']:
        if f['abs_path'] == changed_file or f['path'].endswith(changed_file):
            changed_stats = f
            break
    if not changed_stats:
        return []

    alerts = []
    loc_max = thresholds['loc_bands'][-1]
    deps_max = thresholds['deps_bands'][-1]
    name = changed_stats['path']

    if notify_loc and changed_stats['loc'] > notify_loc:
        alerts.append(
            f"[!] {name}: LOC {changed_stats['loc']} exceeds notify threshold ({notify_loc}). "
            f"Consider splitting before it hits the hard limit."
        )

    if changed_stats.get('status') == 'RED':
        alerts.append(
            f"[ALERT] {name} is in the RED zone: LOC {changed_stats['loc']}, DEPS {changed_stats['deps']}"
        )

    if changed_stats['loc'] > loc_max:
        alerts.append(
            f"[ALERT] {name} exceeds LOC limit: {changed_stats['loc']} > {loc_max}"
        )
    if changed_stats['deps'] > deps_max:
        alerts.append(
            f"[ALERT] {name} exceeds DEPS limit: {changed_stats['deps']} > {deps_max}"
        )
    return alerts


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Modulario code structure analyzer')
    parser.add_argument('--target',         required=True, help='Target directory to analyze')
    parser.add_argument('--output',         required=True, help='Path to write state.json')
    parser.add_argument('--thresholds',     help='Path to thresholds.json')
    parser.add_argument('--changed-file',   default='', help='Primary file changed by this tool call')
    parser.add_argument('--changed-files-json', default='',
                        help='JSON list of every file changed by this tool call')
    parser.add_argument('--session-id',     default='', help='Current agent session id')
    parser.add_argument('--provider',       default='unknown', help='Agent provider (claude/codex)')
    parser.add_argument('--print-summary',  action='store_true', help='Print Claude-facing summary')
    parser.add_argument('--check-violations', action='store_true',
                        help='Exit code 2 if changed file is in a violation')
    args = parser.parse_args()

    thresholds = load_thresholds(args.thresholds)

    old_files = []
    if os.path.exists(args.output):
        try:
            with open(args.output) as f:
                old_files = json.load(f).get('files', [])
        except Exception:
            pass

    files, import_graph, private_violations_raw = walk_target(args.target, thresholds)
    files   = merge_deltas(files, old_files)
    summary = compute_summary(files)

    violations = find_violations(
        import_graph, private_violations_raw, args.target,
        ignore_list=thresholds.get('violation_ignore', [])
    )

    state = {
        'target_dir':   str(Path(args.target).resolve()),
        'last_updated': datetime.now().isoformat(),
        'thresholds':   thresholds,
        'files':        files,
        'import_graph': relativize_import_graph(import_graph, args.target),
        'summary':      summary,
        'violations':   violations,
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, 'w') as f:
        json.dump(state, f, indent=2)

    history_path = _history_path_for(args.output)
    append_history(history_path, state['last_updated'], old_files, files)

    history = []
    try:
        with open(history_path, 'r', encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        history.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass

    coup_path     = coupling_path(args.output)
    coupling_data = update_coupling(coup_path, history, thresholds)

    changed_file = args.changed_file or ''
    try:
        changed_files = json.loads(args.changed_files_json) if args.changed_files_json else []
    except (TypeError, ValueError):
        changed_files = []
    if not isinstance(changed_files, list):
        changed_files = []
    changed_files = [str(path) for path in changed_files if path]
    if changed_file and changed_file not in changed_files:
        changed_files.append(changed_file)
    if args.check_violations and file_in_violations(changed_file, violations):
        print(format_violation_block(violations, changed_file, args.target), file=sys.stderr)
        sys.exit(2)

    touched = update_session_scope(
        args.output, args.target, changed_files, args.session_id, args.provider
    )
    touched_folders = set(touched.get('folders') or [])
    touched_files = set(touched.get('files') or [])

    claude_cfg = stopgate_config.load().get("claude", {})

    # Scope: edited files + their fan-in + fan-out (folder set used to gate
    # nags, watch.py runs, and red_hotspots so unrelated parts of the repo
    # do not generate noise on every edit).
    scope_filter_on = claude_cfg.get("scope_filter", True)
    scope_files, scope_folders = compute_graph_scope(touched_files, state.get('import_graph', {}))
    if not scope_filter_on:
        scope_folders = None
        scope_files = None

    # ── Auto-doc: ensure every folder has a README.md, nag if unfilled ──────
    TEMPLATE_MARKER = DOC_MARKER
    doc_reminders = []

    # Walk all directories in the target (not just those with analyzed files)
    DOC_SKIP_DIRS = list(thresholds.get('skip_dirs', []) or [])
    doc_folders = set()
    for root, dirs, _files in os.walk(args.target):
        dirs[:] = filter_walk_dirs(root, dirs, args.target, DOC_SKIP_DIRS)
        rel = os.path.relpath(root, args.target)
        if rel != '.':
            doc_folders.add(rel)

    # For each folder: create template if missing, nag if unfilled
    unfilled = []
    for folder in sorted(doc_folders):
        folder_abs = os.path.join(args.target, folder)
        if os.path.exists(os.path.join(folder_abs, '.doc.dismissed')):
            continue
        readme = os.path.join(folder_abs, 'README.md')
        if not os.path.exists(readme):
            if not claude_cfg.get("auto_create_readme", True):
                continue
            # Auto-create template
            folder_name = folder.split('/')[-1]
            template = readme_template(folder_name, datetime.now().strftime('%Y-%m-%d'))
            try:
                with open(readme, 'w', encoding='utf-8') as fh:
                    fh.write(template)
            except OSError:
                pass
            unfilled.append(folder)
        else:
            # Check if it's still the unfilled template
            try:
                with open(readme, 'r', encoding='utf-8') as fh:
                    first_line = fh.readline()
                if TEMPLATE_MARKER in first_line:
                    unfilled.append(folder)
            except OSError:
                pass

    # Gate nags: scope (touched ∪ fan-in ∪ fan-out folders) when scope_filter
    # is on, else fall back to touched_folders for the legacy behavior.
    nag_gate = scope_folders if scope_folders is not None else touched_folders
    unfilled = [f for f in unfilled if f in nag_gate]

    if not claude_cfg.get("doc_nag", True):
        unfilled = []
    # Nag about unfilled docs — pick the one closest to the changed file
    if unfilled and changed_file:
        changed_rel = changed_file
        if os.path.isabs(changed_file):
            try:
                changed_rel = os.path.relpath(changed_file, args.target)
            except ValueError:
                changed_rel = changed_file
        # Find unfilled folders relevant to the changed file (most specific first)
        relevant = sorted(
            [f for f in unfilled if changed_rel.startswith(f + '/')],
            key=len, reverse=True,
        )
        if relevant:
            folder = relevant[0]
            doc_reminders.append(
                f"[DOC] {folder}/README.md is unfilled — fill in the template now. "
                f"This reminder repeats on every edit until the doc is completed. "
                f"Remove the marker comment on line 1 when done."
            )
        # Also report total unfilled count if there are others
        remaining = len(unfilled) - len(relevant)
        if remaining > 0:
            doc_reminders.append(
                f"[DOC] {remaining} other folder(s) also have unfilled README.md templates."
            )

    # ── Auto-watch: ensure every folder has a watch.py, nag if unfilled ─────
    watch_reminders = []
    unfilled_watches = []
    for folder in sorted(doc_folders):
        folder_abs = os.path.join(args.target, folder)
        if os.path.exists(os.path.join(folder_abs, '.watch.dismissed')):
            continue
        watch_file = os.path.join(folder_abs, 'watch.py')
        if not os.path.exists(watch_file):
            if not claude_cfg.get("auto_create_watch", True):
                continue
            folder_name = folder.split('/')[-1]
            depth = len(folder.split('/'))
            parents = '/'.join(['..'] * depth)
            template = watch_template(folder_name, parents)
            try:
                with open(watch_file, 'w', encoding='utf-8') as fh:
                    fh.write(template)
            except OSError:
                pass
            unfilled_watches.append(folder)
        else:
            try:
                with open(watch_file, 'r', encoding='utf-8') as fh:
                    first_line = fh.readline()
                if WATCH_MARKER in first_line:
                    unfilled_watches.append(folder)
            except OSError:
                pass

    unfilled_watches = [f for f in unfilled_watches if f in nag_gate]
    if not claude_cfg.get("watch_nag", True):
        unfilled_watches = []

    if unfilled_watches and changed_file:
        changed_rel = changed_file
        if os.path.isabs(changed_file):
            try:
                changed_rel = os.path.relpath(changed_file, args.target)
            except ValueError:
                changed_rel = changed_file
        relevant = sorted(
            [f for f in unfilled_watches if changed_rel.startswith(f + '/')],
            key=len, reverse=True,
        )
        if relevant:
            folder = relevant[0]
            watch_reminders.append(
                f"[WATCH] {folder}/watch.py is unfilled — add health checks for this folder. "
                f"This reminder repeats on every edit until the watch script is completed. "
                f"Remove the marker comment on line 1 when done."
            )
        remaining = len(unfilled_watches) - len(relevant)
        if remaining > 0:
            watch_reminders.append(
                f"[WATCH] {remaining} other folder(s) also have unfilled watch.py templates."
            )

    if args.print_summary and claude_cfg.get("summary_block", True):
        alerts = []
        if claude_cfg.get("threshold_alerts", True):
            alerts += build_threshold_alerts(
                state, changed_file, thresholds,
                notify_loc=stopgate_config.load().get("notify_loc"),
            )
        if claude_cfg.get("import_alerts", True):
            alerts += build_import_alerts(state, changed_file, args.target)
        alerts += doc_reminders
        alerts += watch_reminders

        # Look up the changed file's status for the quiet-when-clean check.
        changed_stats = None
        if changed_file:
            for f in state['files']:
                if f['abs_path'] == changed_file or f['path'].endswith(changed_file):
                    changed_stats = f
                    break

        quiet = claude_cfg.get("quiet_when_clean", True)
        # Clean = nothing actionable to surface. Either the file is in the
        # target and at GREEN/LIME, OR the file is outside the target (so
        # there is nothing project-relevant to say about it).
        is_clean = not alerts and (
            changed_stats is None
            or changed_stats.get('status') in ('GREEN', 'LIME')
        )

        if quiet and is_clean:
            text = ''
        else:
            text = build_claude_summary(
                state, changed_file, thresholds, history, coupling_data,
                modulario_dir=Path(__file__).resolve().parent.parent,
                claude_cfg=claude_cfg,
                scope_files=scope_files,
            )
            if alerts:
                text = text + "\n" + "\n".join(alerts)

        out = {
            "_scope_folders": sorted(scope_folders) if scope_folders is not None else None,
            "hookSpecificOutput": {
                "hookEventName":    "PostToolUse",
                "additionalContext": text,
            }
        }
        # Watch runner consumes/removes `_scope_folders` before returning hook JSON.
        print(json.dumps(out))
    else:
        print(
            f"[Modulario] {summary['total']} files — "
            f"RED:{summary['red']} YELLOW:{summary['yellow']} GREEN:{summary['green']}",
            file=sys.stderr
        )


if __name__ == '__main__':
    main()
