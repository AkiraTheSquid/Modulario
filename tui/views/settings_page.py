"""Stopgate settings page — toggle features + edit max LOC.

Triggered by 'e' from the main view. Reads/writes configs/stopgate.json via
scripts/stopgate_config.py (shared with the stop hook).
"""
import curses
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import cli_autostart
import stopgate_config
from views.input_prompt import prompt_input

_MODULARIO_DIR = Path(__file__).resolve().parent.parent.parent
_CURRENT_TARGET_PATH = _MODULARIO_DIR / "configs" / "current-target.txt"
_DELTA_NOTE_PROJECT_PATH = _MODULARIO_DIR / "configs" / "projects" / "delta-note.json"

_C_HEADER = 7
_C_GREEN  = 5
_C_RED    = 1

_FEATURE_LABELS = [
    ("watches",         "Watches              ", "Run `mod watch run` and block on failures"),
    ("cycles",          "Circular imports     ", "Block when state.json reports import cycles"),
    ("unfilled_docs",   "Unfilled folder docs ", "Block when touched folders have template README.md"),
    ("oversized_files", "Oversized files      ", "Block when any file exceeds the Max LOC limit"),
]

# Agent-facing feature toggles. Stored under cfg["claude"] for backward
# compatibility with existing configs.
_CLAUDE_LABELS = [
    ("gate_master",        "Stop gate (master)   ", "Master switch: block Claude/Codex from ending its turn"),
    ("summary_block",      "PostToolUse summary  ", "Emit [Modulario] block after every tool call"),
    ("red_hotspots",       "Red hotspots         ", "Show red-zone files inside the summary block"),
    ("threshold_alerts",   "Threshold alerts     ", "[!] LOC/DEPS warnings for the changed file"),
    ("violation_alerts",   "Violation alerts     ", "[VIOLATION] cycle / private-access lines"),
    ("import_alerts",      "Import alerts        ", "Warnings from import_alerts.py"),
    ("coupling_alerts",    "Coupling alerts      ", "[coupling] co-change suggestions"),
    ("doc_nag",            "Doc nag              ", "[DOC] reminders for unfilled README.md"),
    ("watch_nag",          "Watch nag            ", "[WATCH] reminders for unfilled watch.py"),
    ("auto_create_readme", "Auto-create README   ", "Drop README.md template into every folder"),
    ("auto_create_watch",  "Auto-create watch.py ", "Drop watch.py template into every folder"),
    ("escape_hatch",       "Escape hatch         ", "Let Claude/Codex run `mod end-attempt` to bypass retries"),
]


def _safe(win, y, x, s, attr=0):
    h, w = win.getmaxyx()
    if y >= h or x >= w:
        return
    try:
        win.addstr(y, x, s[:w - x], attr)
    except curses.error:
        pass


def _current_target_dir():
    try:
        return Path(_CURRENT_TARGET_PATH.read_text().strip()).resolve()
    except OSError:
        return None


def _load_delta_note_project():
    try:
        raw = json.loads(_DELTA_NOTE_PROJECT_PATH.read_text())
    except (OSError, ValueError):
        return None
    target_dir = raw.get("target_dir")
    if not isinstance(target_dir, str) or not target_dir.strip():
        return None
    try:
        raw["_resolved_target_dir"] = Path(target_dir).resolve()
    except OSError:
        return None
    return raw


def _delta_note_settings():
    project = _load_delta_note_project()
    current_target = _current_target_dir()
    if not project or current_target is None:
        return None
    if os.path.realpath(str(project["_resolved_target_dir"])) != os.path.realpath(str(current_target)):
        return None

    mcp_cfg = project.setdefault("mcp_devtools", {})
    enabled = bool(mcp_cfg.get("enabled", False))
    port = mcp_cfg.get("remote_debug_port", 9222)
    if not isinstance(port, int) or port <= 0:
        port = 9222
    return {
        "project": project,
        "project_path": _DELTA_NOTE_PROJECT_PATH,
        "enabled": enabled,
        "port": port,
    }


def _save_delta_note_project(project):
    project = dict(project)
    project.pop("_resolved_target_dir", None)
    _DELTA_NOTE_PROJECT_PATH.write_text(json.dumps(project, indent=2) + "\n")


def _autostart_entries():
    apps = cli_autostart.normalized_autostart_apps()
    entries = []
    current_target = _current_target_dir()
    delta_note_root = Path(cli_autostart.DELTA_NOTE_SCRIPT).parents[1]
    if current_target and os.path.realpath(str(current_target)) == os.path.realpath(str(delta_note_root)):
        delta = apps.get(cli_autostart.AUTOSTART_KEY) or cli_autostart.default_mod_autostart_entry(enabled=False)
        entries.append(delta)
    for key in sorted(apps):
        if key == cli_autostart.AUTOSTART_KEY:
            continue
        entries.append(apps[key])
    return entries


def _prompt_int(stdscr, label, current, allow_zero=False):
    h, w = stdscr.getmaxyx()
    prompt = f"{label} (current: {current}): "
    y = h - 1
    _safe(stdscr, y, 0, " " * (w - 1))
    _safe(stdscr, y, 0, prompt, curses.A_BOLD)
    curses.echo()
    curses.curs_set(1)
    try:
        raw = stdscr.getstr(y, len(prompt), 10).decode("utf-8", "replace")
    except Exception:
        raw = ""
    curses.noecho()
    curses.curs_set(0)
    raw = raw.strip()
    if not raw:
        return None
    # The agent budget treats 0 as "none allowed", which is a real setting, not
    # an empty one; the LOC limits have no such reading and still reject it.
    if not raw.isdigit() or (int(raw) <= 0 and not allow_zero):
        return False
    return int(raw)


def _draw_row(stdscr, y, mark_on, label, desc, selected):
    attr = curses.A_REVERSE if selected else 0
    color = curses.color_pair(_C_GREEN) if mark_on else curses.color_pair(_C_RED)
    mark = "[X]" if mark_on else "[ ]"
    _safe(stdscr, y, 2, mark, color | attr | curses.A_BOLD)
    _safe(stdscr, y, 6, label, attr | curses.A_BOLD)
    _safe(stdscr, y, 28, desc, attr | curses.A_DIM)


def draw_settings_view(stdscr, cfg, cursor, flash_msg=""):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    delta_note = _delta_note_settings()
    autostart_entries = _autostart_entries()

    banner = "  ◈ Stopgate Settings"
    _safe(stdscr, 0, 0, banner.ljust(w)[:w],
          curses.color_pair(_C_HEADER) | curses.A_BOLD)
    _safe(stdscr, 1, 0, "  Gate blockers + agent-facing output toggles.", curses.A_DIM)

    y = 3
    _safe(stdscr, y, 2, "— Gate blockers —", curses.color_pair(_C_HEADER) | curses.A_BOLD)
    y += 1
    for idx, (key, label, desc) in enumerate(_FEATURE_LABELS):
        _draw_row(stdscr, y, cfg["enabled"].get(key, True), label, desc, idx == cursor)
        y += 1

    y += 1
    loc_idx = len(_FEATURE_LABELS)
    attr = curses.A_REVERSE if cursor == loc_idx else 0
    _safe(stdscr, y, 2, f"Max LOC per file: {cfg['loc_limit']}", attr | curses.A_BOLD)
    _safe(stdscr, y, 40, "(Enter to edit — hard block)", attr | curses.A_DIM)
    y += 1
    notify_idx = loc_idx + 1
    attr = curses.A_REVERSE if cursor == notify_idx else 0
    _safe(stdscr, y, 2, f"Notify LOC threshold: {cfg['notify_loc']}", attr | curses.A_BOLD)
    _safe(stdscr, y, 40, "(Enter to edit — soft [!] warn)", attr | curses.A_DIM)
    y += 1
    agents_idx = notify_idx + 1
    attr = curses.A_REVERSE if cursor == agents_idx else 0
    _safe(stdscr, y, 2, f"Max agents per session: {cfg['max_agents']}", attr | curses.A_BOLD)
    _safe(stdscr, y, 40, "(Enter to edit — 0 blocks all subagents)", attr | curses.A_DIM)
    y += 2

    _safe(stdscr, y, 2, "— Agent-facing features —", curses.color_pair(_C_HEADER) | curses.A_BOLD)
    y += 1
    claude = cfg.get("claude", {})
    base = len(_FEATURE_LABELS) + 3
    for idx, (key, label, desc) in enumerate(_CLAUDE_LABELS):
        _draw_row(stdscr, y, claude.get(key, True), label, desc, (base + idx) == cursor)
        y += 1

    if delta_note:
        y += 2
        delta_idx = base + len(_CLAUDE_LABELS)
        _safe(stdscr, y, 2, "— Delta Note MCP —", curses.color_pair(_C_HEADER) | curses.A_BOLD)
        y += 1
        _draw_row(
            stdscr,
            y,
            delta_note["enabled"],
            "Auto MCP debug       ",
            f"Open Electron remote debug port {delta_note['port']} on launch",
            delta_idx == cursor,
        )
        y += 1

    y += 2
    autostart_base = base + len(_CLAUDE_LABELS) + (1 if delta_note else 0)
    _safe(stdscr, y, 2, "— Autostart apps —", curses.color_pair(_C_HEADER) | curses.A_BOLD)
    y += 1
    for idx, entry in enumerate(autostart_entries):
        label = (entry.get("label") or entry["key"])[:20].ljust(20)
        desc_path = entry.get("path") or entry.get("start_cmd") or "(none)"
        _draw_row(stdscr, y, entry.get("enabled", False), label, desc_path, (autostart_base + idx) == cursor)
        y += 1
    add_idx = autostart_base + len(autostart_entries)
    attr = curses.A_REVERSE if cursor == add_idx else 0
    _safe(stdscr, y, 2, "Add autostart path", attr | curses.A_BOLD)
    _safe(stdscr, y, 24, "Prompt for script path and store as new entry", attr | curses.A_DIM)

    footer = "  ↑↓ move   Space/Enter toggle   Enter on LOC/agents edits   e/q/Esc close"
    _safe(stdscr, h - 2, 0, footer.ljust(w)[:w], curses.A_DIM)
    if flash_msg:
        _safe(stdscr, h - 1, 0, flash_msg[:w - 1], curses.color_pair(_C_HEADER) | curses.A_BOLD)

    stdscr.refresh()


def run_settings_loop(stdscr):
    cfg = stopgate_config.load()
    delta_note = _delta_note_settings()
    autostart_entries = _autostart_entries()
    cursor = 0
    gate_n = len(_FEATURE_LABELS)
    loc_idx = gate_n
    notify_idx = gate_n + 1
    agents_idx = gate_n + 2
    claude_base = gate_n + 3
    delta_idx = claude_base + len(_CLAUDE_LABELS)
    autostart_base = delta_idx + (1 if delta_note else 0)
    add_idx = autostart_base + len(autostart_entries)
    total = add_idx + 1
    flash = ""
    while True:
        draw_settings_view(stdscr, cfg, cursor, flash)
        flash = ""
        key = stdscr.getch()
        if key == -1:
            continue
        if key in (ord('q'), ord('Q'), ord('e'), ord('E'), 27, 3):
            return
        if key in (curses.KEY_UP, ord('k')):
            cursor = (cursor - 1) % total
        elif key in (curses.KEY_DOWN, ord('j')):
            cursor = (cursor + 1) % total
        elif key in (ord(' '), 10, 13, curses.KEY_ENTER):
            if cursor < gate_n:
                feat = _FEATURE_LABELS[cursor][0]
                cfg["enabled"][feat] = not cfg["enabled"].get(feat, True)
                stopgate_config.save(cfg)
                flash = f"{feat} → {'enabled' if cfg['enabled'][feat] else 'disabled'}"
            elif cursor >= claude_base:
                if delta_note and cursor == delta_idx:
                    project = delta_note["project"]
                    mcp_cfg = project.setdefault("mcp_devtools", {})
                    next_enabled = not delta_note["enabled"]
                    mcp_cfg["enabled"] = next_enabled
                    if not isinstance(mcp_cfg.get("remote_debug_port"), int) or mcp_cfg["remote_debug_port"] <= 0:
                        mcp_cfg["remote_debug_port"] = 9222
                    _save_delta_note_project(project)
                    delta_note = _delta_note_settings()
                    autostart_base = delta_idx + (1 if delta_note else 0)
                    add_idx = autostart_base + len(autostart_entries)
                    total = add_idx + 1
                    state = "enabled" if next_enabled else "disabled"
                    # The toggle now enables DELTA_NOTE_DEBUG_CDP=1 (in-process
                    # relay on port 9223) instead of --remote-debugging-port,
                    # which broke the renderer on Electron 37. See
                    # delta-note-relay-mcp/README.md.
                    flash = f"delta-note mcp → {state} (relay on port 9223)"
                elif autostart_base <= cursor < add_idx:
                    entry = autostart_entries[cursor - autostart_base]
                    apps = cli_autostart.normalized_autostart_apps()
                    key_name = entry["key"]
                    target = apps.get(key_name, entry)
                    next_enabled = not target.get("enabled", False)
                    target["enabled"] = next_enabled
                    apps[key_name] = target
                    cli_autostart.persist_normalized_autostart_apps(apps)
                    autostart_entries = _autostart_entries()
                    add_idx = autostart_base + len(autostart_entries)
                    total = add_idx + 1
                    state = "enabled" if next_enabled else "disabled"
                    flash = f"autostart.{key_name} → {state}"
                elif cursor == add_idx:
                    raw = prompt_input(stdscr, "Autostart script path: ")
                    if raw is None or not raw.strip():
                        flash = ""
                    else:
                        try:
                            apps = cli_autostart.normalized_autostart_apps()
                            entry = cli_autostart.add_script_autostart(apps, raw.strip(), enabled=True)
                            cli_autostart.persist_normalized_autostart_apps(apps)
                            autostart_entries = _autostart_entries()
                            add_idx = autostart_base + len(autostart_entries)
                            total = add_idx + 1
                            flash = f"autostart added → {entry['key']}"
                        except FileNotFoundError:
                            flash = "Script not found"
                else:
                    feat = _CLAUDE_LABELS[cursor - claude_base][0]
                    claude = cfg.setdefault("claude", {})
                    claude[feat] = not claude.get(feat, True)
                    stopgate_config.save(cfg)
                    flash = f"claude.{feat} → {'enabled' if claude[feat] else 'disabled'}"
            elif cursor == agents_idx:
                val = _prompt_int(stdscr, "Max agents", cfg["max_agents"], allow_zero=True)
                if val is None:
                    flash = ""
                elif val is False:
                    flash = "Invalid — enter 0 or a positive integer"
                else:
                    cfg["max_agents"] = val
                    stopgate_config.save(cfg)
                    flash = (f"Max agents → {val}" if val
                             else "Max agents → 0 (all subagents blocked)")
            elif cursor == notify_idx:
                val = _prompt_int(stdscr, "Notify LOC", cfg["notify_loc"])
                if val is None:
                    flash = ""
                elif val is False:
                    flash = "Invalid — enter a positive integer"
                else:
                    cfg["notify_loc"] = val
                    stopgate_config.save(cfg)
                    flash = f"Notify LOC → {val}"
            else:
                val = _prompt_int(stdscr, "Max LOC", cfg["loc_limit"])
                if val is None:
                    flash = ""
                elif val is False:
                    flash = "Invalid — enter a positive integer"
                else:
                    cfg["loc_limit"] = val
                    stopgate_config.save(cfg)
                    flash = f"Max LOC → {val}"
