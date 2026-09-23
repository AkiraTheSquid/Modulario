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
PRE_HOOK_SCRIPT="$MODULARIO_DIR/scripts/pre_edit_gate.py"
THRESHOLDS="$MODULARIO_DIR/configs/thresholds.json"
TARGET_CONFIG="$MODULARIO_DIR/configs/target.txt"
CURRENT_TARGET_CONFIG="$MODULARIO_DIR/configs/current-target.txt"
CODEX_HOOKS="$HOME/.codex/hooks.json"
STOP_GATE_CMD="python3 $MODULARIO_DIR/scripts/stop_gate.py"
RESTART_HOOK_CMD="$HOME/.config/claude-autostart/restart-hook.sh"

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
echo "$TARGET_DIR" > "$CURRENT_TARGET_CONFIG"
STATE_FILE=$(python3 - "$TARGET_DIR" "$MODULARIO_DIR" <<'PYEOF'
import hashlib
import os
import sys

target, root = sys.argv[1:]
digest = hashlib.md5(os.path.realpath(target).encode()).hexdigest()[:12]
print(os.path.join(root, "data", "state", digest + ".json"))
PYEOF
)
echo "✓ Target set to: $TARGET_DIR"

# ─── Claude settings hook ─────────────────────────────────────────────────────
echo ""
echo "Installing global Claude hooks; registry performs project filtering."
SETTINGS_FILE="$HOME/.claude/settings.json"
mkdir -p "$HOME/.claude"

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
    "matcher": "Bash|apply_patch|Write|Edit|NotebookEdit",
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

STOP_GATE_CMD="$STOP_GATE_CMD" RESTART_HOOK_CMD="$RESTART_HOOK_CMD" PRE_HOOK_CMD="python3 $PRE_HOOK_SCRIPT" python3 - "$CODEX_HOOKS" "$HOOK_SCRIPT" <<'PYEOF'
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
pre_hooks = hooks.setdefault("PreToolUse", [])
post_hooks = hooks.setdefault("PostToolUse", [])
stop_hooks = hooks.setdefault("Stop", [])

pre_cmd = os.environ["PRE_HOOK_CMD"]
pre_installed = any(
    any(h.get("command") == pre_cmd for h in entry.get("hooks", []))
    for entry in pre_hooks
)

if pre_installed:
    print(f"  PreToolUse hook already present in {hooks_file}")
else:
    pre_hooks.append({
        "matcher": "Bash|apply_patch|Write|Edit|NotebookEdit",
        "hooks": [
            {
                "type": "command",
                "command": pre_cmd,
                "timeout": 20
            }
        ]
    })
    with open(hooks_file, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print(f"  ✓ PreToolUse hook added to {hooks_file}")

already_installed = any(
    any(h.get("command") == hook_cmd for h in entry.get("hooks", []))
    for entry in post_hooks
)

if already_installed:
    print(f"  Hook already present in {hooks_file}")
else:
    post_hooks.append({
        "matcher": "Bash|apply_patch|Write|Edit|NotebookEdit",
        "hooks": [
            {
                "type": "command",
                "command": hook_cmd,
                "timeout": 300
            }
        ]
    })
    with open(hooks_file, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print(f"  ✓ Hook added to {hooks_file}")

stop_cmd = os.environ["STOP_GATE_CMD"]
restart_cmd = os.environ.get("RESTART_HOOK_CMD", "")
primary_stop_entry = None
for entry in stop_hooks:
    if entry.get("matcher", "") == "":
        primary_stop_entry = entry
        break

if primary_stop_entry is None:
    primary_stop_entry = {"matcher": "", "hooks": []}
    stop_hooks.append(primary_stop_entry)

primary_stop_hooks = primary_stop_entry.setdefault("hooks", [])
stop_added = False
restart_added = False

if not any(h.get("command") == stop_cmd for h in primary_stop_hooks):
    primary_stop_hooks.append({
        "type": "command",
        "command": stop_cmd,
        "timeout": 240
    })
    stop_added = True

if restart_cmd and not any(h.get("command") == restart_cmd for h in primary_stop_hooks):
    primary_stop_hooks.append({
        "type": "command",
        "command": restart_cmd,
        "timeout": 20
    })
    restart_added = True

deduped_stop_hooks = []
for entry in stop_hooks:
    matcher = entry.get("matcher", "")
    hooks_list = []
    for hook in entry.get("hooks", []):
        cmd = hook.get("command")
        if matcher == "" and cmd in {stop_cmd, restart_cmd} and entry is not primary_stop_entry:
            continue
        hooks_list.append(hook)
    if hooks_list:
        deduped_stop_hooks.append({"matcher": matcher, "hooks": hooks_list})
hooks["Stop"] = deduped_stop_hooks

with open(hooks_file, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")

if stop_added:
    print(f"  ✓ Stop hook added to {hooks_file}")
else:
    print(f"  Stop hook already present in {hooks_file}")

if restart_cmd:
    if restart_added:
        print(f"  ✓ Restart hook added to {hooks_file}")
    else:
        print(f"  Restart hook already present in {hooks_file}")

PYEOF

# Canonical updater: adds Claude Pre/Stop hooks and updates existing matchers/timeouts.
python3 - "$MODULARIO_DIR" <<'PYEOF'
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root / "scripts"))
from cli_common import ensure_codex_hook, ensure_global_hook

ensure_global_hook()
ensure_codex_hook()
print("  ✓ Provider-aware global hooks synchronized")
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
echo "Start project picker + TUI:"
echo ""
echo "  mod"
echo ""
echo "Global Claude/Codex hooks route only registered project edits."
echo ""
echo "To re-run analysis manually:"
echo "  python3 $MODULARIO_DIR/scripts/modulario-analyze.py \\"
echo "    --target $TARGET_DIR \\"
echo "    --output $STATE_FILE"
echo ""
