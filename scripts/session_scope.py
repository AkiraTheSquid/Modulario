"""Per-agent touched-file state + import-graph scope expansion."""
import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


SESSION_TTL = 30 * 24 * 3600
SESSION_CAP = 100


def state_path_for_target(state_dir, target):
    digest = hashlib.md5(os.path.realpath(target).encode()).hexdigest()[:12]
    return Path(state_dir) / f"{digest}.json"


def touched_path_for_state(state_path):
    path = Path(state_path)
    return path.with_name(path.stem + "_touched.json")


def session_key(provider, session_id):
    provider_name = str(provider or "unknown").strip().lower() or "unknown"
    sid = str(session_id or "default").strip() or "default"
    return f"{provider_name}:{sid}"


def _normalize_changed_file(target, changed_file):
    if not changed_file:
        return ""
    target_real = os.path.realpath(target)
    candidate = os.path.realpath(os.path.expanduser(str(changed_file)))
    if not os.path.isabs(str(changed_file)):
        candidate = os.path.realpath(os.path.join(target_real, str(changed_file)))
    try:
        rel = os.path.relpath(candidate, target_real).replace("\\", "/")
    except ValueError:
        return ""
    if rel == ".." or rel.startswith("../"):
        return ""
    return rel


def _folders_for_files(files):
    folders = set()
    for rel in files:
        parts = str(rel).replace("\\", "/").split("/")[:-1]
        acc = []
        for part in parts:
            if part in ("", ".", ".."):
                continue
            acc.append(part)
            folders.add("/".join(acc))
    return folders


def _prune(sessions, now):
    cutoff = now - SESSION_TTL
    stale = [
        key for key, value in sessions.items()
        if not isinstance(value, dict) or value.get("updated", 0) < cutoff
    ]
    for key in stale:
        sessions.pop(key, None)
    if len(sessions) > SESSION_CAP:
        newest = sorted(
            sessions.items(), key=lambda item: item[1].get("updated", 0), reverse=True
        )[:SESSION_CAP]
        sessions.clear()
        sessions.update(newest)


@contextmanager
def _state_lock(path):
    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def update_session_scope(state_path, target, changed_files, session_id, provider):
    """Record changed files for one provider/session. Return current entry."""
    touched_path = touched_path_for_state(state_path)
    key = session_key(provider, session_id)
    with _state_lock(touched_path):
        try:
            data = json.loads(touched_path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            data = {}
        sessions = data.get("sessions")
        if not isinstance(sessions, dict):
            sessions = {}
        entry = sessions.get(key)
        if not isinstance(entry, dict):
            entry = {"folders": [], "files": [], "updated": 0}
        files = set(entry.get("files") or [])
        for changed_file in changed_files or []:
            rel = _normalize_changed_file(target, changed_file)
            if rel:
                files.add(rel)
        now = int(time.time())
        entry.update({
            "provider": str(provider or "unknown"),
            "session_id": str(session_id or "default"),
            "files": sorted(files),
            "folders": sorted(_folders_for_files(files)),
            "updated": now,
        })
        sessions[key] = entry
        _prune(sessions, now)
        touched_path.parent.mkdir(parents=True, exist_ok=True)
        touched_path.write_text(json.dumps({"sessions": sessions}, indent=2) + "\n", encoding="utf-8")
    return entry


def load_session_scope(state_path, session_id, provider):
    touched_path = touched_path_for_state(state_path)
    try:
        data = json.loads(touched_path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return None
    entry = (data.get("sessions") or {}).get(session_key(provider, session_id))
    return entry if isinstance(entry, dict) else None


def compute_graph_scope(touched_files, import_graph):
    """Expand touched files one hop through direct fan-out + fan-in."""
    graph = import_graph or {}
    reverse = {}
    for source, dependencies in graph.items():
        for dependency in dependencies or []:
            reverse.setdefault(dependency, set()).add(source)

    files = set(touched_files or [])
    for touched in list(files):
        files.update(graph.get(touched, []) or [])
        files.update(reverse.get(touched, set()))

    folders = _folders_for_files(files)
    if files:
        folders.add("")  # root watch represents project-wide invariants
    return files, folders


def load_target_session(state_dir, target, session_id, provider):
    state_path = state_path_for_target(state_dir, target)
    if not state_path.exists():
        return None
    entry = load_session_scope(state_path, session_id, provider)
    if not entry or not entry.get("files"):
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return None
    scope_files, scope_folders = compute_graph_scope(
        entry.get("files") or [], state.get("import_graph") or {}
    )
    return {
        "target": os.path.realpath(target),
        "state_path": state_path,
        "state": state,
        "touched_files": set(entry.get("files") or []),
        "touched_folders": set(entry.get("folders") or []),
        "scope_files": scope_files,
        "scope_folders": scope_folders,
    }
