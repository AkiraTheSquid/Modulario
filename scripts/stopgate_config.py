"""Stopgate feature toggles + tunables.

Shared by scripts/stop_gate.py and tui/settings_page.py. One JSON file at
configs/stopgate.json. Missing fields fall back to DEFAULTS — safe to extend.
"""
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "stopgate.json"

FEATURES = ("watches", "cycles", "unfilled_docs", "oversized_files")

# Agent-facing features. Kept under the legacy `claude` key so existing
# configs and callers continue to work without migration.
CLAUDE_FEATURES = (
    "gate_master",        # master switch for the entire stop_gate.py hook
    "summary_block",      # [Modulario] PostToolUse block
    "red_hotspots",       # red hotspots section inside summary
    "threshold_alerts",   # [!] LOC/DEPS warnings
    "violation_alerts",   # [VIOLATION] circular import / private access
    "import_alerts",      # build_import_alerts() output
    "coupling_alerts",    # [coupling] co-change hints
    "doc_nag",            # [DOC] unfilled README.md reminders
    "watch_nag",          # [WATCH] unfilled watch.py reminders
    "auto_create_readme", # auto-create README.md template in every folder
    "auto_create_watch",  # auto-create watch.py template in every folder
    "escape_hatch",       # allow Claude to bypass retries via `mod end-attempt`
)

DEFAULTS = {
    "enabled": {f: True for f in FEATURES},
    "claude": {f: True for f in CLAUDE_FEATURES},
    "loc_limit": 700,
    "notify_loc": 650,
}


def load():
    cfg = {
        "enabled": dict(DEFAULTS["enabled"]),
        "claude": dict(DEFAULTS["claude"]),
        "loc_limit": DEFAULTS["loc_limit"],
        "notify_loc": DEFAULTS["notify_loc"],
    }
    try:
        raw = json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        return cfg
    en = raw.get("enabled") or {}
    for f in FEATURES:
        if isinstance(en.get(f), bool):
            cfg["enabled"][f] = en[f]
    cl = raw.get("claude") or {}
    for f in CLAUDE_FEATURES:
        if isinstance(cl.get(f), bool):
            cfg["claude"][f] = cl[f]
    lim = raw.get("loc_limit")
    if isinstance(lim, int) and lim > 0:
        cfg["loc_limit"] = lim
    nl = raw.get("notify_loc")
    if isinstance(nl, int) and nl > 0:
        cfg["notify_loc"] = nl
    return cfg


def save(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")


def is_enabled(feature):
    return load()["enabled"].get(feature, True)


def loc_limit():
    return load()["loc_limit"]
