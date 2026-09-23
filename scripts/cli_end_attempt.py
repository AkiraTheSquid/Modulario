"""`mod end-attempt "<reason>"` — escape hatch for stop-gate sessions."""
import json
import sys
from datetime import datetime

from cli_common import MODULARIO_DIR


def cmd_end_attempt(argv):
    """Mark the most recent stop-gate session as released so the next Stop
    hook allows immediately. Requires a reason — logged to
    logs/escape_attempts.jsonl for audit.
    """
    sys.path.insert(0, str(MODULARIO_DIR / 'scripts'))
    import stopgate_config

    if not stopgate_config.load().get('claude', {}).get('escape_hatch', True):
        print("mod end-attempt: escape hatch is disabled in settings.", file=sys.stderr)
        sys.exit(1)

    if not argv:
        print('Usage: mod end-attempt "<reason>"', file=sys.stderr)
        print("Reason is required — explain why the stop-gate cycle should end.",
              file=sys.stderr)
        sys.exit(1)
    reason = " ".join(argv).strip()
    if not reason:
        print("mod end-attempt: reason cannot be empty.", file=sys.stderr)
        sys.exit(1)

    tmp_dir = MODULARIO_DIR / 'tmp'
    candidates = sorted(
        tmp_dir.glob('stop_retries_*.json'),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ) if tmp_dir.exists() else []
    if not candidates:
        print("mod end-attempt: no active stop-gate session found.", file=sys.stderr)
        sys.exit(1)
    target_file = candidates[0]

    try:
        state = json.loads(target_file.read_text())
    except Exception:
        state = {}
    state['final_shown'] = True
    state['escaped'] = True
    state['escape_reason'] = reason
    target_file.write_text(json.dumps(state))

    session_id = target_file.stem.replace('stop_retries_', '')
    logs_dir = MODULARIO_DIR / 'logs'
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_entry = {
        'timestamp': datetime.now().isoformat(),
        'session_id': session_id,
        'reason': reason,
    }
    with open(logs_dir / 'escape_attempts.jsonl', 'a') as fh:
        fh.write(json.dumps(log_entry) + '\n')

    print(f"Stop-gate released for session {session_id}.")
    print(f"Reason logged: {reason}")
    print("Next Stop attempt will allow immediately.")
