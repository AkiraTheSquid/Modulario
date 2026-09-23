"""`mod graph <file>` — print fan-out and fan-in for a file."""
import argparse
import json
import os
import sys

from cli_common import resolve_target_file, state_path_for


def cmd_graph(argv):
    p = argparse.ArgumentParser(prog='mod graph', add_help=False)
    p.add_argument('file')
    p.add_argument('--target', default=None, help='Override active target directory')
    args = p.parse_args(argv)

    if args.target:
        target = os.path.realpath(os.path.expanduser(args.target))
        if not os.path.isdir(target):
            print(f"Not a directory: {args.target}")
            sys.exit(1)
        file_path = os.path.realpath(os.path.expanduser(args.file))
        if not os.path.isabs(args.file):
            file_path = os.path.realpath(os.path.join(target, args.file))
        if not os.path.isfile(file_path):
            print(f"Not a file: {args.file}")
            sys.exit(1)
        rel_path = os.path.relpath(file_path, target)
    else:
        target, rel_path = resolve_target_file(args.file)

    sp = state_path_for(target)
    if not sp.exists():
        print(f"No state found for {target}. Run `mod {target}` first.")
        sys.exit(1)

    with open(sp) as f:
        state = json.load(f)

    graph = state.get('import_graph', {})
    imports = graph.get(rel_path, [])
    imported_by = [src for src, targets in graph.items() if rel_path in targets]

    print(f"[Modulario] graph: {rel_path}")
    print()
    print(f"  Imports ({len(imports)}):")
    if imports:
        for p in sorted(imports):
            print(f"    -> {p}")
    else:
        print("    (none within target)")

    print()
    print(f"  Imported by ({len(imported_by)}):")
    if imported_by:
        for p in sorted(imported_by):
            print(f"    <- {p}")
    else:
        print("    (nothing imports this file)")
