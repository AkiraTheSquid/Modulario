"""Stopgate feature toggles + tunables.

Shared by scripts/stop_gate.py and tui/settings_page.py. One JSON file at
configs/stopgate.json. Missing fields fall back to DEFAULTS — safe to extend.
"""
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "stopgate.json"

FEATURES = ("watches", "cycles", "unfilled_docs", "oversized_files")

DEFAULTS = {
    "enabled": {f: True for f in FEATURES},
    "loc_limit": 700,
}


def load():
    cfg = {"enabled": dict(DEFAULTS["enabled"]), "loc_limit": DEFAULTS["loc_limit"]}
    try:
        raw = json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        return cfg
    en = raw.get("enabled") or {}
    for f in FEATURES:
        if isinstance(en.get(f), bool):
            cfg["enabled"][f] = en[f]
    lim = raw.get("loc_limit")
    if isinstance(lim, int) and lim > 0:
        cfg["loc_limit"] = lim
    return cfg


def save(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")


def is_enabled(feature):
    return load()["enabled"].get(feature, True)


def loc_limit():
    return load()["loc_limit"]
