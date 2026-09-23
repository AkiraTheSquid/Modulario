"""Configured Modulario projects + usage-ranked picker history."""
import json
import os
import time
from pathlib import Path


MODULARIO_DIR = Path(__file__).resolve().parent.parent
PROJECTS_DIR = MODULARIO_DIR / "configs" / "projects"
HISTORY_PATH = MODULARIO_DIR / "data" / "project-history.json"


def _real(path):
    return os.path.realpath(os.path.expanduser(str(path or "")))


def path_is_within(path, root):
    """True when path equals root or lives below it."""
    path_real = _real(path)
    root_real = _real(root)
    if not path_real or not root_real:
        return False
    try:
        return os.path.commonpath((path_real, root_real)) == root_real
    except ValueError:
        return False


def load_projects(projects_dir=None, *, existing_only=True):
    """Load enabled project records, deduped by real target path."""
    directory = Path(projects_dir or PROJECTS_DIR)
    projects = []
    seen = set()
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            continue
        if raw.get("enabled", True) is False:
            continue
        target = _real(raw.get("target_dir"))
        if not target or target in seen:
            continue
        if existing_only and not os.path.isdir(target):
            continue
        seen.add(target)
        projects.append({
            "name": str(raw.get("name") or path.stem),
            "display_name": str(raw.get("display_name") or raw.get("name") or Path(target).name),
            "target_dir": target,
            "priority": int(raw.get("priority") or 0),
            "config_path": str(path),
        })
    return projects


def load_history(path=None):
    history_path = Path(path or HISTORY_PATH)
    try:
        raw = json.loads(history_path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def rank_projects(projects, history=None):
    """Most recently/frequently used first; config priority breaks cold ties."""
    usage = history if history is not None else load_history()

    def key(project):
        stats = usage.get(project["target_dir"], {})
        return (
            int(stats.get("uses") or 0),
            float(stats.get("last_used") or 0),
            int(project.get("priority") or 0),
            project["display_name"].lower(),
        )

    return sorted(projects, key=key, reverse=True)


def record_project_use(target_dir, path=None):
    history_path = Path(path or HISTORY_PATH)
    history = load_history(history_path)
    target = _real(target_dir)
    entry = history.get(target, {})
    history[target] = {
        "uses": int(entry.get("uses") or 0) + 1,
        "last_used": time.time(),
    }
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")


def find_project_for_path(path, projects=None):
    """Return deepest registered project containing path."""
    matches = [
        project for project in (projects if projects is not None else load_projects())
        if path_is_within(path, project["target_dir"])
    ]
    if not matches:
        return None
    return max(matches, key=lambda project: len(project["target_dir"]))


def project_by_token(token, projects=None):
    wanted = str(token or "").strip().lower()
    for project in projects if projects is not None else load_projects():
        if wanted in {
            project["name"].lower(),
            project["display_name"].lower(),
            project["target_dir"].lower(),
        }:
            return project
    return None
