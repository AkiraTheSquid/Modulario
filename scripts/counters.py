"""
Modulario — LOC counting, DEPS counting, and status assignment.
Imported by modulario-analyze.py.
"""
import re


def count_loc_python(content):
    """Non-blank, non-pure-comment lines in Python."""
    count = 0
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith('#'):
            count += 1
    return count


def count_loc_js(content):
    """Non-blank, non-comment lines in JS/TS."""
    count = 0
    in_block = False
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if in_block:
            if '*/' in stripped:
                in_block = False
            continue
        if stripped.startswith('//'):
            continue
        if stripped.startswith('/*'):
            if '*/' not in stripped[2:]:
                in_block = True
            continue
        count += 1
    return count


def count_loc_css(content):
    """Non-blank, non-comment lines in CSS."""
    count = 0
    in_block = False
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if in_block:
            if '*/' in stripped:
                in_block = False
                tail = stripped.split('*/', 1)[1].strip()
                if tail:
                    count += 1
            continue
        if stripped.startswith('/*'):
            if '*/' in stripped[2:]:
                tail = stripped.split('*/', 1)[1].strip()
                if tail:
                    count += 1
            else:
                in_block = True
            continue
        count += 1
    return count


def count_deps_python(content, local_packages=None):
    """Count unique local imports — relative and absolute project-local."""
    deps = set()
    for line in content.splitlines():
        stripped = line.strip()
        m = re.match(r'^from\s+(\.[\w.]*)\s+import', stripped)
        if m:
            deps.add(m.group(1))
            continue
        if not local_packages:
            continue
        m = re.match(r'^from\s+([\w.]+)\s+import', stripped)
        if m:
            top = m.group(1).split('.')[0]
            if top in local_packages:
                deps.add(m.group(1))
            continue
        m = re.match(r'^import\s+([\w.,\s]+)', stripped)
        if m:
            for part in m.group(1).split(','):
                top = part.strip().split('.')[0]
                if top in local_packages:
                    deps.add(top)
    return len(deps)


def count_deps_js(content):
    """Count unique local path imports (starting with ./ or ../)."""
    deps = set()
    for m in re.finditer(r"""(?:import\s+.*?from|require\s*\()\s*['"](\.\.?/[^'"]+)['"]""", content):
        raw = re.sub(r'\.(js|ts|jsx|tsx|mjs|cjs)$', '', m.group(1))
        deps.add(re.sub(r'/index$', '', raw))
    for m in re.finditer(r"""import\s*\(\s*['"](\.\.?/[^'"]+)['"]\s*\)""", content):
        raw = re.sub(r'\.(js|ts|jsx|tsx|mjs|cjs)$', '', m.group(1))
        deps.add(raw)
    return len(deps)


def count_deps_css(content):
    """Count unique local stylesheet imports."""
    deps = set()
    for m in re.finditer(r"""@import\s+(?:url\()?['"]?(\.\.?/[^'")\s]+)['"]?\)?""", content):
        raw = re.sub(r'\.css$', '', m.group(1))
        deps.add(re.sub(r'/index$', '', raw))
    return len(deps)


def assign_status(loc, deps, t):
    def band(value, boundaries):
        for i, b in enumerate(boundaries):
            if value <= b:
                return i
        return len(boundaries)

    d = band(loc, t['loc_bands']) + band(deps, t['deps_bands'])
    if d == 0: return 'GREEN'
    if d == 1: return 'LIME'
    if d == 2: return 'YELLOW'
    if d == 3: return 'ORANGE'
    return 'RED'
