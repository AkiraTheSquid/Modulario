#!/usr/bin/env bash
# Modulario install script
# 1. Checks Python deps (installs if missing)
# 2. Asks for target project directory
# 3. Writes PostToolUse hooks into Claude and Codex settings, plus Codex Stop
# 4. Runs initial analysis pass

set -euo pipefail

MODULARIO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ANALYZER="$MODULARIO_DIR/scripts/modulario-analyze.py"
HOOK_SCRIPT="$MODULARIO_DIR/scripts/modulario-hook.sh"
THRESHOLDS="$MODULARIO_DIR/configs/thresholds.json"
STATE_FILE="$MODULARIO_DIR/data/state.json"
TARGET_CONFIG="$MODULARIO_DIR/configs/target.txt"
CODEX_HOOKS="$HOME/.codex/hooks.json"
STOP_GATE_CMD="python3 $MODULARIO_DIR/scripts/stop_gate.py"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║          MODULARIO — Installer               ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ─── Check Python ────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Please install Python 3.8+."
    exit 1
fi
echo "✓ Python: $(python3 --version)"

# ─── Install Python deps ──────────────────────────────────────────────────────
echo ""
echo "Checking Python dependencies..."

install_if_missing() {
    local pkg="$1"
    if python3 -c "import $pkg" 2>/dev/null; then
        echo "  ✓ $pkg already installed"
    else
        echo "  Installing $pkg..."
        pip3 install --quiet "$pkg"
        echo "  ✓ $pkg installed"
    fi
}

install_if_missing rich
install_if_missing watchdog || echo "  (watchdog optional — will fall back to polling)"

# ─── Make scripts executable ──────────────────────────────────────────────────
chmod +x "$HOOK_SCRIPT"
chmod +x "$ANALYZER"

# ─── Target directory ────────────────────────────────────────────────────────
echo ""
if [ -f "$TARGET_CONFIG" ]; then
    CURRENT_TARGET=$(cat "$TARGET_CONFIG")
    echo "Current target: $CURRENT_TARGET"
    read -rp "Enter new target directory (or press Enter to keep current): " NEW_TARGET
    TARGET_DIR="${NEW_TARGET:-$CURRENT_TARGET}"
else
    read -rp "Enter the project directory to monitor: " TARGET_DIR
fi

TARGET_DIR="${TARGET_DIR/#\~/$HOME}"  # expand tilde
TARGET_DIR="$(realpath "$TARGET_DIR")"

if [ ! -d "$TARGET_DIR" ]; then
    echo "ERROR: Directory not found: $TARGET_DIR"
    exit 1
fi

echo "$TARGET_DIR" > "$TARGET_CONFIG"
echo "✓ Target set to: $TARGET_DIR"

# ─── Claude settings hook ─────────────────────────────────────────────────────
echo ""
echo "Where should the Claude PostToolUse hook be installed?"
echo "  1) Project-specific: $TARGET_DIR/.claude/settings.json"
echo "  2) Global: $HOME/.claude/settings.json"
read -rp "Choose [1/2] (default: 1): " CHOICE
CHOICE="${CHOICE:-1}"

if [ "$CHOICE" = "2" ]; then
    SETTINGS_FILE="$HOME/.claude/settings.json"
    mkdir -p "$HOME/.claude"
else
    SETTINGS_FILE="$TARGET_DIR/.claude/settings.json"
    mkdir -p "$TARGET_DIR/.claude"
fi

echo ""
echo "Installing hook into: $SETTINGS_FILE"

# Use Python to safely merge the hook into existing settings
python3 - "$SETTINGS_FILE" "$HOOK_SCRIPT" <<'PYEOF'
import json
import sys
import os

settings_file = sys.argv[1]
hook_cmd = sys.argv[2]

# Load existing settings
if os.path.exists(settings_file):
    try:
        with open(settings_file) as f:
            settings = json.load(f)
    except Exception:
        settings = {}
else:
    settings = {}

# Build the hook entry
hook_entry = {
    "matcher": "Write|Edit|NotebookEdit",
    "hooks": [
        {
            "type": "command",
            "command": hook_cmd
        }
    ]
}

# Merge into PostToolUse list (avoid duplicates by command)
hooks_section = settings.setdefault("hooks", {})
post_hooks = hooks_section.setdefault("PostToolUse", [])

# Check if already installed (same command)
already_installed = any(
    any(h.get("command") == hook_cmd for h in entry.get("hooks", []))
    for entry in post_hooks
)

if already_installed:
    print(f"  Hook already present in {settings_file}")
else:
    post_hooks.append(hook_entry)
    with open(settings_file, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print(f"  ✓ Hook added to {settings_file}")

PYEOF

# ─── Codex hooks.json hook ────────────────────────────────────────────────────
echo ""
echo "Installing Codex hook into: $CODEX_HOOKS"
mkdir -p "$(dirname "$CODEX_HOOKS")"

STOP_GATE_CMD="$STOP_GATE_CMD" python3 - "$CODEX_HOOKS" "$HOOK_SCRIPT" <<'PYEOF'
import json
import os
import sys

hooks_file = sys.argv[1]
hook_cmd = sys.argv[2]

if os.path.exists(hooks_file):
    try:
        with open(hooks_file) as f:
            settings = json.load(f)
    except Exception:
        settings = {}
else:
    settings = {}

hooks = settings.setdefault("hooks", {})
post_hooks = hooks.setdefault("PostToolUse", [])
stop_hooks = hooks.setdefault("Stop", [])

already_installed = any(
    any(h.get("command") == hook_cmd for h in entry.get("hooks", []))
    for entry in post_hooks
)

if already_installed:
    print(f"  Hook already present in {hooks_file}")
else:
    post_hooks.append({
        "matcher": "Write|Edit|NotebookEdit",
        "hooks": [
            {
                "type": "command",
                "command": hook_cmd,
                "timeout": 20
            }
        ]
    })
    with open(hooks_file, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print(f"  ✓ Hook added to {hooks_file}")

stop_cmd = os.environ["STOP_GATE_CMD"]
stop_installed = any(
    any(h.get("command") == stop_cmd for h in entry.get("hooks", []))
    for entry in stop_hooks
)

if stop_installed:
    print(f"  Stop hook already present in {hooks_file}")
else:
    stop_hooks.append({
        "matcher": "",
        "hooks": [
            {
                "type": "command",
                "command": stop_cmd,
                "timeout": 20
            }
        ]
    })
    with open(hooks_file, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print(f"  ✓ Stop hook added to {hooks_file}")

PYEOF

# ─── Initial analysis pass ───────────────────────────────────────────────────
echo ""
echo "Running initial analysis..."
mkdir -p "$(dirname "$STATE_FILE")"

python3 "$ANALYZER" \
    --target "$TARGET_DIR" \
    --output "$STATE_FILE" \
    --thresholds "$THRESHOLDS"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║              Setup complete!                 ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "Start the TUI in a separate terminal:"
echo ""
echo "  python3 $MODULARIO_DIR/tui/modulario-tui.py"
echo ""
echo "The TUI will update automatically after each Claude file edit."
echo ""
echo "To re-run analysis manually:"
echo "  python3 $MODULARIO_DIR/scripts/modulario-analyze.py \\"
echo "    --target $TARGET_DIR \\"
echo "    --output $STATE_FILE"
echo ""
