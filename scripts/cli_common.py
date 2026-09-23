"""Shared helpers and path constants for the `mod` CLI command modules."""
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

MODULARIO_DIR    = Path(__file__).resolve().parent.parent
ANALYZER         = MODULARIO_DIR / 'scripts' / 'modulario-analyze.py'
THRESHOLDS       = MODULARIO_DIR / 'configs' / 'thresholds.json'
TUI              = MODULARIO_DIR / 'tui' / 'modulario-tui.py'
HOOK_SCRIPT      = MODULARIO_DIR / 'scripts' / 'modulario-hook.sh'
PRE_HOOK_SCRIPT  = MODULARIO_DIR / 'scripts' / 'pre_edit_gate.py'
STATE_DIR        = MODULARIO_DIR / 'data' / 'state'
CURRENT_TARGET   = MODULARIO_DIR / 'configs' / 'current-target.txt'
HOOKS_PAUSED     = MODULARIO_DIR / 'data' / 'hooks-paused'
GLOBAL_SETTINGS  = Path.home() / '.claude' / 'settings.json'
CODEX_HOOKS      = Path.home() / '.codex' / 'hooks.json'
RESTART_HOOK     = Path.home() / '.config' / 'claude-autostart' / 'restart-hook.sh'
TOOL_HOOK_MATCHER = 'Bash|apply_patch|Write|Edit|NotebookEdit'
BASE_SKIP_DIRS   = {
    'node_modules', '.git', 'venv', '.venv', '__pycache__',
    '.next', 'dist', 'build', '.mypy_cache', '.pytest_cache',
    'coverage', '.tox', '.eggs', 'htmlcov', '.cache',
    'vendor',
}


def _command_identity(command):
    try:
        parts = shlex.split(command or '')
    except ValueError:
        return command or ''
    for part in reversed(parts):
        expanded = os.path.realpath(os.path.expanduser(part))
        if os.path.isabs(expanded) and os.path.exists(expanded):
            return expanded
    return command or ''


def _ensure_command_hook(entries, command, matcher, timeout=None):
    """Keep one dedicated matcher entry for command; update old installs in place."""
    found = None
    identity = _command_identity(command)
    for entry in list(entries):
        hooks = entry.get('hooks', [])
        matches = [hook for hook in hooks if _command_identity(hook.get('command')) == identity]
        if not matches:
            continue
        if found is None and len(hooks) == 1:
            found = entry
            entry['matcher'] = matcher
            hook = matches[0]
            hook['command'] = command
            if timeout is not None:
                hook['timeout'] = timeout
            continue
        entry['hooks'] = [
            hook for hook in hooks if _command_identity(hook.get('command')) != identity
        ]
        if not entry['hooks']:
            entries.remove(entry)
    if found is None:
        hook = {'type': 'command', 'command': command}
        if timeout is not None:
            hook['timeout'] = timeout
        entries.append({'matcher': matcher, 'hooks': [hook]})


def _remove_command_hook(entries, command):
    identity = _command_identity(command)
    for entry in list(entries):
        entry['hooks'] = [
            hook for hook in entry.get('hooks', [])
            if _command_identity(hook.get('command')) != identity
        ]
        if not entry['hooks']:
            entries.remove(entry)


def state_path_for(target_dir):
    h = hashlib.md5(os.path.realpath(target_dir).encode()).hexdigest()[:12]
    return STATE_DIR / f'{h}.json'


def normalize_rel_path(path):
    rel = str(path or '').replace('\\', '/').strip('/')
    return '' if rel in ('', '.') else rel


def normalize_skip_entry(entry):
    return normalize_rel_path(entry)


def load_skip_dirs():
    data = load_thresholds_config()
    return [normalize_skip_entry(v) for v in (data.get('skip_dirs') or []) if str(v).strip()]


def should_skip_dir(child_rel_path, dirname, skip_entries):
    rel = normalize_rel_path(child_rel_path)
    skip = {normalize_skip_entry(v) for v in (skip_entries or []) if str(v).strip()}
    return dirname in BASE_SKIP_DIRS or dirname.startswith('.') or dirname in skip or rel in skip


def filter_walk_dirs(root, dirs, target_dir, skip_entries):
    target = os.path.realpath(target_dir)
    root = os.path.realpath(root)
    rel_root = normalize_rel_path(os.path.relpath(root, target))
    kept = []
    for dirname in dirs:
        child_rel = dirname if not rel_root else f"{rel_root}/{dirname}"
        if should_skip_dir(child_rel, dirname, skip_entries):
            continue
        kept.append(dirname)
    return sorted(kept)


def ensure_global_hook():
    """Install/update provider-aware global Claude hooks."""
    hook_cmd = str(HOOK_SCRIPT)
    pre_cmd = f"{sys.executable} {PRE_HOOK_SCRIPT}"
    stop_cmd = f"{sys.executable} {MODULARIO_DIR / 'scripts' / 'stop_gate.py'}"
    GLOBAL_SETTINGS.parent.mkdir(parents=True, exist_ok=True)

    settings = {}
    if GLOBAL_SETTINGS.exists():
        try:
            with open(GLOBAL_SETTINGS) as f:
                settings = json.load(f)
        except Exception:
            pass

    hooks = settings.setdefault('hooks', {})
    pre_hooks = hooks.setdefault('PreToolUse', [])
    post_hooks = hooks.setdefault('PostToolUse', [])
    stop_hooks = hooks.setdefault('Stop', [])
    _ensure_command_hook(pre_hooks, pre_cmd, TOOL_HOOK_MATCHER, timeout=20)
    _ensure_command_hook(post_hooks, hook_cmd, TOOL_HOOK_MATCHER, timeout=300)
    _ensure_command_hook(stop_hooks, stop_cmd, '', timeout=240)
    _remove_command_hook(hooks.setdefault('SubagentStop', []), stop_cmd)

    with open(GLOBAL_SETTINGS, 'w') as f:
        json.dump(settings, f, indent=2)
        f.write('\n')


def ensure_codex_hook():
    """Install/update provider-aware global Codex hooks."""
    hook_cmd = str(HOOK_SCRIPT)
    pre_cmd = f"{sys.executable} {PRE_HOOK_SCRIPT}"
    stop_cmd = f"{sys.executable} {MODULARIO_DIR / 'scripts' / 'stop_gate.py'}"
    restart_cmd = str(RESTART_HOOK)
    CODEX_HOOKS.parent.mkdir(parents=True, exist_ok=True)

    settings = {}
    if CODEX_HOOKS.exists():
        try:
            with open(CODEX_HOOKS) as f:
                settings = json.load(f)
        except Exception:
            pass

    hooks = settings.setdefault('hooks', {})
    pre_hooks = hooks.setdefault('PreToolUse', [])
    post_hooks = hooks.setdefault('PostToolUse', [])
    _ensure_command_hook(pre_hooks, pre_cmd, TOOL_HOOK_MATCHER, timeout=20)
    _ensure_command_hook(post_hooks, hook_cmd, TOOL_HOOK_MATCHER, timeout=300)
    _remove_command_hook(hooks.setdefault('SubagentStop', []), stop_cmd)

    stop_hooks = hooks.setdefault('Stop', [])
    primary_stop_entry = None
    for entry in stop_hooks:
        if entry.get('matcher', '') == '':
            primary_stop_entry = entry
            break
    if primary_stop_entry is None:
        primary_stop_entry = {'matcher': '', 'hooks': []}
        stop_hooks.append(primary_stop_entry)

    primary_stop_hooks = primary_stop_entry.setdefault('hooks', [])
    stop_identity = _command_identity(stop_cmd)
    restart_identity = _command_identity(restart_cmd)
    if not any(_command_identity(h.get('command')) == stop_identity for h in primary_stop_hooks):
        primary_stop_hooks.append({'type': 'command', 'command': stop_cmd, 'timeout': 20})
    if RESTART_HOOK.exists() and not any(
        _command_identity(h.get('command')) == restart_identity for h in primary_stop_hooks
    ):
        primary_stop_hooks.append({'type': 'command', 'command': restart_cmd, 'timeout': 20})

    for hook in primary_stop_hooks:
        if _command_identity(hook.get('command')) == stop_identity:
            hook['command'] = stop_cmd
            hook['timeout'] = 240

    deduped_stop_hooks = []
    for entry in stop_hooks:
        matcher = entry.get('matcher', '')
        hooks_list = []
        for hook in entry.get('hooks', []):
            cmd = hook.get('command')
            if (
                matcher == ''
                and _command_identity(cmd) in {stop_identity, restart_identity}
                and entry is not primary_stop_entry
            ):
                continue
            hooks_list.append(hook)
        if hooks_list:
            deduped_stop_hooks.append({'matcher': matcher, 'hooks': hooks_list})
    hooks['Stop'] = deduped_stop_hooks

    with open(CODEX_HOOKS, 'w') as f:
        json.dump(settings, f, indent=2)
        f.write('\n')


def set_current_target(target):
    CURRENT_TARGET.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_TARGET.write_text(target + '\n')
    try:
        HOOKS_PAUSED.unlink()
    except FileNotFoundError:
        pass


def run_analyzer(target, sp):
    return subprocess.run(
        [sys.executable, str(ANALYZER),
         '--target', target,
         '--output', str(sp),
         '--thresholds', str(THRESHOLDS)],
        capture_output=True, text=True,
    )


def load_thresholds_config():
    if THRESHOLDS.exists():
        try:
            with open(THRESHOLDS) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_thresholds_config(data):
    THRESHOLDS.parent.mkdir(parents=True, exist_ok=True)
    with open(THRESHOLDS, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')


def add_skip_dir(entry):
    rel = normalize_skip_entry(entry)
    if not rel:
        return False
    data = load_thresholds_config()
    items = [normalize_skip_entry(v) for v in (data.get('skip_dirs') or []) if str(v).strip()]
    if rel in items:
        return False
    items.append(rel)
    data['skip_dirs'] = sorted(set(items))
    save_thresholds_config(data)
    return True


def remove_skip_dir(entry):
    rel = normalize_skip_entry(entry)
    if not rel:
        return False
    data = load_thresholds_config()
    items = [normalize_skip_entry(v) for v in (data.get('skip_dirs') or []) if str(v).strip()]
    next_items = [v for v in items if v != rel]
    if len(next_items) == len(items):
        return False
    data['skip_dirs'] = next_items
    save_thresholds_config(data)
    return True


def sync_gitignore_rule(target_dir, rel_path, present):
    gitignore_path = Path(target_dir) / '.gitignore'
    rule = normalize_rel_path(rel_path)
    if not rule:
        return
    rule_line = f"{rule}/"
    if not present and not gitignore_path.exists():
        return
    existing = []
    if gitignore_path.exists():
        try:
            existing = gitignore_path.read_text(encoding='utf-8').splitlines()
        except OSError:
            existing = []
    normalized = [line.rstrip() for line in existing]
    if present:
        if rule_line not in normalized:
            if existing and existing[-1].strip():
                existing.append('')
            existing.append(rule_line)
    else:
        existing = [line for line in existing if line.rstrip() != rule_line]
        if not existing:
            gitignore_path.write_text('', encoding='utf-8')
            return
    text = '\n'.join(existing).rstrip() + '\n'
    gitignore_path.write_text(text, encoding='utf-8')


def active_target_dir():
    if not CURRENT_TARGET.exists():
        print("No active target. Run `mod <directory>` first.")
        sys.exit(1)
    target = CURRENT_TARGET.read_text().strip()
    if not target or not os.path.isdir(target):
        print("Current target is missing or invalid. Run `mod <directory>` again.")
        sys.exit(1)
    return os.path.realpath(target)


def resolve_target_file(file_arg):
    target = active_target_dir()
    file_path = os.path.realpath(os.path.expanduser(file_arg))
    if not os.path.isabs(file_arg):
        file_path = os.path.realpath(os.path.join(target, file_arg))
    if not os.path.isfile(file_path):
        print(f"Not a file: {file_arg}")
        sys.exit(1)
    try:
        rel_path = os.path.relpath(file_path, target)
    except ValueError:
        print(f"File is outside current target: {file_path}")
        sys.exit(1)
    if rel_path.startswith('..' + os.sep) or rel_path == '..':
        print(f"File is outside current target: {file_path}")
        sys.exit(1)
    return target, rel_path


def resolve_target_folder(folder_arg):
    target = active_target_dir()
    raw = folder_arg.strip()
    if raw in ('/', '.'):
        return target, '/'

    candidate = os.path.realpath(os.path.join(target, os.path.expanduser(raw)))
    if os.path.isfile(candidate):
        candidate = os.path.dirname(candidate)
    if not os.path.isdir(candidate):
        print(f"Not a folder: {folder_arg}")
        sys.exit(1)

    try:
        rel_path = os.path.relpath(candidate, target)
    except ValueError:
        print(f"Folder is outside current target: {candidate}")
        sys.exit(1)
    if rel_path.startswith('..' + os.sep) or rel_path == '..':
        print(f"Folder is outside current target: {candidate}")
        sys.exit(1)
    if rel_path == '.':
        return target, '/'
    return target, rel_path.rstrip('/') + '/'


def print_help():
    print("""
  mod — structural health monitor

  Usage:
    mod                     Open usage-ranked project picker + live TUI
    mod <directory>         Analyze and show live TUI
    mod <project-name>      Open configured project by name
    mod query <directory>   CLI report only — no TUI (for Claude agent use)
    mod watch run           Run all enabled watch.py scripts
    mod watch status        Show watch.py status for all folders
    mod watch init <folder> Create a watch.py template in a folder
    mod watch enable <folder>
                            Enable a watch for a folder
    mod watch disable <folder>
                            Disable a watch for a folder
    mod watch dismiss <folder>
                            Dismiss watch nag (folder has no source files)
    mod doc init <folder>   Create a README.md template in a folder
    mod doc list            Show unfilled/filled doc status for all folders
    mod doc dismiss <folder>
                            Dismiss doc nag for a folder
    mod ignore add <path>   Ignore a path in Modulario and mirror to .gitignore
    mod ignore remove <path>
                            Remove an ignore path from Modulario and .gitignore
    mod ignore list         Show configured ignore paths
    mod graph <file> [--target <dir>]
                            Show what a file imports and what imports it (fan-out + fan-in)
                            --target overrides the active mod directory
    mod autostart add <path> [name]
                            Add autostart entry for a script path
    mod autostart on <entry>
                            Enable one autostart entry (e.g. `delta_note`)
    mod autostart off <entry>
                            Disable one autostart entry
    mod autostart status [entry]
                            Show all autostart entries or one specific entry
    mod autostart clear <entry>
                            Remove one autostart entry entirely
    mod agents              Show the subagent allowance and what each session has spent
    mod agents <count>      Set how many subagents a session may spawn (0 = block all)
    mod agents reset        Clear the spent-budget ledger
    mod end-attempt "<reason>"
                            Release the current stop-gate session without more
                            retries. For Claude to use when a watch/doc failure
                            is outside the scope of what the user asked it to do.
                            Reason is required and is logged to logs/escape_attempts.jsonl.
    mod goals on|off        Toggle Delta Note goals display in Claude SessionStart hook
    mod goals status        Show current on/off state
    mod goals list          Print today's goals + subgoals (IDs for check/uncheck)
    mod goals check <id>    Mark goal/subgoal complete (e.g. `1` or `1.2`)
                            ONLY run after the user has explicitly confirmed the fix.
    mod goals uncheck <id>  Reopen a goal/subgoal
    mod goals add <parent_id> "<text>"
                            Append a new open subgoal under goal <parent_id>
                            (e.g. `mod goals add 1 "fix header layout"`).
    mod goals describe [id]
                            Create .md file(s) in Delta-Note-Local/instructions/.
                            No id        -> one file per top-level goal.
                            `1`          -> one file per subgoal of goal 1.
                            `1.3`        -> one file for that specific subgoal.
                            Skips files that already exist.
    mod off                 Pause global hooks until next `mod` launch
    mod help                This help

  Claude + Codex hooks stay global, but route edits only into enabled projects in
  configs/projects/. Stop checks use provider/session touched files plus direct
  import fan-in/fan-out. Unrelated projects/files remain silent.
""")
