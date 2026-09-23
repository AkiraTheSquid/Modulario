#!/usr/bin/env bash
# Provider-aware Modulario PostToolUse hook for Claude Code + Codex.

set -euo pipefail

if [ "${MODULARIO_SKIP:-}" = "1" ]; then
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/post_tool_hook.py"
