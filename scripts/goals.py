#!/usr/bin/env python3
"""goals.py — Delta Note goals integration for Modulario.

Backs `mod goals ...` subcommands. Reads/writes today's goals and subgoals in
the local Delta Note Postgres DB.

Subgoal state model (matches Delta Note front-end):
  open    — not done
  checked — done
"""
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

MODULARIO_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE   = MODULARIO_DIR / 'configs' / 'goals.json'
CLAUDE_SETTINGS = Path.home() / '.claude' / 'settings.json'
CODEX_HOOKS     = Path.home() / '.codex' / 'hooks.json'
SESSION_HOOK    = MODULARIO_DIR / 'scripts' / 'goals-session-start.sh'
DESCRIBE_DIR    = Path('/home/stellar-thread/Applications/Delta-Note-Local/instructions')

DB_ENV = {
    **os.environ,
    'PGPASSWORD': os.environ.get('DELTA_NOTE_PGPASSWORD', 'delta_note'),
}
DB_ARGS = ['psql', '-h', 'localhost', '-U',
           os.environ.get('DELTA_NOTE_PGUSER', 'delta_note'),
           '-d', os.environ.get('DELTA_NOTE_PGDB', 'delta_note'),
           '-t', '-A', '-F', '\t', '-q']
USER_ID = os.environ.get('DELTA_NOTE_USER_ID', 'local')


# ─────────────────────────── Config ────────────────────────────
def load_config():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {'enabled': False}


def save_config(cfg):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2) + '\n')


# ─────────────────────────── DB I/O ────────────────────────────
def _psql(sql, params=None):
    """Run SQL, return rows as list of lists (tab-split)."""
    cmd = DB_ARGS + ['-c', sql]
    if params:
        # Caller must inline params safely — we only use psql variable binding
        # via $1 style isn't supported in psql -c. For safety, callers use
        # server-side escaping via format() below.
        raise NotImplementedError
    r = subprocess.run(cmd, env=DB_ENV, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr.strip()}")
    return [line.split('\t') for line in r.stdout.strip().split('\n') if line.strip()]


def _pg_escape(s):
    """Escape a string for inline use in a psql statement."""
    return "'" + str(s).replace("'", "''") + "'"


def fetch_today():
    """Return (goals_rows, meta_json). goals_rows = [(id, content, status)]."""
    today = date.today().isoformat()
    rows = _psql(
        f"SELECT id, section, content, COALESCE(status, 0) "
        f"FROM daily_sections "
        f"WHERE user_id = {_pg_escape(USER_ID)} AND date = {_pg_escape(today)} "
        f"AND section IN ('goals', 'goals_meta') ORDER BY section;"
    )
    goals_row = None
    meta_row = None
    for r in rows:
        if len(r) < 4:
            continue
        row_id, section, content, status = r[0], r[1], r[2], r[3]
        if section == 'goals':
            goals_row = (int(row_id), content or '', int(status or 0))
        elif section == 'goals_meta':
            meta_row = (int(row_id), content or '[]', int(status or 0))
    return goals_row, meta_row


def update_meta(meta_id, new_json):
    """Replace goals_meta.content with new_json (already JSON-encoded string)."""
    _psql(
        f"UPDATE daily_sections SET content = {_pg_escape(new_json)}, "
        f"updated_at = now() WHERE id = {meta_id};"
    )


def update_goals_content(goals_id, new_content):
    _psql(
        f"UPDATE daily_sections SET content = {_pg_escape(new_content)}, "
        f"updated_at = now() WHERE id = {goals_id};"
    )


# ─────────────────────────── Parsing ────────────────────────────
def parse_goals_content(content):
    """Parse multi-line goals content into list of dicts.

    Each line is either '[ ] emoji text' or '[x] emoji text'. Returns:
      [{'checked': bool, 'raw': str, 'text': str}, ...]
    """
    out = []
    for line in (content or '').split('\n'):
        stripped = line.strip()
        if not stripped:
            continue
        checked = stripped.startswith('[x]') or stripped.startswith('[X]')
        open_   = stripped.startswith('[ ]')
        if not (checked or open_):
            out.append({'checked': False, 'raw': line, 'text': line.strip(), 'unparsed': True})
            continue
        text = stripped[3:].strip()
        out.append({'checked': checked, 'raw': line, 'text': text, 'unparsed': False})
    return out


def serialize_goals(goals):
    """Reverse of parse_goals_content."""
    lines = []
    for g in goals:
        if g.get('unparsed'):
            lines.append(g['raw'])
        else:
            box = '[x]' if g['checked'] else '[ ]'
            lines.append(f"{box} {g['text']}")
    return '\n'.join(lines)


# ─────────────────────────── Rendering ────────────────────────────
def build_view():
    """Return (items, text) where items is a flat list for lookup and text is
    a formatted multi-line string.

    items entry: {'id': '1', 'checked': bool, 'text': str, 'kind': 'goal'|'sub',
                  'goal_idx': int, 'sub_idx': int|None}
    """
    goals_row, meta_row = fetch_today()
    items = []
    lines = []

    if not goals_row and not meta_row:
        return items, "(no goals for today)"

    goals = parse_goals_content(goals_row[1] if goals_row else '')
    try:
        meta = json.loads(meta_row[1]) if meta_row else []
    except Exception:
        meta = []

    for i, g in enumerate(goals, start=1):
        box = '[x]' if g['checked'] else '[ ]'
        items.append({'id': str(i), 'checked': g['checked'], 'text': g['text'],
                      'kind': 'goal', 'goal_idx': i - 1, 'sub_idx': None})
        lines.append(f"{box} {i}. {g['text']}")

        meta_entry = meta[i - 1] if i - 1 < len(meta) else None
        subs = (meta_entry or {}).get('subGoals') or []
        for j, sub in enumerate(subs, start=1):
            state = sub.get('state', 'open')
            checked = state == 'checked'
            sbox = '[x]' if checked else '[ ]'
            sid = f"{i}.{j}"
            items.append({'id': sid, 'checked': checked,
                          'text': sub.get('text', ''), 'kind': 'sub',
                          'goal_idx': i - 1, 'sub_idx': j - 1})
            lines.append(f"    {sbox} {sid} {sub.get('text', '')}")

    return items, '\n'.join(lines) if lines else "(no goals for today)"


# ─────────────────────────── Mutations ────────────────────────────
def set_checked(item_id, checked):
    goals_row, meta_row = fetch_today()
    if not goals_row and not meta_row:
        print("No goals found for today.", file=sys.stderr)
        sys.exit(1)

    # Goal-level id: "1", "2", ...
    # Sub-level id:  "1.2"
    parts = item_id.split('.')
    try:
        gidx = int(parts[0]) - 1
    except ValueError:
        print(f"Bad id: {item_id}", file=sys.stderr)
        sys.exit(1)

    if len(parts) == 1:
        # Top-level goal
        if not goals_row:
            print("No goals row.", file=sys.stderr)
            sys.exit(1)
        goals = parse_goals_content(goals_row[1])
        if gidx < 0 or gidx >= len(goals):
            print(f"Goal {item_id} out of range (have {len(goals)}).", file=sys.stderr)
            sys.exit(1)
        goals[gidx]['checked'] = checked
        update_goals_content(goals_row[0], serialize_goals(goals))
        print(f"{'checked' if checked else 'unchecked'}: {item_id} {goals[gidx]['text']}")
        return

    # Sub-goal
    try:
        sidx = int(parts[1]) - 1
    except ValueError:
        print(f"Bad id: {item_id}", file=sys.stderr)
        sys.exit(1)

    if not meta_row:
        print("No goals_meta row — cannot edit subgoals.", file=sys.stderr)
        sys.exit(1)
    try:
        meta = json.loads(meta_row[1])
    except Exception as e:
        print(f"goals_meta JSON parse failed: {e}", file=sys.stderr)
        sys.exit(1)

    if gidx < 0 or gidx >= len(meta):
        print(f"Goal index {gidx + 1} out of range for meta.", file=sys.stderr)
        sys.exit(1)
    subs = meta[gidx].get('subGoals') or []
    if sidx < 0 or sidx >= len(subs):
        print(f"Subgoal {item_id} out of range (have {len(subs)}).", file=sys.stderr)
        sys.exit(1)
    subs[sidx]['state'] = 'checked' if checked else 'open'
    meta[gidx]['subGoals'] = subs
    update_meta(meta_row[0], json.dumps(meta, ensure_ascii=False))
    print(f"{'checked' if checked else 'unchecked'}: {item_id} {subs[sidx].get('text', '')}")


def add_subgoal(parent_id, text):
    """Append a new open subgoal under the goal at `parent_id` (1-based)."""
    goals_row, meta_row = fetch_today()
    if not goals_row or not meta_row:
        print("No goals/goals_meta row for today — create a top-level goal in the app first.",
              file=sys.stderr)
        sys.exit(1)
    try:
        gidx = int(parent_id) - 1
    except ValueError:
        print(f"Bad parent id: {parent_id} (expected top-level goal number, e.g. `1`)",
              file=sys.stderr)
        sys.exit(1)

    try:
        meta = json.loads(meta_row[1])
    except Exception as e:
        print(f"goals_meta JSON parse failed: {e}", file=sys.stderr)
        sys.exit(1)

    goals = parse_goals_content(goals_row[1])
    if gidx < 0 or gidx >= len(goals):
        print(f"Parent goal {parent_id} out of range (have {len(goals)}).", file=sys.stderr)
        sys.exit(1)

    while len(meta) <= gidx:
        meta.append({'subGoals': []})
    subs = meta[gidx].get('subGoals') or []
    subs.append({'state': 'open', 'text': text, 'indent': 0, 'collapsed': False})
    meta[gidx]['subGoals'] = subs
    update_meta(meta_row[0], json.dumps(meta, ensure_ascii=False))
    new_id = f"{gidx + 1}.{len(subs)}"
    print(f"added: {new_id} {text}")


def _slug(text):
    s = re.sub(r'[^\w\s-]', '', (text or '').strip().lower())
    s = re.sub(r'[\s_]+', '-', s).strip('-')
    return s or 'untitled'


DESCRIBE_TEMPLATE = """# {title}

_Instruction file for a subagent working on this item under `/multi-agent`._

## Problem
_What is broken or missing? Include the user-visible symptom._

## Reproduction / Context
_How to reproduce. Relevant files, recent changes, feature it belongs to._

## Acceptance criteria
_What "done" looks like — must be user-verifiable in the live app._
- [ ] …
- [ ] …

## Files likely involved
_Paths. Populate with `mod graph` output + codebase search before fanning out._
- `…`

## Scope / Isolation
_Folders/files this subagent owns. Other subagents MUST NOT touch these._
- owns: `…`
- hands-off: `…`

## Health requirements (mandatory)
- Run `mod query /home/stellar-thread/Applications/Delta-Note-Local` at start and end.
- Do not leave any touched file RED or push a YELLOW file into ORANGE/RED.
- Run `mod graph <file>` before renaming/moving/splitting any file.
- Fill any unfilled `README.md` / `watch.py` in folders touched (in place — see CLAUDE.md).

## Deliverable
Return a structured report with these sections:

- **Summary** — ≤5 bullets: what changed and why.
- **Files changed** — one path per line, with a short "why" per file.
- **Modulario health** — `mod query` Red/Orange/Yellow/Lime/Green counts before → after. Name any file that moved up a tier.
- **Docs + watches** — for every folder you edited, one line per file using this shape:
    - `FILLED  <folder>/README.md  — <one-line summary of Purpose/Owns you wrote>`
    - `FILLED  <folder>/watch.py   — <checks added, e.g. "check_imports asserts X,Y importable; check_public_api asserts foo() exists">`
    - `UNCHANGED  <folder>/`  (if neither template had the marker)
    - `SKIPPED   <folder>/README.md  — <reason>` (only valid reason: folder is not in your Scope / Isolation)
- **Blocked / out of scope** — anything you hit that you didn't fix because it crossed the isolation fence. Name the file and the reason.
- **Self-check** — do NOT run `mod goals check <id>`. Hand back to the orchestrator.
"""


def describe_items(target_id=None):
    """Create one .md file per item in DESCRIBE_DIR.

    - target_id None   -> one file per top-level goal.
    - target_id "1"    -> one file per subgoal of goal 1.
    - target_id "1.2"  -> one file for that specific subgoal only.
    """
    items, _ = build_view()
    if target_id is None:
        picks = [i for i in items if i['kind'] == 'goal']
    elif '.' in str(target_id):
        picks = [i for i in items if i['id'] == str(target_id)]
        if not picks:
            print(f"No item with id {target_id}.", file=sys.stderr)
            sys.exit(1)
    else:
        try:
            gidx = int(target_id) - 1
        except ValueError:
            print(f"Bad id: {target_id}", file=sys.stderr)
            sys.exit(1)
        picks = [i for i in items if i['kind'] == 'sub' and i['goal_idx'] == gidx]
        if not picks:
            print(f"No subgoals under goal {target_id}.", file=sys.stderr)
            sys.exit(1)

    DESCRIBE_DIR.mkdir(parents=True, exist_ok=True)
    created, skipped = [], []
    for item in picks:
        fname = f"{item['id']}-{_slug(item['text'])}.md"
        path = DESCRIBE_DIR / fname
        if path.exists():
            skipped.append(fname)
            continue
        path.write_text(DESCRIBE_TEMPLATE.format(title=item['text']))
        created.append(fname)

    print(f"dir: {DESCRIBE_DIR}")
    for f in created:
        print(f"  created  {f}")
    for f in skipped:
        print(f"  skipped  {f} (exists)")


# ─────────────────────────── Hook management ────────────────────────────
def _load_json(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n')


def _register_session_hook_in(path):
    s = _load_json(path)
    hooks = s.setdefault('hooks', {})
    sess = hooks.setdefault('SessionStart', [])
    cmd = str(SESSION_HOOK)
    if any(any(h.get('command') == cmd for h in e.get('hooks', [])) for e in sess):
        return
    sess.append({'hooks': [{'type': 'command', 'command': cmd, 'timeout': 10}]})
    _save_json(path, s)


def _unregister_session_hook_in(path):
    s = _load_json(path)
    hooks = s.get('hooks', {})
    sess = hooks.get('SessionStart', [])
    cmd = str(SESSION_HOOK)
    new_sess = []
    for e in sess:
        filtered = [h for h in e.get('hooks', []) if h.get('command') != cmd]
        if filtered:
            e = {**e, 'hooks': filtered}
            new_sess.append(e)
    hooks['SessionStart'] = new_sess
    _save_json(path, s)


def register_session_hook():
    _register_session_hook_in(CLAUDE_SETTINGS)
    _register_session_hook_in(CODEX_HOOKS)


def unregister_session_hook():
    _unregister_session_hook_in(CLAUDE_SETTINGS)
    _unregister_session_hook_in(CODEX_HOOKS)


# ─────────────────────────── Output for SessionStart hook ────────────────────────────
SAFETY_RULE = (
    "Rule: Use `mod goals check <id>` to mark an item complete ONLY after the "
    "user has explicitly said the fix/feature works. Do not self-check based "
    "on your own testing, a passing build, or your belief that the change is "
    "correct. User confirmation is required. To reopen, use `mod goals uncheck <id>`."
)

HELP_BLURB = (
    "View: `mod goals list`.  Check: `mod goals check <id>`.  "
    "Uncheck: `mod goals uncheck <id>`.  IDs look like `1` (top goal) or `1.2` (subgoal)."
)


def session_start_output():
    cfg = load_config()
    if not cfg.get('enabled'):
        return ''
    try:
        items, text = build_view()
    except Exception as e:
        return f"[Modulario Goals] error reading goals: {e}"
    body = (
        "[Modulario Goals] Today's goals (Delta Note):\n"
        f"{text}\n\n"
        f"{HELP_BLURB}\n"
        f"{SAFETY_RULE}"
    )
    return body


# ─────────────────────────── CLI dispatch ────────────────────────────
def cmd_on():
    cfg = load_config()
    cfg['enabled'] = True
    save_config(cfg)
    register_session_hook()
    print("goals: ON (Claude/Codex SessionStart hook registered)")


def cmd_off():
    cfg = load_config()
    cfg['enabled'] = False
    save_config(cfg)
    unregister_session_hook()
    print("goals: OFF (Claude/Codex SessionStart hook removed)")


def cmd_status():
    cfg = load_config()
    state = 'ON' if cfg.get('enabled') else 'OFF'
    print(f"goals: {state}")


def cmd_list():
    _, text = build_view()
    print(text)


def cmd_session_start():
    body = session_start_output()
    if not body:
        return
    out = {
        'hookSpecificOutput': {
            'hookEventName': 'SessionStart',
            'additionalContext': body,
        }
    }
    print(json.dumps(out))


def main(argv):
    if not argv or argv[0] in ('-h', '--help', 'help'):
        print("Usage: mod goals {on|off|status|list|check <id>|uncheck <id>|"
              "add <parent_id> <text>|describe [id]}")
        return
    cmd = argv[0]
    if cmd == 'on':
        cmd_on()
    elif cmd == 'off':
        cmd_off()
    elif cmd == 'status':
        cmd_status()
    elif cmd == 'list':
        cmd_list()
    elif cmd == 'check':
        if len(argv) < 2:
            print("Usage: mod goals check <id>", file=sys.stderr)
            sys.exit(1)
        set_checked(argv[1], True)
    elif cmd == 'uncheck':
        if len(argv) < 2:
            print("Usage: mod goals uncheck <id>", file=sys.stderr)
            sys.exit(1)
        set_checked(argv[1], False)
    elif cmd == 'add':
        if len(argv) < 3:
            print('Usage: mod goals add <parent_id> "<text>"', file=sys.stderr)
            sys.exit(1)
        add_subgoal(argv[1], ' '.join(argv[2:]))
    elif cmd == 'describe':
        parent = argv[1] if len(argv) >= 2 else None
        describe_items(parent)
    elif cmd == 'session-start':
        cmd_session_start()
    else:
        print(f"Unknown goals subcommand: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main(sys.argv[1:])
