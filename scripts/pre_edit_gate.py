#!/usr/bin/env python3
"""Provider-aware PreToolUse snapshot + hard LOC edit gate."""
import json
import os
import sys
from pathlib import Path

if os.environ.get("MODULARIO_SKIP") == "1":
    sys.exit(0)

SCRIPT_DIR = Path(__file__).resolve().parent
if (SCRIPT_DIR.parent / "data" / "hooks-paused").exists():
    sys.exit(0)
sys.path.insert(0, str(SCRIPT_DIR))

from cli_common import state_path_for  # noqa: E402
from counters import count_loc_css, count_loc_js, count_loc_python  # noqa: E402
from hook_scope import capture_before, extract_direct_paths, payload_tool_name  # noqa: E402
from project_registry import find_project_for_path, load_projects  # noqa: E402
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


def _cached_file(state_path, changed_file):
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for item in data.get("files", []) or []:
        if item.get("abs_path") == changed_file:
            return item
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    projects = load_projects()
    capture_before(payload, projects)

    if payload_tool_name(payload) not in {"Write", "Edit", "NotebookEdit", "apply_patch"}:
        return

    loc_limit = stopgate_config.load()["loc_limit"]
    blocked = []
    for changed_file in extract_direct_paths(payload):
        project = find_project_for_path(changed_file, projects)
        if not project:
            continue
        cached = _cached_file(state_path_for(project["target_dir"]), changed_file)
        if not cached or (cached.get("loc") or 0) <= loc_limit:
            continue
        current = live_loc(changed_file)
        if current is None or current <= loc_limit:
            continue
        rel = os.path.relpath(changed_file, project["target_dir"])
        blocked.append((rel, current, cached.get("status", "?")))

    if not blocked:
        return
    lines = ["Modulario blocked this edit before it starts.", ""]
    for rel, current, status in blocked:
        lines.append(f"`{rel}` exceeds hard LOC limit: {current} > {loc_limit} (status: {status}).")
    lines.extend(("", "Edit a sibling module or split file first, then retry."))
    print(json.dumps({"decision": "block", "reason": "\n".join(lines)}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        if os.environ.get("MODULARIO_DEBUG") == "1":
            print(f"[Modulario] pre-hook error: {exc}", file=sys.stderr)
