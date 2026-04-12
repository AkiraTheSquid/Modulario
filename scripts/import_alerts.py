"""
Modulario — import-graph alert checks fired on every PostToolUse.

Three checks:
  1. Broken import paths  — a file imports a path that no longer exists on disk
  2. Removed named export — changed JS/TS file no longer exports a name that an
                            importer expects
  3. Fan-in warning       — changed file has many dependents (wide blast radius)
"""
import os
import re
from pathlib import Path

_JS_EXTS = {'.js', '.ts', '.jsx', '.tsx', '.mjs', '.cjs'}
FAN_IN_THRESHOLD = 3  # warn when a changed file has this many or more importers


# ── Export / import parsers ────────────────────────────────────────────────────

def _js_exports(content):
    """Return set of names exported by a JS/TS file."""
    exports = set()

    # export { foo, bar as baz }
    for m in re.finditer(r'export\s*\{([^}]+)\}', content):
        for part in m.group(1).split(','):
            part = part.strip()
            if not part or part.startswith('//'):
                continue
            as_m = re.match(r'\w+\s+as\s+(\w+)', part)
            exports.add(as_m.group(1) if as_m else part.split()[0])

    # export [async] function/class/const/let/var name
    for m in re.finditer(
        r'export\s+(?:default\s+)?(?:async\s+)?'
        r'(?:function\*?|class|const|let|var)\s+(\w+)',
        content,
    ):
        exports.add(m.group(1))

    if re.search(r'export\s+default[\s({]', content):
        exports.add('default')

    return exports


def _js_named_imports(content, dep_basename):
    """Return set of names imported from any path whose basename matches dep_basename."""
    names = set()
    pattern = (
        r'import\s*\{([^}]+)\}\s*from\s*[\'"][^\'"]*'
        + re.escape(dep_basename)
        + r'[\'"]'
    )
    for m in re.finditer(pattern, content):
        for part in m.group(1).split(','):
            part = part.strip()
            if not part or part.startswith('//'):
                continue
            # "foo as bar" → original name is foo
            as_m = re.match(r'(\w+)\s+as\s+\w+', part)
            names.add(as_m.group(1) if as_m else part.split()[0])
    return names


# ── Main entry point ───────────────────────────────────────────────────────────

def build_import_alerts(state, changed_file, target_dir):
    """
    Return a list of alert strings for import-graph problems.
    Called from modulario-analyze.py after every analysis run.
    """
    alerts = []
    graph = state.get('import_graph', {})   # rel -> [rel, ...]
    target = Path(target_dir).resolve()

    # ── 1. Broken import paths ─────────────────────────────────────────────
    for src_rel, deps in graph.items():
        for dep_rel in deps:
            if not (target / dep_rel).exists():
                alerts.append(
                    f"[IMPORT] BROKEN PATH: {src_rel} imports "
                    f"{dep_rel} — file does not exist on disk"
                )

    # ── 2 & 3. Changed-file checks ─────────────────────────────────────────
    if not changed_file:
        return alerts

    try:
        changed_rel = str(Path(changed_file).resolve().relative_to(target))
    except ValueError:
        return alerts

    importers = [src for src, deps in graph.items() if changed_rel in deps]

    # 3. Fan-in warning
    if len(importers) >= FAN_IN_THRESHOLD:
        # Sort importers by their own fan-in (how many files import them) descending.
        # This surfaces the most "load-bearing" dependents first.
        def _fan_in(path):
            return sum(1 for deps in graph.values() if path in deps)

        ranked = sorted(importers, key=_fan_in, reverse=True)
        importer_list = '\n'.join(
            f"    - {p}  (imported by {_fan_in(p)})" for p in ranked
        )
        alerts.append(
            f"[IMPORT] HIGH FAN-IN: {changed_rel} is imported by "
            f"{len(importers)} files — changes here have wide blast radius.\n"
            f"  Dependents ranked by their own fan-in:\n{importer_list}"
        )

    # 2. Removed named export (JS/TS only)
    ext = Path(changed_file).suffix.lower()
    if ext in _JS_EXTS and importers:
        changed_abs = target / changed_rel
        if changed_abs.exists():
            try:
                content = changed_abs.read_text(encoding='utf-8', errors='ignore')
                current_exports = _js_exports(content)
                dep_basename = changed_abs.name

                for imp_rel in importers:
                    imp_abs = target / imp_rel
                    if not imp_abs.exists():
                        continue
                    try:
                        imp_content = imp_abs.read_text(encoding='utf-8', errors='ignore')
                    except OSError:
                        continue
                    expected = _js_named_imports(imp_content, dep_basename)
                    missing = expected - current_exports - {'default'}
                    if missing:
                        alerts.append(
                            f"[IMPORT] BROKEN EXPORT: {imp_rel} imports "
                            f"{{{', '.join(sorted(missing))}}} from {changed_rel} "
                            f"— not found in current exports"
                        )
            except OSError:
                pass

    return alerts
