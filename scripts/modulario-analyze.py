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

from counters import (count_loc_python, count_loc_js, count_loc_css,
                      count_deps_python, count_deps_js, count_deps_css, assign_status)
from violations import (collect_graph_data, find_violations,
                        file_in_violations, format_violation_block)
from import_alerts import build_import_alerts
from churn_tracker import churn_paths, append_history, update_churn
from coupling_tracker import coupling_path, update_coupling, find_strong_pairs, interpret_coupling

SKIP_DIRS = {
    'node_modules', '.git', 'venv', '.venv', '__pycache__',
    '.next', 'dist', 'build', '.mypy_cache', '.pytest_cache',
    'coverage', '.tox', '.eggs', 'htmlcov', '.cache',
    'vendor',
}

SUPPORTED_EXTENSIONS = {'.py', '.js', '.ts', '.jsx', '.tsx', '.mjs', '.cjs', '.css'}


def _touched_folders_path(output_path):
    base, _ = os.path.splitext(output_path)
    return base + '_touched.json'


def update_touched_folders(output_path, target, changed_file):
    """Persistent per-target set of folders that have been edited at least once.

    Read existing set, merge in every ancestor folder of `changed_file` (relative
    to target, excluding target root itself), persist, return the merged set.
    Used to gate DOC/WATCH nag emission so untouched folders stay silent.
    """
    path = _touched_folders_path(output_path)
    touched = set()
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                touched = set(json.load(fh).get('folders', []))
        except Exception:
            touched = set()

    if changed_file:
        try:
            rel = os.path.relpath(changed_file, target)
        except ValueError:
            rel = changed_file
        rel = rel.replace('\\', '/')
        if not rel.startswith('..'):
            parts = rel.split('/')[:-1]
            acc = []
            for part in parts:
                if part in ('', '.', '..'):
                    continue
                acc.append(part)
                touched.add('/'.join(acc))
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'w', encoding='utf-8') as fh:
                    json.dump({'folders': sorted(touched)}, fh)
            except OSError:
                pass

    return touched


# ─── Threshold loading ────────────────────────────────────────────────────────

def load_thresholds(path):
    defaults = {
        "loc_bands":  [150, 300, 450, 600, 750],
        "deps_bands": [4, 8, 12, 16, 20],
        "violation_ignore": [],
        "churn_window": 20,
        "churn_thresholds": {"high": 5, "critical": 8},
        "churn_scoring": {
            "day_weight": 1.0,
            "span_day_weight": 0.2,
            "same_day_repeat_weight": 0.25,
        },
        "churn_file_rules": {},
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
                    "loc_bands", "deps_bands", "violation_ignore",
                    "churn_window", "churn_thresholds", "churn_scoring", "churn_file_rules",
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

def find_local_packages(target_dir):
    packages   = set()
    target_path = Path(target_dir).resolve()
    for root, dirs, files in os.walk(target_path):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in SKIP_DIRS]
        if any(f.endswith('.py') for f in files):
            packages.add(Path(root).name)
        for f in files:
            if f.endswith('.py'):
                packages.add(Path(f).stem)
    return packages


def walk_target(target_dir, thresholds):
    results     = []
    target_path = Path(target_dir).resolve()
    local_packages = find_local_packages(target_dir)

    files_set = set()
    for root, dirs, files in os.walk(target_path):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith('.'))
        for filename in files:
            fp = Path(root) / filename
            if fp.suffix.lower() in SUPPORTED_EXTENSIONS:
                files_set.add(str(fp.resolve()))

    import_graph = {}
    all_private  = []

    for root, dirs, files in os.walk(target_path):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith('.'))
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



def write_churn_warning_log(modulario_dir, changed_stats, churn_info, window):
    """Create a dated review note for each churn warning."""
    logs_root = Path(modulario_dir) / 'logs' / 'churn-warnings'
    now = datetime.now()
    date_dir = logs_root / now.strftime('%Y-%m-%d')
    date_dir.mkdir(parents=True, exist_ok=True)

    file_slug = changed_stats['path'].replace(os.sep, '__').replace('/', '__')
    file_slug = ''.join(ch for ch in file_slug if ch.isalnum() or ch in ('-', '_', '.'))
    log_path = date_dir / f"{now.strftime('%H%M%S')}_{file_slug}.txt"

    body = "\n".join([
        "Modulario churn warning review",
        f"Generated: {now.isoformat()}",
        f"File: {changed_stats['path']}",
        f"Status: {churn_info.get('churn_status', 'LOW')}",
        f"Weighted churn score: {churn_info.get('churn_score', 0)}",
        f"Sessions touched: {churn_info.get('sessions_touched', 0)}/{window}",
        f"Distinct days touched: {churn_info.get('days_touched', 0)}",
        f"Touch span days: {churn_info.get('touch_span_days', 0)}",
        f"Same-day retouches: {churn_info.get('same_day_retouches', 0)}",
        f"Relative churn: {churn_info.get('relative_churn', 0.0)}",
        "",
        "AI review:",
        "Classification: PENDING",
        "False positive?: PENDING",
        "Hidden legacy/setup artifacts to inspect:",
        "- ",
        "Reasoning:",
        "- ",
        "Suggested cleanup or structural follow-up:",
        "- ",
    ]) + "\n"

    with open(log_path, 'w', encoding='utf-8') as fh:
        fh.write(body)
    return log_path


# ─── Summary for Claude (PostToolUse) ────────────────────────────────────────

def build_claude_summary(state, changed_file, thresholds, churn_data=None, history=None,
                         coupling_data=None, modulario_dir=None):
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

    red_files = sorted(
        (f for f in files if f['status'] == 'RED'),
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

    if changed_stats:
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

    for c in violations.get('cycles', [])[:2]:
        files = c['files']
        lines.append(f"[VIOLATION] Circular import: {files[0]}")
        for f in files[1:]:
            lines.append(f"                          → {f}")
    for p in violations.get('private', [])[:2]:
        lines.append(f"[VIOLATION] Private access: {p['importer']} imports {p['member']}")

    if churn_data:
        window      = churn_data.get('window_size', 20)
        total_files = len(state['files'])

        # ── Session spread: warn if this session touched a large fraction of the project
        s_touched = churn_data.get('session_files_touched', 0)
        if s_touched >= 5 and s_touched / max(total_files, 1) >= 0.20:
            spread_pct = s_touched / total_files
            lines.append(
                f"[~] Session spread: {s_touched}/{total_files} files touched"
                f" ({spread_pct:.0%} of project). Consider narrowing task scope."
            )

        if changed_stats:
            path = changed_stats['path']
            name = os.path.basename(path)
            cd   = churn_data.get('files', {}).get(path, {})
            st   = cd.get('sessions_touched', 0)
            dt   = cd.get('days_touched', 0)
            span = cd.get('touch_span_days', 0)
            score = cd.get('churn_score', 0)
            cs   = cd.get('churn_status', 'LOW')
            if cs in ('HIGH', 'CRITICAL'):
                log_path = None
                if modulario_dir:
                    try:
                        log_path = write_churn_warning_log(modulario_dir, changed_stats, cd, window)
                    except OSError:
                        log_path = None
                lines.append(
                    f"[churn] {name} shows sustained churn: score {score} from {dt} active day(s),"
                    f" {span} day span, {st}/{window} touched sessions ({cs})."
                    f" Ask the user whether this file has hidden legacy setup, stale artifacts,"
                    f" or structural leftovers from direction changes before deciding to split it."
                )
                if log_path:
                    lines.append(
                        f"[churn-log] Update {log_path} and record whether this churn warning"
                        f" looks like a TRUE POSITIVE or FALSE POSITIVE."
                    )

    if coupling_data and changed_stats:
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


def build_threshold_alerts(state, changed_file, thresholds):
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
    parser.add_argument('--changed-file',   default='', help='Path of file just changed')
    parser.add_argument('--print-summary',  action='store_true', help='Print Claude-facing summary')
    parser.add_argument('--check-violations', action='store_true',
                        help='Exit code 2 if changed file is in a violation')
    parser.add_argument('--churn-window',   type=int, default=None,
                        help='Rolling window size for churn (overrides thresholds.json)')
    args = parser.parse_args()

    thresholds = load_thresholds(args.thresholds)
    if args.churn_window is not None:
        thresholds['churn_window'] = args.churn_window

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

    history_path, churn_path = churn_paths(args.output)
    append_history(history_path, state['last_updated'], old_files, files)
    churn_data = update_churn(churn_path, history_path, files, thresholds)

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
    coupling_data = update_coupling(coup_path, history, churn_data, thresholds)

    changed_file = args.changed_file or ''
    if args.check_violations and file_in_violations(changed_file, violations):
        print(format_violation_block(violations, changed_file, args.target), file=sys.stderr)
        sys.exit(2)

    touched_folders = update_touched_folders(args.output, args.target, changed_file)

    # ── Auto-doc: ensure every folder has a README.md, nag if unfilled ──────
    TEMPLATE_MARKER = '<!-- modulario:template -->'
    doc_reminders = []

    # Walk all directories in the target (not just those with analyzed files)
    SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.next', 'dist', 'build', 'venv', '.venv'}
    doc_folders = set()
    for root, dirs, _files in os.walk(args.target):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
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
            # Auto-create template
            folder_name = folder.split('/')[-1]
            template = f"""{TEMPLATE_MARKER}
# {folder_name}

## Purpose
- One or two sentences on what this folder is responsible for.
- Describe the business/domain concern, not the technical details.

## Owns
- List the main responsibilities this folder **does own**.
- Each item should be something that changes when this folder changes.

## Does NOT own
- List responsibilities that live elsewhere to prevent scope creep.
- Link to the other folder/module if relevant.

## Key Files
- `example.js`: short description of what this file is and when it runs.

## Data & External Dependencies
- What data models or types this area works with.
- What external services or libraries it directly touches.
- Any important shared modules it depends on.

## How It Works (Flow)
1. Brief step-by-step of the main flow.
2. Optional secondary flows if they are important.

## Invariants & Constraints
- Rules that **must** remain true.
- Performance or security constraints.
- "Never do X" type rules that are easy to forget.

## Extension Points
- How to add a new feature in this area.
- What file to start from when extending behavior.

## Known Issues, Recurring Bugs, and Pain Points (and How to Prevent Them)

- **Short name of issue** — `ACTIVE` or `RESOLVED`
  - When it happens: one line about the situation/context.
  - Symptom: what you see break.
  - Root cause: the underlying mistake or assumption.
  - Prevention/fix: the rule, pattern, or helper to use so it doesn't come back.
  - Status: `ACTIVE` = still a risk, `RESOLVED` = was an issue, now fixed (keep for history).

## Recent Changes
- {datetime.now().strftime('%Y-%m-%d')}: Initial doc created.
"""
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

    # Gate nags to touched folders only (persistent, per target)
    unfilled = [f for f in unfilled if f in touched_folders]

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
    WATCH_MARKER = '# modulario:template'
    watch_reminders = []
    unfilled_watches = []
    for folder in sorted(doc_folders):
        folder_abs = os.path.join(args.target, folder)
        if os.path.exists(os.path.join(folder_abs, '.watch.dismissed')):
            continue
        watch_file = os.path.join(folder_abs, 'watch.py')
        if not os.path.exists(watch_file):
            folder_name = folder.split('/')[-1]
            depth = len(folder.split('/'))
            parents = '/'.join(['..'] * depth)
            template = f'''{WATCH_MARKER}
"""watch.py — health checks for {folder_name}

Auto-generated by Modulario. Fill in the stub functions below with real
checks for this folder. Remove the marker comment on line 1 when done.
Runs via `mod watch` — exit 0 = PASS, exit non-zero = FAIL.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '{parents}'))


# ── Import checks ──────────────────────────────
# Verify key modules in this folder can be imported without error.
def check_imports():
    pass  # e.g. from {folder_name} import main_module


# ── Public API checks ─────────────────────────
# Verify expected functions/classes exist and are callable.
def check_public_api():
    pass  # e.g. assert callable(main_module.some_function)


# ── Invariant checks ──────────────────────────
# Verify structural rules that must remain true for this folder.
def check_invariants():
    pass  # e.g. assert config file exists, assert no forbidden patterns


# ── Run all checks ────────────────────────────
if __name__ == '__main__':
    checks = [check_imports, check_public_api, check_invariants]
    for fn in checks:
        try:
            fn()
        except Exception as e:
            print(f"FAIL {{fn.__name__}}: {{e}}", file=sys.stderr)
            sys.exit(1)
'''
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

    unfilled_watches = [f for f in unfilled_watches if f in touched_folders]

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

    if args.print_summary:
        text = build_claude_summary(
            state, changed_file, thresholds, churn_data, history, coupling_data,
            modulario_dir=Path(__file__).resolve().parent.parent,
        )
        alerts = build_threshold_alerts(state, changed_file, thresholds)
        alerts += build_import_alerts(state, changed_file, args.target)
        alerts += doc_reminders
        alerts += watch_reminders
        if alerts:
            text = text + "\n" + "\n".join(alerts)
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName":    "PostToolUse",
                "additionalContext": text,
            }
        }))
    else:
        print(
            f"[Modulario] {summary['total']} files — "
            f"RED:{summary['red']} YELLOW:{summary['yellow']} GREEN:{summary['green']}",
            file=sys.stderr
        )


if __name__ == '__main__':
    main()
