"""State file I/O + analyzer invocation for the Modulario TUI.

Pulled out of modulario-tui.py to keep the entry point focused on event-loop
orchestration. These helpers read/write the on-disk state.json, _churn.json,
and _history.jsonl produced by `modulario-analyze.py`, and re-run the analyzer
when the user edits LOC/DEPS thresholds at runtime.
"""
import hashlib
import json
import os
import subprocess
import sys

from core.config import ANALYZER_PATH, CURRENT_TARGET, STATE_DIR, THRESHOLDS_PATH


def state_path_for(target_dir):
    digest = hashlib.md5(os.path.realpath(target_dir).encode()).hexdigest()[:12]
    return STATE_DIR / f'{digest}.json'


def set_current_target(target):
    CURRENT_TARGET.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_TARGET.write_text(target + '\n')


def analyze_target(target_dir, state_path_str):
    target = os.path.realpath(os.path.expanduser(target_dir))
    if not os.path.isdir(target):
        return False, f"Not a directory: {target}"

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    set_current_target(target)
    result = subprocess.run(
        [sys.executable, str(ANALYZER_PATH),
         '--target', target,
         '--output', state_path_str,
         '--thresholds', str(THRESHOLDS_PATH)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "analysis failed"
        return False, detail
    return True, target


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _churn_path(state_path_str):
    stem = state_path_str[:-5] if state_path_str.endswith('.json') else state_path_str
    return stem + '_churn.json'


def _history_path(state_path_str):
    stem = state_path_str[:-5] if state_path_str.endswith('.json') else state_path_str
    return stem + '_history.jsonl'


def load_churn(state_path_str):
    try:
        with open(_churn_path(state_path_str)) as f:
            return json.load(f).get('files', {})
    except Exception:
        return {}


def load_history(state_path_str):
    entries = []
    try:
        with open(_history_path(state_path_str), 'r', encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass
    return entries


def apply_interval(field, interval, target_dir, state_path_str):
    bands = [interval * (i + 1) for i in range(5)]
    try:
        data = {}
        if THRESHOLDS_PATH.exists():
            with open(THRESHOLDS_PATH) as f:
                data = json.load(f)
        data[field] = bands
        with open(THRESHOLDS_PATH, 'w') as f:
            json.dump(data, f, indent=2)
            f.write('\n')
        ok, _ = analyze_target(target_dir, state_path_str)
        return ok
    except Exception:
        return False
