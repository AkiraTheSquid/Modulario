"""`mod <dir>`, `mod query <dir>`, `mod off` — analyzer + TUI launching."""
import json
import os
import sys

from project_registry import load_projects, project_by_token, record_project_use
from cli_common import (
    ANALYZER, CURRENT_TARGET, HOOKS_PAUSED, THRESHOLDS, TUI,
    ensure_codex_hook, ensure_global_hook, run_analyzer,
    set_current_target, state_path_for,
)


def cmd_launch(target_dir):
    project = project_by_token(target_dir, load_projects())
    target = project['target_dir'] if project else os.path.realpath(os.path.expanduser(target_dir))
    if not os.path.isdir(target):
        print(f"Not a directory: {target}")
        sys.exit(1)

    sp = state_path_for(target)
    sp.parent.mkdir(parents=True, exist_ok=True)
    set_current_target(target)
    ensure_global_hook()
    ensure_codex_hook()
    record_project_use(target)

    print(f"Analyzing {target} ...", end=' ', flush=True)
    result = run_analyzer(target, sp)
    if result.stderr:
        print(result.stderr.strip())
    else:
        print()

    os.execv(sys.executable, [sys.executable, str(TUI), '--state', str(sp)])


def cmd_query(target_dir):
    """CLI-only analysis — prints structured report, no TUI. Used by Claude agent."""
    project = project_by_token(target_dir, load_projects())
    target = project['target_dir'] if project else os.path.realpath(os.path.expanduser(target_dir))
    if not os.path.isdir(target):
        print(f"Not a directory: {target}")
        sys.exit(1)

    sp = state_path_for(target)
    sp.parent.mkdir(parents=True, exist_ok=True)
    set_current_target(target)
    ensure_global_hook()
    ensure_codex_hook()
    record_project_use(target)

    result = run_analyzer(target, sp)
    if result.returncode != 0 and result.stderr:
        print(f"Analyzer error: {result.stderr.strip()}")
        sys.exit(1)

    with open(sp) as f:
        state = json.load(f)

    summary    = state['summary']
    files      = state['files']
    violations = state.get('violations', {})

    print(f"[Modulario] {target}")
    print(f"R:{summary['red']}  O:{summary['orange']}  Y:{summary['yellow']}"
          f"  L:{summary['lime']}  G:{summary['green']}  Total:{summary['total']}")
    print()

    hotspots = [f for f in files if f['status'] in ('RED', 'ORANGE')]
    hotspots.sort(key=lambda f: f['loc'] + f['deps'] * 20, reverse=True)

    if hotspots:
        print("Hotspots (RED/ORANGE, ranked by combined score):")
        for f in hotspots:
            score = f['loc'] + f['deps'] * 20
            flag  = '●' if f['status'] == 'RED' else '◆'
            print(f"  {flag} {f['status']:6}  {f['path']:<65}  "
                  f"LOC:{f['loc']:>5}  DEPS:{f['deps']:>3}  score:{score:>5}")
    else:
        print("Hotspots: none — all files are YELLOW or better.")
    print()

    cycles  = violations.get('cycles', [])
    private = violations.get('private', [])
    if cycles or private:
        print("Violations:")
        for c in cycles:
            chain = '\n              → '.join(c['files'])
            print(f"  [CYCLE]   {chain}")
        for p in private:
            print(f"  [PRIVATE] {p['importer']} imports {p['member']}")
    else:
        print("Violations: none")
    print()

    coup_file = str(sp)[:-5] + '_coupling.json'
    if os.path.exists(coup_file):
        with open(coup_file) as f:
            coupling = json.load(f)
        session_count = coupling.get('session_count', 0)
        thresholds_data = {}
        if THRESHOLDS.exists():
            try:
                with open(THRESHOLDS) as f:
                    thresholds_data = json.load(f)
            except Exception:
                pass
        min_sessions = thresholds_data.get('coupling_min_sessions', 20)
        pairs = coupling.get('pairs', [])
        strong = [p for p in pairs if p['coupling_status'] in ('STRONG', 'CRITICAL')]

        print(f"Coupling: {session_count} sessions of history  "
              f"({'active' if session_count >= min_sessions else f'inactive — need {min_sessions}'})")
        if strong:
            print("  STRONG / CRITICAL pairs:")
            for p in strong[:10]:
                print(f"    {p['coupling_status']:8}  {p['file_a']:<45} ↔  {p['file_b']:<45}"
                      f"  {p['coupling_strength']:.0%}  ({p['co_change_count']}× / "
                      f"{p['sessions_a']}|{p['sessions_b']} sessions)")
        elif pairs:
            print(f"  {len(pairs)} MODERATE pair(s) — below warning threshold")
        else:
            print("  No pairs above MODERATE threshold yet")
    else:
        print("Coupling: no history yet (run a session with mod active)")
    print()


def cmd_off():
    HOOKS_PAUSED.parent.mkdir(parents=True, exist_ok=True)
    HOOKS_PAUSED.write_text('paused\n')
    print("Modulario hooks paused. Run `mod` or `mod <project>` to resume.")
