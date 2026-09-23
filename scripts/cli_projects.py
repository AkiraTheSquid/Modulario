"""Interactive project picker used by bare `mod`."""
import curses
import sys

from project_registry import load_projects, rank_projects


def _draw(stdscr, projects, cursor):
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    title = " Modulario — choose project "
    try:
        stdscr.addstr(0, 0, title.ljust(width)[:width], curses.A_REVERSE | curses.A_BOLD)
        stdscr.addstr(2, 2, "↑↓/jk select   Enter open   1-9 quick-select   q quit", curses.A_DIM)
    except curses.error:
        pass
    for index, project in enumerate(projects):
        if index + 4 >= height:
            break
        marker = "▶" if index == cursor else " "
        line = f" {marker} {index + 1}. {project['display_name']:<24} {project['target_dir']}"
        attr = curses.A_REVERSE | curses.A_BOLD if index == cursor else 0
        try:
            stdscr.addstr(index + 4, 1, line[:max(0, width - 2)], attr)
        except curses.error:
            pass
    stdscr.refresh()


def _pick(stdscr, projects):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    cursor = 0
    while True:
        _draw(stdscr, projects, cursor)
        key = stdscr.getch()
        if key in (curses.KEY_UP, ord("k")):
            cursor = (cursor - 1) % len(projects)
        elif key in (curses.KEY_DOWN, ord("j")):
            cursor = (cursor + 1) % len(projects)
        elif key in (10, 13, curses.KEY_ENTER):
            return projects[cursor]["target_dir"]
        elif ord("1") <= key <= ord("9"):
            index = key - ord("1")
            if index < len(projects):
                return projects[index]["target_dir"]
        elif key in (ord("q"), 27):
            return None


def pick_project():
    projects = rank_projects(load_projects())
    if not projects:
        print("No enabled projects. Add configs/projects/<name>.json.")
        return None
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("Configured Modulario projects:")
        for index, project in enumerate(projects, 1):
            print(f"  {index}. {project['display_name']}: {project['target_dir']}")
        print("Interactive picker requires terminal. Use `mod <project-name|directory>`.")
        return None
    return curses.wrapper(_pick, projects)
