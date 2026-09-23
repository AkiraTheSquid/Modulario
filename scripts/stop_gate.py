#!/usr/bin/env python3
"""Session/touch-scoped Modulario Stop gate for Claude Code + Codex."""
import json
import os
import subprocess
import sys
from pathlib import Path

if os.environ.get("MODULARIO_SKIP") == "1":
    sys.exit(0)

SCRIPT_DIR = Path(__file__).resolve().parent
MODULARIO_DIR = SCRIPT_DIR.parent
if (MODULARIO_DIR / "data" / "hooks-paused").exists():
    sys.exit(0)
STATE_DIR = MODULARIO_DIR / "data" / "state"
TMP_DIR = MODULARIO_DIR / "tmp"
WATCH_RUNNER = SCRIPT_DIR / "modulario-watch-runner.py"
MAX_FIX_ATTEMPTS = 3
DOC_MARKER = "<!-- modulario:template -->"

sys.path.insert(0, str(SCRIPT_DIR))
from counters import count_loc_css, count_loc_js, count_loc_python  # noqa: E402
from hook_scope import payload_provider, payload_session_id  # noqa: E402
from project_registry import load_projects  # noqa: E402
from session_scope import load_target_session  # noqa: E402
import stopgate_config  # noqa: E402


_JS_EXTS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
_CSS_EXTS = {".css", ".scss", ".sass", ".less"}


def live_loc(abs_path):
    try:
        content = Path(abs_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    ext = Path(abs_path).suffix.lower()
    if ext == ".py":
        return count_loc_python(content)
    if ext in _JS_EXTS:
        return count_loc_js(content)
    if ext in _CSS_EXTS:
        return count_loc_css(content)
    return None


def session_state_path(provider, session_id):
    raw = f"{provider}_{session_id}"
    safe = "".join(char for char in raw if char.isalnum() or char in "-_") or "default"
    return TMP_DIR / f"stop_retries_{safe}.json"


def load_retry_state(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"count": 0, "final_shown": False}


def save_retry_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def clear_retry_state(path):
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _run_watches(target, scope_folders):
    try:
        proc = subprocess.run(
            [
                sys.executable, str(WATCH_RUNNER),
                "--target", target,
                "--changed-file", "",
                "--print-plain",
                "--scope-folders-json", json.dumps(sorted(scope_folders)),
            ],
            capture_output=True,
            text=True,
            timeout=180,
            env={**os.environ, "MODULARIO_SKIP": "1"},
        )
        return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()
    except Exception as exc:
        return 1, f"watch runner error: {exc}"


def _scoped_cycles(state, scope_files):
    cycles = state.get("violations", {}).get("cycles", []) or []
    result = []
    for cycle in cycles:
        files = cycle.get("files", []) if isinstance(cycle, dict) else cycle
        if set(files or []) & scope_files:
            result.append(cycle)
    return result


def _scoped_oversized(target_scope, loc_limit):
    result = []
    target = target_scope["target"]
    scope_files = target_scope["scope_files"]
    for item in target_scope["state"].get("files", []) or []:
        rel = item.get("path") or ""
        if rel not in scope_files or (item.get("loc") or 0) <= loc_limit:
            continue
        abs_path = item.get("abs_path") or os.path.join(target, rel)
        current = live_loc(abs_path)
        if current is not None and current > loc_limit:
            result.append((abs_path, current))
    return sorted(result, key=lambda pair: -pair[1])


def _unfilled_touched_docs(target_scope):
    target = target_scope["target"]
    result = []
    for folder in target_scope["touched_folders"]:
        folder_abs = os.path.join(target, folder)
        if os.path.exists(os.path.join(folder_abs, ".doc.dismissed")):
            continue
        readme = os.path.join(folder_abs, "README.md")
        try:
            first_line = Path(readme).read_text(encoding="utf-8").splitlines()[0]
        except (OSError, IndexError):
            first_line = ""
        if not first_line or DOC_MARKER in first_line:
            result.append(readme)
    return result


def _project_failures(target_scope, enabled, loc_limit, scope_filter):
    if scope_filter:
        scope_files = target_scope["scope_files"]
        scope_folders = target_scope["scope_folders"]
    else:
        scope_files = {item.get("path") for item in target_scope["state"].get("files", [])}
        scope_folders = None

    if enabled.get("watches", True):
        watch_exit, watch_out = _run_watches(
            target_scope["target"],
            scope_folders if scope_folders is not None else set(),
        ) if scope_folders is not None else _run_watches_unscoped(target_scope["target"])
    else:
        watch_exit, watch_out = 0, ""

    scoped = dict(target_scope)
    scoped["scope_files"] = scope_files
    return {
        "target": target_scope["target"],
        "watch_exit": watch_exit,
        "watch_out": watch_out,
        "cycles": _scoped_cycles(target_scope["state"], scope_files)
        if enabled.get("cycles", True) else [],
        "docs": _unfilled_touched_docs(target_scope)
        if enabled.get("unfilled_docs", True) else [],
        "oversized": _scoped_oversized(scoped, loc_limit)
        if enabled.get("oversized_files", True) else [],
    }


def _run_watches_unscoped(target):
    try:
        proc = subprocess.run(
            [sys.executable, str(WATCH_RUNNER), "--target", target, "--print-plain"],
            capture_output=True,
            text=True,
            timeout=180,
            env={**os.environ, "MODULARIO_SKIP": "1"},
        )
        return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()
    except Exception as exc:
        return 1, f"watch runner error: {exc}"


def _format_failures(failures, loc_limit):
    parts = []
    multi = len(failures) > 1
    for failure in failures:
        project_parts = []
        if failure["watch_exit"]:
            project_parts.append("[Modulario] scoped watch FAILED:\n" + failure["watch_out"])
        if failure["cycles"]:
            lines = ["[Modulario] Circular imports intersecting touched graph scope:"]
            for cycle in failure["cycles"]:
                files = cycle.get("files", []) if isinstance(cycle, dict) else cycle
                lines.append("  " + " -> ".join(files))
            project_parts.append("\n".join(lines))
        if failure["docs"]:
            lines = ["[Modulario] Touched folders with unfilled README.md:"]
            lines.extend(f"  {path}" for path in failure["docs"])
            lines.append("\nFill docs with intent, rationale, invariants, constraints, integration, gotchas. Remove template marker from line 1.")
            project_parts.append("\n".join(lines))
        if failure["oversized"]:
            lines = [f"[Modulario] Files exceeding {loc_limit} LOC inside touched graph scope:"]
            lines.extend(f"  {path}  ({loc} LOC)" for path, loc in failure["oversized"])
            lines.append(f"\nSplit listed files below {loc_limit} LOC. Keep public API stable. Use `mod graph <file> --target {failure['target']}` before moving code.")
            project_parts.append("\n".join(lines))
        if project_parts:
            prefix = f"Project: {failure['target']}\n" if multi else ""
            parts.append(prefix + "\n\n".join(project_parts))
    return "\n\n".join(parts)


def _fix_line(failures, loc_limit):
    tasks = []
    if any(f["watch_exit"] for f in failures):
        tasks.append("fix scoped failing watch(es)")
    if any(f["cycles"] for f in failures):
        tasks.append("break scoped circular import(s)")
    if any(f["docs"] for f in failures):
        tasks.append("fill touched-folder README.md files")
    if any(f["oversized"] for f in failures):
        tasks.append(f"split scoped files over {loc_limit} LOC")
    return "; ".join(tasks) or "resolve scoped issues"


def block(reason):
    print(json.dumps({"decision": "block", "reason": reason}))


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    provider = payload_provider(payload)
    session_id = payload_session_id(payload)
    retry_path = session_state_path(provider, session_id)
    retry_state = load_retry_state(retry_path)
    if retry_state.get("final_shown"):
        return

    cfg = stopgate_config.load()
    claude_cfg = cfg.get("claude", {})
    if not claude_cfg.get("gate_master", True):
        return

    target_scopes = []
    for project in load_projects():
        scope = load_target_session(
            STATE_DIR, project["target_dir"], session_id, provider
        )
        if scope:
            target_scopes.append(scope)
    if not target_scopes:
        clear_retry_state(retry_path)
        return

    failures = [
        _project_failures(
            scope,
            cfg["enabled"],
            cfg["loc_limit"],
            claude_cfg.get("scope_filter", True),
        )
        for scope in target_scopes
    ]
    failures = [
        failure for failure in failures
        if failure["watch_exit"] or failure["cycles"] or failure["docs"] or failure["oversized"]
    ]
    if not failures:
        clear_retry_state(retry_path)
        return

    retry_state["count"] = int(retry_state.get("count", 0)) + 1
    detail = _format_failures(failures, cfg["loc_limit"])
    if retry_state["count"] <= MAX_FIX_ATTEMPTS:
        save_retry_state(retry_path, retry_state)
        escape = ""
        if claude_cfg.get("escape_hatch", True):
            escape = (
                "\n\nEscape hatch: only for genuinely out-of-scope failures. Explain why, "
                "run `mod end-attempt \"<one-sentence reason>\"`, then end turn."
            )
        block(
            f"Stop blocked by Modulario (fix attempt {retry_state['count']}/{MAX_FIX_ATTEMPTS}).\n\n"
            f"{detail}\n\nNext: {_fix_line(failures, cfg['loc_limit'])}, then try to stop again.{escape}"
        )
        return


    retry_state["final_shown"] = True
    save_retry_state(retry_path, retry_state)
    block(
        f"Modulario: {MAX_FIX_ATTEMPTS} scoped fix attempts exhausted. Stop fixing.\n\n"
        "Summarize work; report remaining scoped failures; judge real bug vs false positive.\n\n"
        f"{detail}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        if os.environ.get("MODULARIO_DEBUG") == "1":
            print(f"[Modulario] stop-hook error: {exc}", file=sys.stderr)
