"""
Modulario — import graph collection and violation detection.
Imported by modulario-analyze.py.
"""
import os
import re
import sys
from pathlib import Path


def resolve_py_relative(import_str, importer_dir, files_set):
    """Resolve a Python relative import string to an absolute file path, or None."""
    m = re.match(r'^(\.+)(.*)', import_str)
    if not m:
        return None
    dots = len(m.group(1))
    rest = m.group(2)

    base = Path(importer_dir)
    for _ in range(dots - 1):
        base = base.parent

    if rest:
        candidate = base / Path(rest.replace('.', os.sep))
    else:
        candidate = base

    for p in [str(candidate) + '.py', str(candidate / '__init__.py')]:
        if p in files_set:
            return p
    return None


def resolve_py_module(import_str, files_set):
    """Resolve a Python module path like foo.bar to an absolute file path, or None."""
    rel_mod = import_str.replace('.', os.sep)
    suffixes = [
        os.sep + rel_mod + '.py',
        os.sep + rel_mod + os.sep + '__init__.py',
    ]
    for path in files_set:
        if any(path.endswith(suffix) for suffix in suffixes):
            return path
    return None


def resolve_js_relative(import_str, importer_dir, files_set):
    """Resolve a JS relative import string to an absolute file path, or None."""
    import_str = import_str.split('?')[0].split('#')[0]
    candidate = (Path(importer_dir) / import_str).resolve()
    # Check exact path first (import already includes extension, e.g. './foo.js')
    if str(candidate) in files_set:
        return str(candidate)
    for ext in ['.js', '.ts', '.jsx', '.tsx', '.mjs', '.cjs']:
        for p in [str(candidate) + ext, str(candidate / 'index') + ext]:
            if p in files_set:
                return p
    return None


def resolve_css_relative(import_str, importer_dir, files_set):
    """Resolve a CSS relative import string to an absolute file path, or None."""
    import_str = import_str.split('?')[0].split('#')[0]
    candidate = (Path(importer_dir) / import_str).resolve()
    for p in [str(candidate), str(candidate) + '.css', str(candidate / 'index.css')]:
        if p in files_set:
            return p
    return None


def collect_graph_data(content, file_abs, ext, files_set, local_packages):
    """
    Returns:
      graph_edges        — list of resolved abs paths this file imports
      private_violations — list of (importer_abs, module_ref, private_member)
    """
    graph_edges = []
    private_violations = []
    file_dir = str(Path(file_abs).parent)

    if ext == '.py':
        for line in content.splitlines():
            stripped = line.strip()

            m = re.match(r'^from\s+(\.[\w.]*)\s+import\s+(.+)', stripped)
            if m:
                mod = m.group(1)
                members_str = m.group(2).strip()
                resolved = resolve_py_relative(mod, file_dir, files_set)
                if resolved:
                    graph_edges.append(resolved)
                if re.search(r'\._', mod):
                    private_violations.append((file_abs, mod, mod))
                for mem in (x.strip().split()[0] for x in members_str.split(',') if x.strip()):
                    if mem.startswith('_') and not (mem.startswith('__') and mem.endswith('__')):
                        private_violations.append((file_abs, mod, mem))
                continue

            m = re.match(r'^from\s+([\w.]+)\s+import\s+(.+)', stripped)
            if m and local_packages:
                mod = m.group(1)
                top = mod.split('.')[0]
                if top in local_packages:
                    members_str = m.group(2).strip()
                    resolved = resolve_py_module(mod, files_set)
                    if resolved:
                        graph_edges.append(resolved)
                    if re.search(r'\._', mod):
                        private_violations.append((file_abs, mod, mod))
                    for mem in (x.strip().split()[0] for x in members_str.split(',') if x.strip()):
                        if mem.startswith('_') and not (mem.startswith('__') and mem.endswith('__')):
                            private_violations.append((file_abs, mod, mem))
                continue

            m = re.match(r'^import\s+(.+)', stripped)
            if m and local_packages:
                modules_str = m.group(1).strip()
                for mod in (x.strip().split()[0] for x in modules_str.split(',') if x.strip()):
                    top = mod.split('.')[0]
                    if top not in local_packages:
                        continue
                    resolved = resolve_py_module(mod, files_set)
                    if resolved:
                        graph_edges.append(resolved)
    elif ext in {'.js', '.ts', '.jsx', '.tsx', '.mjs', '.cjs'}:
        for m in re.finditer(
            r"""import\s*\{([^}]+)\}\s*from\s*['"](\.[^'"]+)['"]""", content
        ):
            names_str = m.group(1)
            imp_path = m.group(2)
            resolved = resolve_js_relative(imp_path, file_dir, files_set)
            if resolved:
                graph_edges.append(resolved)
            for name in (n.strip().split()[0] for n in names_str.split(',') if n.strip()):
                if name.startswith('_'):
                    private_violations.append((file_abs, imp_path, name))

        for m in re.finditer(
            r"""import\s+(?:\w+|\*\s+as\s+\w+)\s+from\s*['"](\.[^'"]+)['"]""", content
        ):
            imp_path = m.group(1)
            resolved = resolve_js_relative(imp_path, file_dir, files_set)
            if resolved:
                graph_edges.append(resolved)

        for m in re.finditer(r"""require\s*\(\s*['"](\.[^'"]+)['"]\s*\)""", content):
            imp_path = m.group(1)
            resolved = resolve_js_relative(imp_path, file_dir, files_set)
            if resolved:
                graph_edges.append(resolved)

        for m in re.finditer(r"""(?<!\w)import\s*\(\s*['"](\.[^'"]+)['"]\s*\)""", content):
            imp_path = m.group(1)
            resolved = resolve_js_relative(imp_path, file_dir, files_set)
            if resolved:
                graph_edges.append(resolved)
    elif ext == '.css':
        for m in re.finditer(
            r"""@import\s+(?:url\()?['"]?(\.\.?/[^'")\s]+)['"]?\)?""", content
        ):
            imp_path = m.group(1)
            resolved = resolve_css_relative(imp_path, file_dir, files_set)
            if resolved:
                graph_edges.append(resolved)

    graph_edges = list(dict.fromkeys(graph_edges))
    return graph_edges, private_violations


def detect_cycles(graph):
    """DFS cycle detection. Returns list of cycles; each is [a, b, c, a]."""
    all_nodes = set(graph.keys())
    for neighbors in graph.values():
        all_nodes.update(neighbors)

    visited   = set()
    rec_stack = []
    rec_set   = set()
    cycles    = []
    seen_sets = set()

    def dfs(node):
        rec_stack.append(node)
        rec_set.add(node)
        for neighbor in graph.get(node, []):
            if neighbor in rec_set:
                idx   = rec_stack.index(neighbor)
                cycle = rec_stack[idx:] + [neighbor]
                key   = frozenset(rec_stack[idx:])
                if key not in seen_sets:
                    seen_sets.add(key)
                    cycles.append(cycle)
            elif neighbor not in visited:
                dfs(neighbor)
        rec_stack.pop()
        rec_set.discard(node)
        visited.add(node)

    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old_limit, len(all_nodes) * 3 + 200))
    try:
        for node in sorted(all_nodes):
            if node not in visited:
                dfs(node)
    finally:
        sys.setrecursionlimit(old_limit)

    return cycles


def find_violations(import_graph, private_violations_raw, target_path, ignore_list=None):
    """Returns {'cycles': [...], 'private': [...]}."""
    ignore = set(ignore_list or [])
    target = Path(target_path).resolve()

    def to_rel(p):
        try:
            return str(Path(p).relative_to(target))
        except ValueError:
            return p

    cycle_violations = []
    for cycle in detect_cycles(import_graph):
        rel_paths = [to_rel(p) for p in cycle]
        if any(p in ignore for p in rel_paths):
            continue
        cycle_violations.append({'type': 'circular_import', 'files': rel_paths})

    priv_violations = []
    seen_priv = set()
    for (importer, module_ref, member) in private_violations_raw:
        rel_importer = to_rel(importer)
        if rel_importer in ignore:
            continue
        key = (rel_importer, module_ref, member)
        if key in seen_priv:
            continue
        seen_priv.add(key)
        priv_violations.append({
            'type': 'private_access',
            'importer': rel_importer,
            'module': module_ref,
            'member': member,
        })

    return {'cycles': cycle_violations, 'private': priv_violations}


def file_in_violations(changed_file, violations):
    """Return True if changed_file is involved in any violation."""
    if not changed_file:
        return False
    cf = os.path.basename(changed_file)

    def _matches(viol_path):
        return (viol_path == changed_file
                or changed_file.endswith(os.sep + viol_path)
                or os.path.basename(viol_path) == cf)

    for c in violations.get('cycles', []):
        if any(_matches(p) for p in c['files']):
            return True
    for p in violations.get('private', []):
        if _matches(p['importer']):
            return True
    return False


def format_violation_block(violations, changed_file, target_path):
    lines = ["[Modulario] BOUNDARY VIOLATION DETECTED — fix error before proceeding.\n"]

    for c in violations.get('cycles', []):
        lines.append("[VIOLATION] Circular import:")
        for p in c['files']:
            lines.append(f"  {p}")
        lines.append("Fix: one of these files must stop importing the other.\n")

    for p in violations.get('private', []):
        lines.append("[VIOLATION] Private API access:")
        lines.append(f"  {p['importer']}  imports  {p['member']}  from  {p['module']}")
        lines.append(f"Fix: use the public API instead of accessing {p['member']} directly.\n")

    return '\n'.join(lines)
