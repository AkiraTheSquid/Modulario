# tui

## Purpose
The interactive terminal UI for Modulario — a live dashboard that shows a project's structural health (LOC/DEPS bands, folder rollups, import violations, watch results) and lets the user reanalyze, edit thresholds, switch targets, and manage folder-level watch scripts without leaving the terminal. It's the human-facing front end for the data produced by `scripts/modulario-analyze.py`.

## Owns
- `modulario-tui.py` — the entry point and outer curses loop. Owns the `wrapper()` call, the per-view dispatch, the `dirty` flag lifecycle, and the flash-message timer.
- The split between non-UI backbone (`core/`) and curses rendering/input (`views/`). That split is the key architectural rule of this folder — see the subfolder READMEs for why.
- The TUI's own watch script (`watch.py`) that health-checks the views+core structure.

## Does NOT own
- The analyzer (`../scripts/modulario-analyze.py`) — the TUI only shells out to it via `core/state_io.py`.
- The stop-hook / stopgate configuration surface (`../scripts/stopgate_config.py`). The TUI edits this file through the settings page, but the hook itself runs outside the TUI process.
- The `mod` CLI entry points (`../bin/mod`, `../scripts/mod-*.py`). Those are separate executables; the TUI is one of several front ends that can be launched.
- README/watch.py template generation — that lives in the analyzer/CLI layer.

## Key Files
- `modulario-tui.py` — curses entry point. Initializes colors, builds the initial `TuiState`, then runs the outer loop: read snapshot under lock → call the current view's handler → check return value → repeat.
- `core/` — non-UI backbone. Shared state, state.json I/O, analyzer invocation, derived metrics, watch execution, bug report formatting. Zero curses. See `core/README.md`.
- `views/` — curses drawing and input handlers, one pair per view. The only folder allowed to `import curses`. See `views/README.md`.
- `watch.py` — this folder's own health check; asserts the core/views split.

## Data & External Dependencies
- **state.json** produced by `../scripts/modulario-analyze.py`, keyed by md5 of realpath(target). Read via `core/state_io.py`.
- **_history.jsonl** — analyzer run log, consumed by the activity overlay.
- **configs/thresholds.json** — LOC/DEPS band edges. Edited live from the main view, re-triggers analyzer.
- **configs/stopgate.json** — stop-hook feature toggles. Edited from the settings page; the stop hook reads the same file out-of-process.
- **watchdog** (optional) — live state.json reloads. Code tolerates its absence.
- **curses** — Python stdlib. No ncurses extensions used, so it runs on any POSIX terminal.

## How It Works (Flow)
1. `modulario-tui.py` reads `configs/current-target.txt`, builds a `TuiState`, and enters `curses.wrapper(main)`.
2. `main` initializes colors, creates a `ViewState`, and enters the outer loop.
3. Each iteration: snapshot shared data under `state.lock`, dispatch to the current view's handler, draw if `dirty`, read one keypress, mutate view/state, return.
4. View switches happen by mutating `view.mode`; the next iteration dispatches to the new handler.
5. Long-running work (analyzer runs, run-all watches) is spawned on background threads that update shared state under the lock; the outer loop redraws on the next tick.
6. Settings page is a blocking sub-loop that returns control only when the user exits.

## Invariants & Constraints
- **`core/` must not import `curses`.** The single most important architectural rule in the TUI. Violating it makes core untestable headlessly.
- **`views/` must not read state files or shell out to the analyzer directly.** Route everything through `core/tui_state.TuiState`.
- **All mutation of `state.data` is gated by `state.lock`.** Readers snapshot-then-release; background threads take the lock for every write.
- **Never `getch()` while holding `state.lock`.** Deadlocks run-all watches which need the lock to report progress.
- **Always use `views/display.safe_addstr`** — raw `addstr` at the terminal's bottom-right corner raises a curses error.
- **State file paths hash `realpath(target)` with md5.** Don't change the hashing scheme without a migration.

## Extension Points
- **New view:** add `views/<name>_view.py` + `views/<name>_handler.py`, register the mode in `modulario-tui.py`'s dispatch.
- **New derived metric:** add a pure function in `core/`; expose via `TuiState.snapshot()`.
- **New stopgate toggle:** add the key to `../scripts/stopgate_config.py` first, then a row to `_FEATURE_LABELS` in `views/settings_page.py`.
- **New keybind:** add a branch in the relevant `*_handler.py` keypress dispatch. For string input, use `views/input_prompt.prompt_input`.

## Known Issues, Recurring Bugs, and Pain Points (and How to Prevent Them)

- **`sys.path` shim for `stopgate_config`** — `ACTIVE`
  - When it happens: `views/settings_page.py` inserts `../../scripts` into `sys.path` to import the stop-hook config module.
  - Symptom: moving `tui/` or renaming `scripts/` crashes the settings page with `ModuleNotFoundError`.
  - Prevention/fix: update the path expression in `settings_page.py` if either folder moves.
  - Status: `ACTIVE`.

- **Analyzer crash leaves stale state** — `ACTIVE`
  - Symptom: empty rows until the user triggers another reload.
  - Prevention/fix: `core/state_io` always re-reads from disk after a failed invocation.
  - Status: `ACTIVE` — low impact.

## Recent Changes
- 2026-04-14: Initial doc filled in.
