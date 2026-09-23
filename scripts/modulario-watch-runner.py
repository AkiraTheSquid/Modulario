"""
Modulario watch runner — folder-based watch.py system.

Scans the target directory for filled, enabled watch.py files and runs them.
Reads analyzer JSON from stdin and appends watch results to additionalContext,
or prints plain text with --print-plain.

Quiet-by-default: only FAIL results are emitted to Claude. PASS lines are
suppressed because constant "all green" status is noise. Optional scope
filter (passed via top-level `_scope_folders` key in stdin JSON) limits
which folders' watch scripts are executed.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from cli_common import filter_walk_dirs, load_skip_dirs

WATCH_MARKER = '# modulario:template'


def _normalize_folder(rel):
    rel = rel.replace('\\', '/').strip('/')
    return '' if rel in ('', '.') else rel


def scan_and_run(target_dir, timeout=10, scope_folders=None):
    """Scan for watch.py files and run all eligible ones.

    If `scope_folders` is a set, only run watches whose folder (relative to
    target, normalized; '' for root) is in that set. None = run all.
    """
    target = os.path.realpath(target_dir)
    results = []
    skip_dirs = load_skip_dirs()
    for root, dirs, files in os.walk(target):
        dirs[:] = filter_walk_dirs(root, dirs, target, skip_dirs)
        if 'watch.py' not in files:
            continue
        watch_path = os.path.join(root, 'watch.py')
        if os.path.exists(os.path.join(root, '.watch.dismissed')):
            continue
        if os.path.exists(os.path.join(root, '.watch.disabled')):
            continue
        try:
            with open(watch_path, 'r') as f:
                first_line = f.readline()
            if WATCH_MARKER in first_line:
                continue
        except OSError:
            continue

        rel = _normalize_folder(os.path.relpath(root, target))
        if scope_folders is not None and rel not in scope_folders:
            continue

        folder = '/' if rel == '' else rel + '/'
        try:
            r = subprocess.run(
                [sys.executable, watch_path],
                cwd=target,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if r.returncode == 0:
                results.append(('PASS', folder, ''))
            else:
                reason = _failure_reason(r.stderr, r.stdout)
                results.append(('FAIL', folder, reason))
        except subprocess.TimeoutExpired:
            results.append(('FAIL', folder, f'timed out after {timeout}s'))
        except Exception as e:
            results.append(('FAIL', folder, str(e)[:120]))
    return results


def _failure_reason(stderr, stdout):
    """Return the useful assertion line, not a traceback header."""
    lines = [
        line.strip()
        for stream in (stderr, stdout)
        for line in (stream or '').splitlines()
        if line.strip()
    ]
    explicit = next((line[5:] for line in lines if line.startswith('FAIL ')), None)
    return (explicit or (lines[-1] if lines else 'non-zero exit'))[:200]


def format_results(results):
    """Emit only FAIL lines. PASS is silent — repeating 'all green' is noise."""
    lines = []
    for status, folder, reason in results:
        if status == 'FAIL':
            lines.append(f'[WATCH] {folder}: FAIL — {reason}')
    return '\n'.join(lines)


def format_summary(results):
    """Human-facing run summary; never injected into hook context."""
    passed = sum(status == 'PASS' for status, _folder, _reason in results)
    failed = sum(status == 'FAIL' for status, _folder, _reason in results)
    noun = 'watch' if len(results) == 1 else 'watches'
    return f'{len(results)} {noun} ran: {passed} PASS, {failed} FAIL'


def _extract_scope(data):
    """Pop scope_folders out of the piped JSON (private channel from analyzer)."""
    if not isinstance(data, dict):
        return None
    sf = data.pop('_scope_folders', None)
    if isinstance(sf, list):
        return {_normalize_folder(s) for s in sf if isinstance(s, str)}
    return None


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', required=True)
    parser.add_argument('--changed-file', default='')
    parser.add_argument('--print-plain', action='store_true')
    parser.add_argument('--show-summary', action='store_true',
                        help='print PASS/FAIL totals for an interactive CLI run')
    parser.add_argument('--scope-folders-json', default='',
                        help='JSON list of watch folders allowed to run')
    args = parser.parse_args()

    raw = ''
    data = None
    scope_folders = None
    if args.scope_folders_json:
        try:
            parsed_scope = json.loads(args.scope_folders_json)
            if isinstance(parsed_scope, list):
                scope_folders = {_normalize_folder(s) for s in parsed_scope if isinstance(s, str)}
        except ValueError:
            scope_folders = set()
    elif not args.print_plain:
        raw = sys.stdin.read()
        try:
            data = json.loads(raw.strip()) if raw.strip() else None
        except Exception:
            data = None
        scope_folders = _extract_scope(data)

    results = scan_and_run(os.path.realpath(args.target), scope_folders=scope_folders)
    text = format_results(results)

    if args.print_plain:
        if args.show_summary:
            print(format_summary(results))
        if text:
            print(text)
            sys.exit(1)
        return

    # No FAILs to report — pass through analyzer output (with scope key already stripped).
    if not text:
        if isinstance(data, dict):
            print(json.dumps(data))
        else:
            sys.stdout.write(raw)
        return

    if isinstance(data, dict):
        ctx = data.get('hookSpecificOutput', {}).get('additionalContext', '') or ''
        merged = (ctx.rstrip() + '\n' + text) if ctx.strip() else text
        data.setdefault('hookSpecificOutput', {})['additionalContext'] = merged
        print(json.dumps(data))
    else:
        if raw:
            sys.stdout.write(raw if raw.endswith('\n') else raw + '\n')
        print(text)


if __name__ == '__main__':
    main()
