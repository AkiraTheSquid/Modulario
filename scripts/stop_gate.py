#!/usr/bin/env python3
"""Modulario Stop hook gate.

Runs `mod watch run` and checks state.json for circular imports whenever
Claude tries to end its turn. Blocks stop on failures up to MAX_FIX_ATTEMPTS
times per session, then releases with a final summarize-and-report instruction.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from counters import count_loc_python, count_loc_js, count_loc_css
import stopgate_config

MODULARIO_DIR = Path(__file__).resolve().parent.parent

_JS_EXTS = {'.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs'}
_CSS_EXTS = {'.css', '.scss', '.sass', '.less'}
_PY_EXTS = {'.py'}


def live_loc(abs_path):
    """Recount LOC from disk. Returns None if the file is missing/unreadable
    or the extension isn't one we know how to count."""
    try:
        with open(abs_path, 'r', encoding='utf-8', errors='replace') as fh:
            content = fh.read()
    except OSError:
        return None
    ext = os.path.splitext(abs_path)[1].lower()
    if ext in _PY_EXTS:
        return count_loc_python(content)
    if ext in _JS_EXTS:
        return count_loc_js(content)
    if ext in _CSS_EXTS:
        return count_loc_css(content)
    return None
CURRENT_TARGET = MODULARIO_DIR / "configs" / "current-target.txt"
TMP_DIR = MODULARIO_DIR / "tmp"
MOD_BIN = MODULARIO_DIR / "bin" / "modulario"
MAX_FIX_ATTEMPTS = 3
DOC_MARKER = "<!-- modulario:template -->"


def load_payload():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def session_state_path(session_id):
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_") or "default"
    return TMP_DIR / f"stop_retries_{safe}.json"


def load_state(path):
    if not path.exists():
        return {"count": 0, "final_shown": False}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"count": 0, "final_shown": False}


def save_state(path, state):
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))


def clear_state(path):
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def target_state_file(target):
    h = hashlib.md5(os.path.realpath(target).encode()).hexdigest()[:12]
    return MODULARIO_DIR / "data" / "state" / f"{h}.json"


STATE_DIR = MODULARIO_DIR / "data" / "state"


def iter_tracked_targets(active_target):
    """Yield (target_dir, touched_folders_list) for state entries whose
    target_dir is the active target or a subdirectory of it. Skips entries
    whose sibling state.json is missing or whose target_dir no longer exists."""
    if not STATE_DIR.exists() or not active_target:
        return
    active_real = os.path.realpath(active_target)
    for touched_path in sorted(STATE_DIR.glob("*_touched.json")):
        state_path = touched_path.with_name(touched_path.name.replace("_touched.json", ".json"))
        if not state_path.exists():
            continue
        try:
            state = json.loads(state_path.read_text())
            target = state.get("target_dir") or ""
        except Exception:
            continue
        if not target or not os.path.isdir(target):
            continue
        target_real = os.path.realpath(target)
        if target_real != active_real and not target_real.startswith(active_real + os.sep):
            continue
        try:
            folders = json.loads(touched_path.read_text()).get("folders", []) or []
        except Exception:
            folders = []
        yield target, folders


def unfilled_touched_docs_all(active_target):
    """Scan the active target, return list of (target, folder) pairs where
    the touched folder's README.md is missing or still a template."""
    result = []
    for target, folders in iter_tracked_targets(active_target):
        for folder in folders:
            folder_abs = os.path.join(target, folder)
            if os.path.exists(os.path.join(folder_abs, ".doc.dismissed")):
                continue
            readme = os.path.join(folder_abs, "README.md")
            if not os.path.exists(readme):
                result.append((target, folder))
                continue
            try:
                with open(readme, "r", encoding="utf-8") as fh:
                    if DOC_MARKER in fh.readline():
                        result.append((target, folder))
            except OSError:
                pass
    return result


def oversized_files_all(active_target, loc_limit):
    """Scan state.json files for the active target (or its subdirs) only."""
    result = []
    if not STATE_DIR.exists() or not active_target:
        return result
    active_real = os.path.realpath(active_target)
    for state_path in sorted(STATE_DIR.glob("*.json")):
        name = state_path.name
        if name.endswith("_touched.json") or name.endswith("_coupling.json"):
            continue
        try:
            data = json.loads(state_path.read_text())
        except Exception:
            continue
        target = data.get("target_dir") or ""
        if not target or not os.path.isdir(target):
            continue
        target_real = os.path.realpath(target)
        if target_real != active_real and not target_real.startswith(active_real + os.sep):
            continue
        for f in data.get("files", []) or []:
            cached_loc = f.get("loc", 0)
            if cached_loc <= loc_limit:
                continue
            abs_path = f.get("abs_path") or os.path.join(target, f.get("path", ""))
            if not os.path.isfile(abs_path):
                continue
            current = live_loc(abs_path)
            if current is None or current <= loc_limit:
                continue
            result.append((abs_path, current))
    result.sort(key=lambda x: -x[1])
    return result


def get_cycles(target):
    sf = target_state_file(target)
    if not sf.exists():
        return []
    try:
        data = json.loads(sf.read_text())
        return data.get("violations", {}).get("cycles", []) or []
    except Exception:
        return []


def run_watches():
    try:
        proc = subprocess.run(
            [str(MOD_BIN), "watch", "run"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except Exception as e:
        return 1, f"watch runner error: {e}"


def format_cycles(cycles):
    if not cycles:
        return ""
    lines = ["Circular imports detected:"]
    for c in cycles:
        files = c.get("files", []) if isinstance(c, dict) else c
        lines.append("  " + " -> ".join(files))
    return "\n".join(lines)


def block(reason):
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def allow():
    sys.exit(0)


def main():
    payload = load_payload()
    session_id = payload.get("session_id", "default")

    sp = session_state_path(session_id)
    state = load_state(sp)

    if state.get("final_shown"):
        allow()

    # Watches + cycles still read from the active target (that's what
    # `mod watch run` scopes to anyway). Doc checks scan every tracked target.
    active_target = ""
    if CURRENT_TARGET.exists():
        candidate = CURRENT_TARGET.read_text().strip()
        if candidate and os.path.isdir(candidate):
            active_target = candidate

    cfg = stopgate_config.load()
    enabled = cfg["enabled"]
    claude_cfg = cfg.get("claude", {})
    loc_limit = cfg["loc_limit"]

    if not claude_cfg.get("gate_master", True):
        allow()

    if enabled.get("watches", True):
        watch_exit, watch_out = run_watches()
    else:
        watch_exit, watch_out = 0, ""
    cycles = get_cycles(active_target) if (active_target and enabled.get("cycles", True)) else []
    unfilled_docs = unfilled_touched_docs_all(active_target) if enabled.get("unfilled_docs", True) else []
    oversized = oversized_files_all(active_target, loc_limit) if enabled.get("oversized_files", True) else []
    failing = watch_exit != 0 or bool(cycles) or bool(unfilled_docs) or bool(oversized)

    if not failing:
        clear_state(sp)
        allow()

    state["count"] = int(state.get("count", 0)) + 1

    parts = []
    if watch_exit != 0:
        parts.append("[Modulario] `mod watch run` FAILED:\n" + watch_out.strip())
    if cycles:
        parts.append("[Modulario] " + format_cycles(cycles))
    if unfilled_docs:
        doc_lines = ["[Modulario] Touched folders with unfilled README.md:"]
        for target_dir, folder in unfilled_docs:
            doc_lines.append(f"  {os.path.join(target_dir, folder, 'README.md')}")
        doc_lines.append(
            "\nFill these in with information a reader would NOT be able to glean "
            "from reading the code itself — intent, rationale, invariants, non-obvious "
            "constraints, how this folder fits into the larger system, gotchas. "
            "Do NOT just restate what the code does. Remove the template marker "
            "(`<!-- modulario:template -->`) from line 1 when done."
        )
        parts.append("\n".join(doc_lines))
    if oversized:
        loc_lines = [f"[Modulario] Files exceeding {loc_limit} LOC:"]
        for path, loc in oversized:
            loc_lines.append(f"  {path}  ({loc} LOC)")
        loc_lines.append(
            f"\nSplit these files so each is under {loc_limit} LOC. Extract cohesive "
            "chunks into sibling modules; keep public API stable. Use `mod graph <file>` "
            "to see importers before moving code."
        )
        parts.append("\n".join(loc_lines))
    detail = "\n\n".join(parts) or "Modulario detected a problem."

    if state["count"] <= MAX_FIX_ATTEMPTS:
        save_state(sp, state)
        fix_instructions = []
        if watch_exit != 0:
            fix_instructions.append("fix the failing watch(es)")
        if cycles:
            fix_instructions.append("break the circular import(s)")
        if unfilled_docs:
            fix_instructions.append("fill in the README.md for each touched folder listed above")
        if oversized:
            fix_instructions.append(f"split files over {loc_limit} LOC listed above")
        fix_line = "; ".join(fix_instructions) if fix_instructions else "resolve the issues above"
        escape_line = ""
        if claude_cfg.get("escape_hatch", True):
            escape_line = (
                "\n\nEscape hatch: if the failures above are OUTSIDE the scope of what "
                "the user asked you to do (e.g. a pre-existing broken watch, an unfilled "
                "README for a folder the user never touched, an oversized file you never "
                "edited), do NOT spend retries fixing unrelated work. Instead:\n"
                "  1. In your final message, explain WHY the failures are out of scope and "
                "what you would have had to change to fix them.\n"
                "  2. Then run:  mod end-attempt \"<one-sentence reason>\"\n"
                "  3. Then end your turn normally. The next Stop will allow immediately.\n"
                "Only use the escape hatch when the failures genuinely don't belong to your "
                "current task — not to skip fixing real bugs you introduced."
            )
        reason = (
            f"Stop blocked by Modulario (fix attempt {state['count']}/{MAX_FIX_ATTEMPTS}).\n\n"
            f"{detail}\n\n"
            f"Next: {fix_line}, then try to stop again.{escape_line}"
        )
        block(reason)
    else:
        state["final_shown"] = True
        save_state(sp, state)
        reason = (
            f"Modulario: {MAX_FIX_ATTEMPTS} fix attempts exhausted. STOP TRYING TO FIX.\n\n"
            "Do all of the following, then end your turn:\n"
            "  1. Summarize what you did this session as usual.\n"
            "  2. Tell the user which Modulario checks are still failing.\n"
            "  3. Paste the failing output verbatim:\n\n"
            f"{detail}\n\n"
            "  4. For any watch / cycle failures, give your judgment: real bug vs. false positive, with reasoning.\n"
            "  5. For any unfilled docs, say why you couldn't complete them.\n\n"
            "After this turn, further stop attempts in this session will be allowed automatically."
        )
        block(reason)


if __name__ == "__main__":
    main()
