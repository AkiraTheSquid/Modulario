"""
Modulario watch runner — folder-based watch.py system.

Scans the target directory for filled, enabled watch.py files and runs them.
Reads analyzer JSON from stdin and appends watch results to additionalContext,
or prints plain text with --print-plain.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

WATCH_MARKER = '# modulario:template'
SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.next', 'dist', 'build', 'venv', '.venv'}


def scan_and_run(target_dir, timeout=10):
    """Scan for watch.py files and run all eligible ones."""
    target = os.path.realpath(target_dir)
    results = []
    for root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        if 'watch.py' not in files:
            continue
        watch_path = os.path.join(root, 'watch.py')
        # Skip dismissed, disabled, or unfilled
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

        rel = os.path.relpath(root, target)
        folder = '/' if rel == '.' else rel + '/'
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
                reason = (r.stderr or r.stdout or 'non-zero exit').strip()
                reason = reason.splitlines()[0][:120]
                results.append(('FAIL', folder, reason))
        except subprocess.TimeoutExpired:
            results.append(('FAIL', folder, f'timed out after {timeout}s'))
        except Exception as e:
            results.append(('FAIL', folder, str(e)[:120]))
    return results


def format_results(results):
    lines = []
    for status, folder, reason in results:
        if status == 'PASS':
            lines.append(f'[WATCH] {folder}: PASS')
        else:
            lines.append(f'[WATCH] {folder}: FAIL — {reason}')
    return '\n'.join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', required=True)
    parser.add_argument('--changed-file', default='')
    parser.add_argument('--print-plain', action='store_true')
    args = parser.parse_args()

    results = scan_and_run(os.path.realpath(args.target))

    if not results:
        if args.print_plain:
            return
        sys.stdout.write(sys.stdin.read())
        return

    text = format_results(results)

    if args.print_plain:
        print(text)
        return

    raw = sys.stdin.read().strip()
    try:
        data = json.loads(raw)
        ctx = data.get('hookSpecificOutput', {}).get('additionalContext', '')
        data.setdefault('hookSpecificOutput', {})['additionalContext'] = (
            ctx.rstrip() + '\n' + text
        )
        print(json.dumps(data))
    except Exception:
        print(raw)
        print(text)


if __name__ == '__main__':
    main()
