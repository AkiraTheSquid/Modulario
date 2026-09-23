#!/usr/bin/env python3
"""Provider-aware Modulario PostToolUse hook implementation."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from cli_common import THRESHOLDS, state_path_for
from hook_scope import collect_changes, payload_provider, payload_session_id
from project_registry import load_projects, record_project_use


MODULARIO_DIR = Path(__file__).resolve().parent.parent
ANALYZER = MODULARIO_DIR / "scripts" / "modulario-analyze.py"
WATCH_RUNNER = MODULARIO_DIR / "scripts" / "modulario-watch-runner.py"
ALERT_SOUND = MODULARIO_DIR / "assets" / "modulario-alert.wav"
REDUCTION_SOUND = MODULARIO_DIR / "assets" / "modulario-reduction-chime.wav"
FALLBACK_SOUND = Path("/usr/share/sounds/Yaru/stereo/dialog-error.oga")


def _hook_env():
    env = os.environ.copy()
    env["MODULARIO_SKIP"] = "1"
    return env


def _run_project(target, changed_files, session_id, provider):
    state_path = state_path_for(target)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    primary = changed_files[-1]
    analyzer = subprocess.run(
        [
            sys.executable, str(ANALYZER),
            "--target", target,
            "--output", str(state_path),
            "--thresholds", str(THRESHOLDS),
            "--changed-file", primary,
            "--changed-files-json", json.dumps(changed_files),
            "--session-id", session_id,
            "--provider", provider,
            "--print-summary",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=_hook_env(),
    )
    if analyzer.returncode != 0:
        detail = (analyzer.stderr or analyzer.stdout or "unknown error").strip()
        return f"[Modulario] analyzer failed for {target}: {detail}", state_path, primary

    watcher = subprocess.run(
        [
            sys.executable, str(WATCH_RUNNER),
            "--target", target,
            "--changed-file", primary,
        ],
        input=analyzer.stdout,
        capture_output=True,
        text=True,
        timeout=180,
        env=_hook_env(),
    )
    raw = (watcher.stdout or analyzer.stdout or "").strip()
    try:
        data = json.loads(raw) if raw else {}
        text = data.get("hookSpecificOutput", {}).get("additionalContext", "") or ""
    except (TypeError, ValueError):
        text = raw
    return text.strip(), state_path, primary


def _sound_kind(state_path, changed_file):
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        thresholds = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "alert"
    limit = (thresholds.get("loc_bands") or [0])[-1]
    for item in state.get("files", []):
        if item.get("abs_path") == changed_file:
            if (item.get("loc_delta") or 0) < 0 and (item.get("loc") or 0) > limit:
                return "reduction"
            break
    return "alert"


def _play_sound(kind):
    candidates = []
    if kind == "reduction":
        candidates.append(REDUCTION_SOUND)
    candidates.extend((ALERT_SOUND, FALLBACK_SOUND))
    player = shutil.which("pw-play") or shutil.which("paplay")
    if not player:
        return
    sound = next((path for path in candidates if path.is_file()), None)
    if sound:
        subprocess.Popen(
            [player, str(sound)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_hook_env(),
        )


def _notify(text, kind):
    alerts = [line.strip() for line in text.splitlines() if line.strip().startswith("[ALERT]")]
    if not alerts:
        return
    notify = shutil.which("notify-send")
    if notify:
        env = _hook_env()
        env.setdefault("DISPLAY", ":0")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
        subprocess.run(
            [notify, "-u", "critical", "-t", "15000", "-i", "dialog-warning",
             "Modulario threshold alert", "\n".join(alerts)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    _play_sound(kind)


def main():
    if os.environ.get("MODULARIO_SKIP") == "1":
        return
    if (MODULARIO_DIR / "data" / "hooks-paused").exists():
        return
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    projects = load_projects()
    changes = collect_changes(payload, projects)
    if not changes:
        return

    provider = payload_provider(payload)
    session_id = payload_session_id(payload)
    contexts = []
    last_state = None
    last_changed = ""
    for target, changed_files in changes.items():
        text, state_path, primary = _run_project(target, changed_files, session_id, provider)
        record_project_use(target)
        last_state, last_changed = state_path, primary
        if text:
            if len(changes) > 1:
                text = f"[Modulario] project: {target}\n{text}"
            contexts.append(text)

    if not contexts:
        return
    merged = "\n\n".join(contexts)
    if last_state:
        _notify(merged, _sound_kind(last_state, last_changed))
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": merged,
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        if os.environ.get("MODULARIO_DEBUG") == "1":
            print(f"[Modulario] post-hook error: {exc}", file=sys.stderr)
