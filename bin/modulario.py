#!/usr/bin/env python3
"""mod — structural health monitor. Thin dispatcher; per-command logic lives
in scripts/cli_*.py."""
import os
import sys
from pathlib import Path

MODULARIO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODULARIO_DIR / 'scripts'))

from cli_common import print_help
from cli_analyze import cmd_launch, cmd_query, cmd_off
from cli_graph import cmd_graph
from cli_watch import cmd_watch
from cli_doc import cmd_document
from cli_ignore import cmd_ignore
from cli_autostart import cmd_autostart
from cli_end_attempt import cmd_end_attempt
from cli_agents import cmd_agents
from cli_projects import pick_project


def main():
    args = sys.argv[1:]
    if not args:
        target = pick_project()
        if target:
            cmd_launch(target)
        return

    if args[0] in ('-h', '--help', '-help', 'help'):
        print_help()
        return

    if args[0] == 'query':
        if len(args) < 2:
            print("Usage: mod query <directory>")
            sys.exit(1)
        cmd_query(args[1])
        return

    if args[0] == 'off':
        cmd_off()
        return

    if args[0] == 'watch':
        cmd_watch(args[1:])
        return

    if args[0] == 'doc':
        cmd_document(args[1:])
        return

    if args[0] == 'ignore':
        cmd_ignore(args[1:])
        return

    if args[0] == 'graph':
        cmd_graph(args[1:])
        return

    if args[0] == 'autostart':
        cmd_autostart(args[1:])
        return

    if args[0] == 'agents':
        sys.exit(cmd_agents(args[1:]))

    if args[0] in ('end-attempt', 'end_attempt'):
        cmd_end_attempt(args[1:])
        return

    if args[0] == 'goals':
        goals_script = MODULARIO_DIR / 'scripts' / 'goals.py'
        os.execv(sys.executable, [sys.executable, str(goals_script), *args[1:]])

    cmd_launch(args[0])


if __name__ == '__main__':
    main()
