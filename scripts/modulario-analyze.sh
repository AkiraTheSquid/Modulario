#!/usr/bin/env bash
# Modulario PostToolUse hook
# Called by Claude Code after every Write, Edit, or NotebookEdit.
# 1. Reads changed file path from hook JSON (stdin)
# 2. Runs the analyzer against the configured target directory
# 3. Prints compact structural summary to stdout (Claude sees this in its context)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODULARIO_DIR="$(dirname "$SCRIPT_DIR")"
ANALYZER="$SCRIPT_DIR/modulario-analyze.py"
THRESHOLDS="$MODULARIO_DIR/configs/thresholds.json"
STATE_FILE="$MODULARIO_DIR/data/state.json"
TARGET_CONFIG="$MODULARIO_DIR/configs/target.txt"

# Bail out silently if not configured
if [ ! -f "$TARGET_CONFIG" ]; then
    exit 0
fi

TARGET_DIR=$(cat "$TARGET_CONFIG")

# Read stdin (Claude hook JSON payload) and extract changed file path
PAYLOAD=$(cat)
CHANGED_FILE=""
if [ -n "$PAYLOAD" ]; then
    CHANGED_FILE=$(echo "$PAYLOAD" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    # PostToolUse payload: tool_input.file_path (Write/Edit)
    ti = data.get('tool_input', {})
    path = ti.get('file_path', '') or ti.get('path', '')
    print(path)
except Exception:
    pass
" 2>/dev/null || true)
fi

# Run analyzer — updates state.json and prints Claude summary to stdout
python3 "$ANALYZER" \
    --target "$TARGET_DIR" \
    --output "$STATE_FILE" \
    --thresholds "$THRESHOLDS" \
    --changed-file "$CHANGED_FILE" \
    --print-summary
