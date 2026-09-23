"""`mod autostart ...` — configure restart-hook app entries."""
import json
import os
import shlex
import sys
from pathlib import Path

AUTOSTART_DIR = Path.home() / '.config' / 'claude-autostart'
AUTOSTART_APPS = AUTOSTART_DIR / 'apps.json'
AUTOSTART_KEY = 'mod_autostart'
LEGACY_DELTA_NOTE_KEY = 'delta_note'
DELTA_NOTE_SCRIPT = Path.home() / 'Applications' / 'Delta-Note' / 'linux' / 'run_reflection_app.sh'
DELTA_NOTE_RESTART_SIGNAL = Path.home() / 'Applications' / 'Delta-Note' / 'linux' / 'request_restart.sh'
DELTA_NOTE_STOP_CMD = f"bash {shlex.quote(str(DELTA_NOTE_RESTART_SIGNAL))}"


def load_autostart_apps():
    if not AUTOSTART_APPS.exists():
        return {}
    try:
        with open(AUTOSTART_APPS, encoding='utf-8') as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save_autostart_apps(apps):
    AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
    with open(AUTOSTART_APPS, 'w', encoding='utf-8') as f:
        json.dump(apps, f, indent=2)
        f.write('\n')


def _shell_quote(path_str):
    return shlex.quote(str(path_str))


def _foreground_start_cmd_for_path(path_str):
    path_obj = Path(os.path.abspath(os.path.expanduser(str(path_str))))
    quoted = _shell_quote(path_obj)
    if os.access(path_obj, os.X_OK):
        return quoted
    return f"bash {quoted}"


def _slugify(value):
    raw = ''.join(ch.lower() if ch.isalnum() else '_' for ch in str(value or '').strip())
    raw = '_'.join(part for part in raw.split('_') if part)
    return raw or 'app'


def _basename_label(path_str):
    return Path(path_str).name or path_str


def _extract_path_from_start_cmd(start_cmd):
    try:
        parts = shlex.split(start_cmd or '')
    except ValueError:
        return ''
    for part in parts:
        expanded = os.path.abspath(os.path.expanduser(part))
        if os.path.exists(expanded):
            return expanded
    return ''


def _build_script_entry(script_path, *, key=None, enabled=True, stop_cmd='', label=None, kind='script'):
    path_obj = Path(os.path.abspath(os.path.expanduser(str(script_path))))
    start_cmd = _foreground_start_cmd_for_path(path_obj)
    return {
        'key': key or _slugify(path_obj.stem),
        'label': label or _basename_label(str(path_obj)),
        'path': str(path_obj),
        'kind': kind,
        'start_cmd': start_cmd,
        'stop_cmd': stop_cmd,
        'enabled': bool(enabled),
    }


def default_mod_autostart_entry(enabled=False):
    return {
        'key': AUTOSTART_KEY,
        'label': 'Delta Note',
        'path': str(DELTA_NOTE_SCRIPT),
        'kind': 'preset',
        'start_cmd': _foreground_start_cmd_for_path(DELTA_NOTE_SCRIPT),
        'stop_cmd': '',
        'enabled': bool(enabled),
    }


def normalize_entry(key, entry):
    raw = dict(entry or {})
    if key in (AUTOSTART_KEY, LEGACY_DELTA_NOTE_KEY):
        base = default_mod_autostart_entry(enabled=raw.get('enabled', False))
        if 'enabled' in raw:
            base['enabled'] = bool(raw.get('enabled', False))
        base['key'] = AUTOSTART_KEY
        return base

    start_cmd = raw.get('start_cmd', '')
    path_str = raw.get('path') or _extract_path_from_start_cmd(start_cmd)
    kind = raw.get('kind') or 'script'
    if path_str and kind in ('script', 'preset'):
        start_cmd = _foreground_start_cmd_for_path(path_str)
    entry_key = raw.get('key') or key or _slugify(Path(path_str or 'app').stem)
    return {
        'key': entry_key,
        'label': raw.get('label') or _basename_label(path_str or entry_key),
        'path': path_str,
        'kind': kind,
        'start_cmd': start_cmd,
        'stop_cmd': raw.get('stop_cmd', ''),
        'enabled': bool(raw.get('enabled', False)),
    }


def normalized_autostart_apps():
    raw_apps = load_autostart_apps()
    out = {}
    for key, entry in raw_apps.items():
        if not isinstance(entry, dict):
            continue
        if key == LEGACY_DELTA_NOTE_KEY:
            continue
        out[key] = normalize_entry(key, entry)

    legacy_delta = raw_apps.get(LEGACY_DELTA_NOTE_KEY)
    canonical = out.get(AUTOSTART_KEY)
    if canonical is None and (DELTA_NOTE_SCRIPT.exists() or isinstance(legacy_delta, dict)):
        canonical = default_mod_autostart_entry(enabled=False)
    if canonical is not None:
        if isinstance(legacy_delta, dict):
            legacy_entry = normalize_entry(LEGACY_DELTA_NOTE_KEY, legacy_delta)
            canonical['enabled'] = bool(canonical.get('enabled') or legacy_entry.get('enabled'))
            if not canonical.get('stop_cmd'):
                canonical['stop_cmd'] = legacy_entry.get('stop_cmd', '')
            if not canonical.get('start_cmd'):
                canonical['start_cmd'] = legacy_entry.get('start_cmd', '')
        out[AUTOSTART_KEY] = canonical
    return out


def persist_normalized_autostart_apps(apps):
    raw = {}
    for key, entry in apps.items():
        item = dict(entry)
        item.pop('key', None)
        raw[key] = item
    save_autostart_apps(raw)


def resolve_entry_key(apps, token):
    if token == LEGACY_DELTA_NOTE_KEY and AUTOSTART_KEY in apps:
        return AUTOSTART_KEY
    if token in apps:
        return token
    wanted = os.path.abspath(os.path.expanduser(token))
    for key, entry in apps.items():
        if entry.get('path') and os.path.abspath(entry['path']) == wanted:
            return key
    return None


def ensure_unique_key(apps, base_key):
    key = _slugify(base_key)
    if key not in apps:
        return key
    idx = 2
    while f"{key}_{idx}" in apps:
        idx += 1
    return f"{key}_{idx}"


def add_script_autostart(apps, script_path, key=None, *, enabled=True):
    path_str = os.path.abspath(os.path.expanduser(script_path))
    if not os.path.exists(path_str):
        raise FileNotFoundError(path_str)
    existing = resolve_entry_key(apps, path_str)
    final_key = key or Path(path_str).stem
    if existing:
        final_key = existing
    else:
        final_key = ensure_unique_key(apps, final_key)
    entry = _build_script_entry(path_str, key=final_key, enabled=enabled)
    apps[final_key] = entry
    return entry


def set_entry_enabled(apps, key, enabled):
    entry = apps.get(key)
    if not entry:
        return None
    entry['enabled'] = bool(enabled)
    apps[key] = entry
    return entry


def clear_entry(apps, key):
    return apps.pop(key, None)


def print_entry(entry):
    state = 'ON ' if entry.get('enabled') else 'OFF'
    label = entry.get('label') or entry['key']
    path_str = entry.get('path') or '(none)'
    print(f"{entry['key']}: {state}  {label}  path: {path_str}")
    print(f"  start: {entry.get('start_cmd', '(none)')}")
    if entry.get('stop_cmd'):
        print(f"  stop:  {entry.get('stop_cmd')}")


def cmd_autostart(argv):
    usage = ("Usage:\n"
             "  mod autostart add <path> [name]    Add script entry\n"
             "  mod autostart on <entry>           Enable one entry\n"
             "  mod autostart off <entry>          Disable one entry\n"
             "  mod autostart status [entry]       Show entries\n"
             "  mod autostart clear <entry>        Remove one entry\n"
             "  mod autostart <path>               Legacy alias for add <path>\n"
             "\n"
             "Built-in entry keys:\n"
             "  mod_autostart                      Delta Note launcher preset")

    if not argv:
        print(usage)
        sys.exit(1)

    sub = argv[0]
    apps = normalized_autostart_apps()

    if sub == 'status':
        if len(argv) > 1:
            key = resolve_entry_key(apps, argv[1])
            if not key:
                print(f"autostart: unknown entry: {argv[1]}")
                sys.exit(1)
            print_entry(apps[key])
            return
        if not apps:
            print("autostart: no entries configured")
            return
        for key in sorted(apps, key=lambda k: (k != AUTOSTART_KEY, k)):
            print_entry(apps[key])
        return

    if sub in ('on', 'off', 'clear'):
        if len(argv) < 2:
            print(f"autostart: missing entry for `{sub}`")
            print(usage)
            sys.exit(1)
        key = resolve_entry_key(apps, argv[1])
        if not key:
            print(f"autostart: unknown entry: {argv[1]}")
            sys.exit(1)
        if sub == 'clear':
            clear_entry(apps, key)
            persist_normalized_autostart_apps(apps)
            print(f"autostart: cleared {key}")
            return
        entry = set_entry_enabled(apps, key, sub == 'on')
        persist_normalized_autostart_apps(apps)
        state = 'ON' if entry['enabled'] else 'OFF'
        print(f"autostart: {state}  {key}")
        return

    if sub == 'add':
        if len(argv) < 2:
            print("autostart: missing path for `add`")
            print(usage)
            sys.exit(1)
        script_path = argv[1]
        key = argv[2] if len(argv) > 2 else None
        try:
            entry = add_script_autostart(apps, script_path, key, enabled=True)
        except FileNotFoundError:
            script_path = os.path.abspath(os.path.expanduser(script_path))
            print(f"Error: script not found: {script_path}")
            sys.exit(1)
        persist_normalized_autostart_apps(apps)
        print(f"autostart: ON  {entry['key']}  path: {entry['path']}")
        return

    if sub in (AUTOSTART_KEY, LEGACY_DELTA_NOTE_KEY):
        entry = apps.get(AUTOSTART_KEY) or default_mod_autostart_entry(enabled=False)
        apps[AUTOSTART_KEY] = entry
        persist_normalized_autostart_apps(apps)
        print_entry(entry)
        return

    try:
        entry = add_script_autostart(apps, sub, enabled=True)
    except FileNotFoundError:
        script_path = os.path.abspath(os.path.expanduser(sub))
        print(f"Error: script not found: {script_path}")
        sys.exit(1)
    persist_normalized_autostart_apps(apps)
    print(f"autostart: ON  {entry['key']}  path: {entry['path']}")
