#!/usr/bin/env bash
# Modulario PostToolUse hook for Claude and Codex.
#
# Reads the current target from configs/current-target.txt (written by `mod <dir>`).
# Fires for every tracked write/edit event regardless of where it was started.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODULARIO_DIR="$(dirname "$SCRIPT_DIR")"
ANALYZER="$SCRIPT_DIR/modulario-analyze.py"
WATCH_RUNNER="$SCRIPT_DIR/modulario-watch-runner.py"
THRESHOLDS="$MODULARIO_DIR/configs/thresholds.json"
CURRENT_TARGET="$MODULARIO_DIR/configs/current-target.txt"
ALERT_SOUND="$MODULARIO_DIR/assets/modulario-alert.wav"
REDUCTION_SOUND="$MODULARIO_DIR/assets/modulario-reduction-chime.wav"
FALLBACK_SOUND="/usr/share/sounds/Yaru/stereo/dialog-error.oga"
DEBUG_SOUND="${MODULARIO_DEBUG_SOUND:-0}"

# No active target — exit silently until `mod <dir>` is run
[ -f "$CURRENT_TARGET" ] || exit 0

TARGET_DIR=$(cat "$CURRENT_TARGET")
[ -d "$TARGET_DIR" ] || exit 0

# Extract changed file from hook JSON payload (stdin)
PAYLOAD=$(cat)
CHANGED_FILE=""
if [ -n "$PAYLOAD" ]; then
    CHANGED_FILE=$(echo "$PAYLOAD" | python3 -c "
import json, sys

def pick(mapping, *keys):
    cur = mapping
    for key in keys:
        if not isinstance(cur, dict):
            return ''
        cur = cur.get(key)
    return cur if isinstance(cur, str) else ''

try:
    data = json.load(sys.stdin)
except Exception:
    data = {}

candidates = [
    pick(data, 'tool_input', 'file_path'),
    pick(data, 'tool_input', 'path'),
    pick(data, 'toolInput', 'file_path'),
    pick(data, 'toolInput', 'path'),
    pick(data, 'payload', 'tool_input', 'file_path'),
    pick(data, 'payload', 'tool_input', 'path'),
    pick(data, 'payload', 'toolInput', 'file_path'),
    pick(data, 'payload', 'toolInput', 'path'),
]

for item in candidates:
    if item:
        print(item)
        break
" 2>/dev/null || true)
fi

# Derive state file path (same hash logic as CLI)
STATE_FILE=$(python3 -c "
import hashlib, os
h = hashlib.md5(os.path.realpath('$TARGET_DIR').encode()).hexdigest()[:12]
print('$MODULARIO_DIR/data/state/' + h + '.json')
")
mkdir -p "$(dirname "$STATE_FILE")"

OUTPUT=$(python3 "$ANALYZER" \
    --target "$TARGET_DIR" \
    --output "$STATE_FILE" \
    --thresholds "$THRESHOLDS" \
    --changed-file "$CHANGED_FILE" \
    --print-summary \
    --check-violations \
    | python3 "$WATCH_RUNNER" \
        --target "$TARGET_DIR" \
        --changed-file "$CHANGED_FILE")

SOUND_KIND=$(python3 - "$STATE_FILE" "$CHANGED_FILE" "$THRESHOLDS" <<'PY'
import json
import os
import sys

state_path = sys.argv[1]
changed_file = sys.argv[2]
thresholds_path = sys.argv[3]

try:
    with open(thresholds_path, 'r', encoding='utf-8') as fh:
        thresholds = json.load(fh)
except Exception:
    thresholds = {}

loc_max = (thresholds.get('loc_bands') or [0])[-1]

if not changed_file or not os.path.exists(state_path):
    print('alert')
    raise SystemExit(0)

try:
    with open(state_path, 'r', encoding='utf-8') as fh:
        state = json.load(fh)
except Exception:
    print('alert')
    raise SystemExit(0)

changed = None
for item in state.get('files', []):
    if item.get('abs_path') == changed_file or str(item.get('path', '')).endswith(changed_file):
        changed = item
        break

if not changed:
    print('alert')
    raise SystemExit(0)

loc_delta = changed.get('loc_delta', 0) or 0
loc_value = changed.get('loc', 0) or 0

if loc_delta < 0 and loc_value > loc_max:
    print('reduction')
else:
    print('alert')
PY
)

if [ "$DEBUG_SOUND" = "1" ]; then
    case "$SOUND_KIND" in
        reduction|alert)
            printf '[Modulario] sound=%s changed=%s\n' "$SOUND_KIND" "${CHANGED_FILE:-N/A}" >&2
            ;;
        *)
            printf '[Modulario] sound=unknown changed=%s\n' "${CHANGED_FILE:-N/A}" >&2
            ;;
    esac
fi

ALERT_TEXT=$(echo "$OUTPUT" | python3 -c "
import json, sys
raw = sys.stdin.read().strip()
if not raw:
    raise SystemExit(0)
try:
    data = json.loads(raw)
    text = data.get('hookSpecificOutput', {}).get('additionalContext', '')
except Exception:
    text = raw
alerts = [line.strip() for line in text.splitlines() if line.strip().startswith('[ALERT]')]
if alerts:
    print('\n'.join(alerts))
" 2>/dev/null || true)

if [ -n "$ALERT_TEXT" ]; then
    export DISPLAY="${DISPLAY:-:0}"
    export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"
    notify-send -u critical -t 15000 -i dialog-warning "Modulario threshold alert" "$ALERT_TEXT" 2>/dev/null || true
    if [ -t 1 ]; then
        printf '\a\a\a' || true
    fi
    (
        if [ "$SOUND_KIND" = "reduction" ] && command -v pw-play >/dev/null 2>&1 && [ -f "$REDUCTION_SOUND" ]; then
            pw-play --volume 3.0 "$REDUCTION_SOUND" >/dev/null 2>&1 || true
        elif [ "$SOUND_KIND" = "reduction" ] && command -v paplay >/dev/null 2>&1 && [ -f "$REDUCTION_SOUND" ]; then
            paplay "$REDUCTION_SOUND" >/dev/null 2>&1 || true
        elif command -v pw-play >/dev/null 2>&1 && [ -f "$ALERT_SOUND" ]; then
            pw-play --volume 4.5 "$ALERT_SOUND" >/dev/null 2>&1 || true
        elif command -v paplay >/dev/null 2>&1 && [ -f "$ALERT_SOUND" ]; then
            paplay "$ALERT_SOUND" >/dev/null 2>&1 || true
        elif command -v pw-play >/dev/null 2>&1 && [ -f "$FALLBACK_SOUND" ]; then
            pw-play --volume 4.0 "$FALLBACK_SOUND" >/dev/null 2>&1 || true
        elif command -v paplay >/dev/null 2>&1 && [ -f "$FALLBACK_SOUND" ]; then
            paplay "$FALLBACK_SOUND" >/dev/null 2>&1 || true
        elif command -v canberra-gtk-play >/dev/null 2>&1; then
            if [ "$SOUND_KIND" = "reduction" ]; then
                canberra-gtk-play -i complete >/dev/null 2>&1 || true
            else
                canberra-gtk-play -i dialog-error >/dev/null 2>&1 || true
            fi
        fi
    ) &
fi

echo "$OUTPUT"
