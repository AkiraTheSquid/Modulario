"""Claude/Codex hook payload adapter + pre/post Git change detection."""
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

from project_registry import find_project_for_path, load_projects, path_is_within


MODULARIO_DIR = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = MODULARIO_DIR / "tmp" / "hook-snapshots"
_PATCH_PATH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)
_UNIFIED_PATH = re.compile(r"^\+\+\+ (?:b/)?([^\t\n]+)", re.MULTILINE)


def _nested(payload, *keys):
    value = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def payload_provider(payload):
    explicit = payload.get("provider") if isinstance(payload, dict) else None
    if explicit:
        return str(explicit).lower()
    if isinstance(payload, dict) and ("turn_id" in payload or "model" in payload):
        return "codex"
    return "claude"


def payload_session_id(payload):
    for keys in (("session_id",), ("sessionId",), ("payload", "session_id"), ("payload", "sessionId")):
        value = _nested(payload, *keys)
        if isinstance(value, str) and value:
            return value
    return "default"


def payload_cwd(payload):
    for keys in (("cwd",), ("payload", "cwd")):
        value = _nested(payload, *keys)
        if isinstance(value, str) and value:
            return os.path.realpath(os.path.expanduser(value))
    return os.getcwd()


def payload_tool_name(payload):
    for keys in (("tool_name",), ("toolName",), ("payload", "tool_name"), ("payload", "toolName")):
        value = _nested(payload, *keys)
        if isinstance(value, str):
            return value
    return ""


def _tool_input(payload):
    for keys in (("tool_input",), ("toolInput",), ("payload", "tool_input"), ("payload", "toolInput")):
        value = _nested(payload, *keys)
        if isinstance(value, dict):
            return value
    return {}


def payload_effective_cwd(payload):
    """Tool-specific workdir wins over session cwd (notably Codex Bash)."""
    base = payload_cwd(payload)
    tool_input = _tool_input(payload)
    value = tool_input.get("workdir") or tool_input.get("cwd")
    if not isinstance(value, str) or not value:
        return base
    value = os.path.expanduser(value)
    if not os.path.isabs(value):
        value = os.path.join(base, value)
    return os.path.realpath(value)


def _resolve_path(raw, cwd):
    value = os.path.expanduser(str(raw or "").strip())
    if not value or value == "/dev/null":
        return ""
    if not os.path.isabs(value):
        value = os.path.join(cwd, value)
    return os.path.realpath(value)


def extract_direct_paths(payload):
    """Extract explicit file paths from Claude/Codex tool args."""
    tool_input = _tool_input(payload)
    cwd = payload_effective_cwd(payload)
    raw_paths = []
    for key in ("file_path", "path", "notebook_path", "filePath", "notebookPath"):
        value = tool_input.get(key)
        if isinstance(value, str):
            raw_paths.append(value)
    for key in ("patch", "input", "diff"):
        value = tool_input.get(key)
        if not isinstance(value, str):
            continue
        raw_paths.extend(_PATCH_PATH.findall(value))
        raw_paths.extend(_UNIFIED_PATH.findall(value))
    return sorted({path for raw in raw_paths if (path := _resolve_path(raw, cwd))})


def _command_text(payload):
    tool_input = _tool_input(payload)
    for key in ("cmd", "command"):
        value = tool_input.get(key)
        if isinstance(value, str):
            return value
    return ""


def candidate_projects(payload, projects=None):
    projects = projects if projects is not None else load_projects()
    selected = {}
    for path in extract_direct_paths(payload):
        project = find_project_for_path(path, projects)
        if project:
            selected[project["target_dir"]] = project

    cwd_project = find_project_for_path(payload_effective_cwd(payload), projects)
    if cwd_project:
        selected[cwd_project["target_dir"]] = cwd_project

    command = _command_text(payload)
    for project in projects:
        target = project["target_dir"]
        if target in command or str(Path(target).expanduser()) in command:
            selected[target] = project
    return list(selected.values())


def _snapshot_key(payload):
    parts = [
        payload_provider(payload),
        payload_session_id(payload),
        str(payload.get("turn_id") or payload.get("turnId") or ""),
        str(payload.get("tool_use_id") or payload.get("toolUseId") or ""),
        payload_tool_name(payload),
    ]
    digest = hashlib.sha256("\0".join(parts).encode()).hexdigest()[:24]
    return SNAPSHOT_DIR / f"{digest}.json"


def _run_git(repo_root, args):
    env = os.environ.copy()
    env["MODULARIO_SKIP"] = "1"
    proc = subprocess.run(
        ["git", "-C", repo_root, *args], capture_output=True, timeout=15, env=env
    )
    return proc.stdout if proc.returncode == 0 else b""


def _repo_root(target):
    raw = _run_git(target, ["rev-parse", "--show-toplevel"]).decode(errors="replace").strip()
    return os.path.realpath(raw) if raw else ""


def _file_fingerprint(path):
    if not os.path.lexists(path):
        return "missing"
    if os.path.islink(path):
        return "link:" + os.readlink(path)
    if not os.path.isfile(path):
        return "other"
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        return "unreadable"
    return digest.hexdigest()


def git_snapshot(target):
    """Fingerprint dirty/untracked paths + HEAD for one registered project."""
    target = os.path.realpath(target)
    repo_root = _repo_root(target)
    if not repo_root:
        return {"repo_root": "", "head": "", "files": {}}
    commands = (
        ["diff", "--name-only", "-z"],
        ["diff", "--cached", "--name-only", "-z"],
        ["ls-files", "--others", "--exclude-standard", "-z"],
    )
    rel_paths = set()
    for args in commands:
        for item in _run_git(repo_root, args).decode(errors="surrogateescape").split("\0"):
            if item:
                rel_paths.add(item)
    files = {}
    for repo_rel in rel_paths:
        absolute = os.path.realpath(os.path.join(repo_root, repo_rel))
        if not path_is_within(absolute, target):
            continue
        target_rel = os.path.relpath(absolute, target).replace("\\", "/")
        files[target_rel] = _file_fingerprint(absolute)
    head = _run_git(repo_root, ["rev-parse", "HEAD"]).decode(errors="replace").strip()
    return {"repo_root": repo_root, "head": head, "files": files}


def capture_before(payload, projects=None):
    snapshots = {}
    for project in candidate_projects(payload, projects):
        snapshots[project["target_dir"]] = git_snapshot(project["target_dir"])
    if not snapshots:
        return
    path = _snapshot_key(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 24 * 3600
    for stale in path.parent.glob("*.json"):
        try:
            if stale.stat().st_mtime < cutoff:
                stale.unlink()
        except OSError:
            pass
    path.write_text(json.dumps({"projects": snapshots}), encoding="utf-8")


def _committed_paths(before, after, target):
    old_head = before.get("head") or ""
    new_head = after.get("head") or ""
    repo_root = after.get("repo_root") or before.get("repo_root") or ""
    if not repo_root or not old_head or not new_head or old_head == new_head:
        return set()
    changed = set()
    raw = _run_git(repo_root, ["diff", "--name-only", "-z", old_head, new_head])
    for repo_rel in raw.decode(errors="surrogateescape").split("\0"):
        if not repo_rel:
            continue
        absolute = os.path.realpath(os.path.join(repo_root, repo_rel))
        if path_is_within(absolute, target):
            changed.add(os.path.relpath(absolute, target).replace("\\", "/"))
    return changed


def collect_changes(payload, projects=None):
    """Return {target_dir: [absolute changed paths]} for current tool call."""
    projects = projects if projects is not None else load_projects()
    changes = {}
    for path in extract_direct_paths(payload):
        project = find_project_for_path(path, projects)
        if project:
            changes.setdefault(project["target_dir"], set()).add(path)

    snapshot_path = _snapshot_key(payload)
    try:
        before_all = (json.loads(snapshot_path.read_text(encoding="utf-8")) or {}).get("projects", {})
    except (OSError, ValueError):
        before_all = {}
    try:
        snapshot_path.unlink()
    except FileNotFoundError:
        pass

    candidates = {p["target_dir"]: p for p in candidate_projects(payload, projects)}
    for target, before in before_all.items():
        project = find_project_for_path(target, projects)
        if project and project["target_dir"] == os.path.realpath(target):
            candidates[target] = project
            after = git_snapshot(target)
            before_files = before.get("files") or {}
            after_files = after.get("files") or {}
            rel_changed = {
                rel for rel in set(before_files) | set(after_files)
                if before_files.get(rel) != after_files.get(rel)
            }
            rel_changed.update(_committed_paths(before, after, target))
            for rel in rel_changed:
                changes.setdefault(target, set()).add(os.path.realpath(os.path.join(target, rel)))

    return {target: sorted(paths) for target, paths in changes.items() if paths}
