"""Formats the plaintext 'bug report' the user copies to the clipboard.

Bound to the `b` key in the main view: walks the last analyzer result's
violations + watch results and produces a single multi-line string listing
circular imports, private-API accesses, and failing watch scripts. Kept out
of the main handler because the formatting is chatty and would otherwise
inflate that file.
"""


def build_bug_report(target_dir, violations, watch_results):
    lines = [f"Modulario bugs detected in {target_dir}", ""]

    cycles = (violations or {}).get('cycles', [])
    if cycles:
        lines.append("Circular imports:")
        for c in cycles:
            files = c['files']
            lines.append(f"  [CYCLE] {files[0]}")
            for f in files[1:]:
                lines.append(f"       → {f}")
        lines.append("")

    priv = (violations or {}).get('private', [])
    if priv:
        lines.append("Private API access:")
        for p in priv:
            lines.append(f"  [PRIV]  {p['importer']} imports {p['member']} from {p['module']}")
        lines.append("")

    fails = [(n, r) for (s, n, r) in (watch_results or []) if s == 'FAIL']
    if fails:
        lines.append("Watch failures:")
        for n, r in fails:
            lines.append(f"  [WATCH] {n}: {r}")
        lines.append("")

    if not (cycles or priv or fails):
        lines.append("(no bugs detected)")

    return '\n'.join(lines)
