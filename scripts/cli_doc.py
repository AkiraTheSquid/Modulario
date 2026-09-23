"""`mod doc ...` — per-folder README.md templates."""
import argparse
import os
import sys
from datetime import datetime

from cli_common import active_target_dir, filter_walk_dirs, load_skip_dirs, resolve_target_folder

TEMPLATE_MARKER = '<!-- modulario:template -->'


def _template(folder_name):
    return f"""{TEMPLATE_MARKER}
# {folder_name}

## Purpose
- One or two sentences on what this folder is responsible for.
- Describe the business/domain concern, not the technical details.

## Owns
- List the main responsibilities this folder **does own**.
- Each item should be something that changes when this folder changes.

## Does NOT own
- List responsibilities that live elsewhere to prevent scope creep.
- Link to the other folder/module if relevant.

## Key Files
- `example.js`: short description of what this file is and when it runs.

## Data & External Dependencies
- What data models or types this area works with.
- What external services or libraries it directly touches.
- Any important shared modules it depends on.

## How It Works (Flow)
1. Brief step-by-step of the main flow.
2. Optional secondary flows if they are important.

## Invariants & Constraints
- Rules that **must** remain true.
- Performance or security constraints.
- "Never do X" type rules that are easy to forget.

## Extension Points
- How to add a new feature in this area.
- What file to start from when extending behavior.

## Known Issues, Recurring Bugs, and Pain Points (and How to Prevent Them)

- **Short name of issue** — `ACTIVE` or `RESOLVED`
  - When it happens: one line about the situation/context.
  - Symptom: what you see break.
  - Root cause: the underlying mistake or assumption.
  - Prevention/fix: the rule, pattern, or helper to use so it doesn't come back.
  - Status: `ACTIVE` = still a risk, `RESOLVED` = was an issue, now fixed (keep for history).

## Recent Changes
- {datetime.now().strftime('%Y-%m-%d')}: Initial doc created.
"""


def _cmd_init(target, folder_arg):
    _, folder = resolve_target_folder(folder_arg)
    folder_name = folder.rstrip('/').split('/')[-1] if folder != '/' else os.path.basename(target)
    abs_doc = os.path.join(target, folder.rstrip('/'), 'README.md') if folder != '/' else os.path.join(target, 'README.md')
    if os.path.exists(abs_doc):
        print(f"File already exists: {folder}README.md")
        sys.exit(1)
    os.makedirs(os.path.dirname(abs_doc), exist_ok=True)
    with open(abs_doc, 'w') as f:
        f.write(_template(folder_name))
    print(f"Created {folder}README.md — fill in the template.")


def _cmd_list(target):
    unfilled, filled = [], []
    skip_dirs = load_skip_dirs()
    for root, dirs, _filenames in os.walk(target):
        dirs[:] = filter_walk_dirs(root, dirs, target, skip_dirs)
        readme = os.path.join(root, 'README.md')
        rel = os.path.relpath(root, target)
        rel = '/' if rel == '.' else rel + '/'
        if os.path.exists(os.path.join(root, '.doc.dismissed')):
            continue
        if not os.path.exists(readme):
            unfilled.append((rel, 'missing'))
        else:
            try:
                with open(readme, 'r') as f:
                    first_line = f.readline()
                if TEMPLATE_MARKER in first_line:
                    unfilled.append((rel, 'unfilled'))
                else:
                    filled.append(rel)
            except OSError:
                pass
    if unfilled:
        print(f"Unfilled ({len(unfilled)}):")
        for folder, status in sorted(unfilled):
            print(f"  {folder:<40} {status}")
    if filled:
        print(f"Filled ({len(filled)}):")
        for folder in sorted(filled):
            print(f"  {folder}")
    if not unfilled and not filled:
        print("No folders found.")


def _cmd_dismiss(target, folder_arg):
    _, folder = resolve_target_folder(folder_arg)
    abs_folder = os.path.join(target, folder.rstrip('/')) if folder != '/' else target
    marker = os.path.join(abs_folder, '.doc.dismissed')
    if os.path.exists(marker):
        print(f"Already dismissed: {folder}")
        return
    readme = os.path.join(abs_folder, 'README.md')
    if os.path.exists(readme):
        with open(readme, 'r') as f:
            first_line = f.readline()
        if TEMPLATE_MARKER in first_line:
            os.remove(readme)
    with open(marker, 'w') as f:
        f.write('')
    print(f"Dismissed doc nag for '{folder}'. Remove {folder}.doc.dismissed to re-enable.")


def cmd_document(argv):
    p = argparse.ArgumentParser(prog='mod doc', add_help=True)
    sub = p.add_subparsers(dest='doc_cmd', required=True)

    p_init = sub.add_parser('init', help='Create a doc template for a folder')
    p_init.add_argument('folder', help='Folder to create template for')

    sub.add_parser('list', help='Show folders with unfilled doc templates')

    p_dismiss = sub.add_parser('dismiss', help='Dismiss doc nag for a folder')
    p_dismiss.add_argument('folder', help='Folder to dismiss')

    args = p.parse_args(argv)
    target = active_target_dir()

    if args.doc_cmd == 'init':
        _cmd_init(target, args.folder)
    elif args.doc_cmd == 'list':
        _cmd_list(target)
    elif args.doc_cmd == 'dismiss':
        _cmd_dismiss(target, args.folder)
