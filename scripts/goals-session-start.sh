#!/usr/bin/env bash
# SessionStart hook — inject today's Delta Note goals into Claude's context.
# Silent when `mod goals off` or when there are no goals.

set -euo pipefail
if [ "${MODULARIO_SKIP:-}" = "1" ]; then
  exit 0
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/goals.py" session-start
