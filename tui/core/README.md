# core

## Purpose
Non-UI backbone for the Modulario TUI: owns the shared mutable state, reads/writes the analyzer's state.json, runs watches, and computes the derived structures (folder metrics, activity diffs, bug reports) that the curses views render. Everything in here is curses-free so the views can be rewritten without touching the data layer.

## Owns
- Shared `TuiState` (thread-safe `data` dict + lock) and the transient `ViewState` namespace.
- State file I/O and analyzer invocation (`state_io.py`): reading state.json, re-running `modulario-analyze.py` when thresholds change, and the filesystem watcher that reloads when the state file is rewritten out-of-band.
- Derived metrics: folder rollups (`tree.build_folder_metrics`), tree flattening with collapse-set support (`tree.flatten_tree`), activity diffs against a baseline snapshot (`activity.py`).
- Watch scanning + execution (`watches.py`) including the `# modulario:template` marker detection that decides whether a watch is "unfilled".
- Bug report formatting (`bug_report.py`) — the plaintext dump the `b` key copies to the clipboard.
- Config paths (`config.py`) — single source of truth for where thresholds, state, and analyzer live.

## Does NOT own
- Any curses drawing or input handling — that's in `../views/`. Nothing in this folder may `import curses`.
- The analyzer itself (`../../scripts/modulario-analyze.py`). This folder only shells out to it.
- The stop-hook / stopgate logic (`../../scripts/stopgate_config.py`). Settings page reads that directly.
- Watch template content and the README/DOC template system — those live under `../../scripts/` and the Modulario CLI.

## Key Files
- `tui_state.py` — `TuiState` (shared dict + lock + analyzer orchestration) and `ViewState` (per-view scroll/cursor). The only place view handlers should reach for cross-view state.
- `state_io.py` — state.json path hashing (`state_path_for`), `load_state`, `load_history`, and `analyze_target` which shells out to `modulario-analyze.py`.
- `tree.py` — folder metrics aggregation + scrollable row flattening honoring the user's collapse set.
- `activity.py` — baseline-vs-current diffing for the "activity" overlay (added/removed LOC per file/folder).
- `watches.py` — `scan_watches`, `run_watches`, enable/disable/dismiss marker files, and the unfilled-template detection.
- `bug_report.py` — plaintext formatter for cycles, private-API violations, and failing watches.
- `utils.py` — `StateWatcher` (watchdog wrapper) and clipboard export helpers.
- `config.py` — constant paths. Intentionally tiny; don't let it grow into a settings loader.

## Data & External Dependencies
- **state.json** — written by `modulario-analyze.py`, consumed here. Schema: `files[]`, `import_graph`, `violations`, `thresholds`, `target_dir`.
- **_history.jsonl** — append-only analyzer run log, consumed by activity diffs.
- **watch.py scripts** — discovered by walking the target directory; run as subprocesses.
- **watchdog** (optional) — used by `StateWatcher` for live reloads. Code must tolerate import failure since watchdog isn't guaranteed to be installed.
- **clipboard** — `xclip` / `wl-copy` / `pbcopy` shelled out to from `utils.py`.

## How It Works (Flow)
1. Entry point builds a `TuiState`, which loads the state file for the current target and starts a `StateWatcher`.
2. View handlers call `TuiState` methods to read a consistent snapshot under the lock, then render from that snapshot.
3. When the user edits thresholds or switches target, `TuiState` re-invokes `analyze_target`, waits for the new state.json, and reloads.
4. Activity overlay: on demand, `activity_from_diff` compares the current `files[]` against a baseline snapshot captured earlier and returns per-file/per-folder deltas.
5. Watches view: `scan_watches` walks the target, `run_watches` executes enabled scripts in parallel subprocesses, results land back in the shared dict.

## Invariants & Constraints
- **No curses imports in this folder.** If a helper needs to know the terminal size, pass it in — don't reach into curses from core.
- **All mutation of `TuiState.data` happens under `state.lock`.** Readers may snapshot under the lock and then release; writers must hold it for the full update.
- **State file paths are derived from `realpath` + md5.** Don't switch to a different hashing scheme without migrating existing state files — users will lose their history and collapse-set.
- **Analyzer invocation is synchronous from the caller's perspective.** `analyze_target` blocks; if a view needs async reanalysis it must spawn its own thread.
- **`StateWatcher` must tolerate watchdog being absent.** Fall back silently; the TUI still works, just without live reloads.
- **`watches.py` SKIP_DIRS must stay in sync** with the analyzer's skip list — otherwise the TUI will show watches for folders the analyzer never scanned.

## Extension Points
- **New derived metric:** add a pure function in `tree.py` or a new module here, call it from `TuiState.snapshot()`, expose via `ViewState` if view-local.
- **New watch action (e.g. "run filtered subset"):** extend `watches.py` with the new function, then wire a keybind in `../views/watch_handler.py`.
- **New state.json field:** add the read in `state_io.load_state`, thread it through `TuiState`, and make sure `activity.py` tolerates its absence in older snapshots.

## Known Issues, Recurring Bugs, and Pain Points (and How to Prevent Them)

- **Stale state after analyzer crash** — `ACTIVE`
  - When it happens: `analyze_target` subprocess fails mid-run and leaves state.json untouched while `TuiState` has already cleared its in-memory cache.
  - Symptom: TUI shows empty rows until the user forces a reload.
  - Prevention/fix: always re-read from disk after a failed analyzer call; never trust in-memory state alone after an invocation attempt.
  - Status: `ACTIVE`.

- **Watchdog double-fire** — `ACTIVE`
  - When it happens: some editors write-and-rename, firing two filesystem events for one logical save.
  - Symptom: `StateWatcher` callback runs twice, causing a flash of duplicate reloads.
  - Prevention/fix: debounce in the callback (currently the TUI just redraws, which is cheap enough to live with).
  - Status: `ACTIVE` — low impact.

## Recent Changes
- 2026-04-14: Initial doc filled in.
