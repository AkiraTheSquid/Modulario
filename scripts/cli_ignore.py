"""`mod ignore ...` — sync repo ignores with Modulario skip_dirs + live state."""
import argparse
import os
import sys

from cli_common import (
    active_target_dir,
    add_skip_dir,
    load_skip_dirs,
    normalize_rel_path,
    remove_skip_dir,
    run_analyzer,
    state_path_for,
    sync_gitignore_rule,
)


def _resolve_ignore_path(target, path_arg):
    raw = path_arg.strip()
    candidate = os.path.realpath(os.path.join(target, os.path.expanduser(raw)))
    if not os.path.exists(candidate):
        print(f"Not found: {path_arg}")
        sys.exit(1)
    if not os.path.isdir(candidate):
        print(f"Ignore currently supports directories only: {path_arg}")
        sys.exit(1)
    try:
        rel = os.path.relpath(candidate, target)
    except ValueError:
        print(f"Path is outside current target: {candidate}")
        sys.exit(1)
    rel = normalize_rel_path(rel)
    if not rel:
        print("Refusing to ignore the target root.")
        sys.exit(1)
    return rel


def _reanalyze_target(target):
    sp = state_path_for(target)
    sp.parent.mkdir(parents=True, exist_ok=True)
    result = run_analyzer(target, sp)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "analysis failed"
        print(f"Ignore saved, but re-analysis failed: {detail}")
        sys.exit(1)


def _cmd_add(target, path_arg):
    rel = _resolve_ignore_path(target, path_arg)
    changed = add_skip_dir(rel)
    sync_gitignore_rule(target, rel, present=True)
    _reanalyze_target(target)
    status = "Added" if changed else "Already present"
    print(f"{status} ignore: {rel}")


def _cmd_remove(target, path_arg):
    rel = _resolve_ignore_path(target, path_arg)
    changed = remove_skip_dir(rel)
    sync_gitignore_rule(target, rel, present=False)
    _reanalyze_target(target)
    status = "Removed" if changed else "Not present"
    print(f"{status} ignore: {rel}")


def _cmd_list(_target):
    items = load_skip_dirs()
    if not items:
        print("No Modulario ignores configured.")
        return
    print(f"Ignores ({len(items)}):")
    for item in items:
        print(f"  {item}")


def cmd_ignore(argv):
    p = argparse.ArgumentParser(prog='mod ignore', add_help=True)
    sub = p.add_subparsers(dest='ignore_cmd', required=True)

    p_add = sub.add_parser('add', help='Ignore a folder or file path in Modulario and .gitignore')
    p_add.add_argument('path')

    p_remove = sub.add_parser('remove', help='Remove a Modulario/.gitignore ignore path')
    p_remove.add_argument('path')

    sub.add_parser('list', help='List configured Modulario ignore paths')

    args = p.parse_args(argv)
    target = active_target_dir()

    if args.ignore_cmd == 'add':
        _cmd_add(target, args.path)
    elif args.ignore_cmd == 'remove':
        _cmd_remove(target, args.path)
    else:
        _cmd_list(target)
