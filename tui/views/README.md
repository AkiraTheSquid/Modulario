# views

## Purpose
Curses rendering + keyboard-event handling for the Modulario TUI. This is the only folder in the TUI that is allowed to touch `curses`. Each view (main matrix+tree, ranked list, watch list, settings page) has a "draw" half and a "handle" half, so rendering can be re-skinned without rewriting input dispatch and vice versa.

## Owns
- All curses drawing code: color pair setup, safe-addstr wrappers, the triangle matrix, file tree rows, ranked list rows, watch list, settings page.
- Per-view event handlers (`*_handler.py`) — one outer-loop iteration per call, returning `None` to keep running or `'break'` to exit.
- The blocking single-line text prompt (`input_prompt.py`) used for threshold edits and target-directory switches.
- The stopgate settings page — loads/saves via `scripts/stopgate_config.py`, shared with the stop hook so the TUI and the hook agree on what is enabled.

## Does NOT own
- Shared mutable state, state.json parsing, or analyzer invocation — those live in `../core/`. Handlers reach into `core` via the `TuiState` passed into them; they must not read state files directly.
- Watch execution logic — `../core/watches.py` owns `scan_watches` / `run_watches`. The watch handler only schedules those calls and displays results.
- The stop hook itself — this folder only edits the config it reads.
- The entry point (`../modulario-tui.py`) — this folder provides the handlers it dispatches into, but does not own the outer curses loop or the `wrapper()` call.

## Key Files
- `display.py` — color pair constants, the main-view drawer (`draw_main_view`), watch-view drawer, and `safe_addstr`. Everything in here is stateless drawing.
- `main_handler.py` — main view event loop iteration: matrix + tree + footer, then a keypress dispatch for navigation, threshold edits, clipboard copy, bug report, settings page, reanalyze, folder switch.
- `ranked_view.py` + `ranked_handler.py` — the sorted-file views (LOC, DEPS, priority). Split for the same draw-vs-handle reason.
- `watch_handler.py` — watch list view: scroll, enable/disable, run-one, run-all. Uses a background thread for run-all so the UI stays responsive; callbacks close over `state` and mutate under `state.lock`.
- `settings_page.py` — blocking sub-loop triggered by `e`. Edits `configs/stopgate.json` via `stopgate_config.py`. The stop hook reads the same file, so changes here take effect on the next Claude Stop event.
- `input_prompt.py` — reusable blocking text prompt. Centralized so cursor/timeout setup is done in one place.

## Data & External Dependencies
- **`TuiState`** from `core/tui_state.py` — all handlers take it as their first arg and hold `state.lock` for any cross-thread mutation.
- **`stopgate_config`** from `../../scripts/` — loaded via a `sys.path.insert` hack at the top of `settings_page.py`. The path shim is load-bearing; the stop hook lives outside the TUI package and can't be imported normally.
- **curses** — all drawing goes through `display.safe_addstr` to avoid the "addstr past EOL raises" footgun.
- **Clipboard** — `core/utils.build_text_dump` + `copy_to_clipboard` invoked from `main_handler` on key `c`.

## How It Works (Flow)
1. The outer loop in `modulario-tui.py` calls `handle_<view>_view(stdscr, state, view, snap, dirty, flash_msg, h)` once per iteration.
2. If `dirty`, the handler calls its drawer; otherwise it skips the redraw.
3. The handler calls `stdscr.getch()` to block on input.
4. It mutates `view` for scroll/cursor changes and `state.data` (under `state.lock`) for anything shared.
5. It returns `None` to keep running, `'break'` to exit, or changes `view.mode` to switch to a different handler on the next iteration.
6. Settings page is the exception: it runs its own inner loop via `run_settings_loop` and returns only when the user exits the page.

## Invariants & Constraints
- **This is the only folder that may `import curses`.** Adding curses imports to `core/` breaks the separation and makes `core/` untestable outside a terminal.
- **Handlers must be re-entrant per iteration.** Each call is one loop tick; don't stash curses state across calls — use `view` / `state` for anything that must persist.
- **All access to `state.data` is gated by `state.lock`.** Reading is cheap; take the lock, snapshot what you need, release, then draw.
- **Never call `stdscr.getch()` while holding `state.lock`.** A background thread may need the lock to report progress; blocking input while holding it can deadlock run-all.
- **`safe_addstr` over raw `addstr`.** Curses raises when you write to the bottom-right corner; always use the wrapper.
- **Settings page writes must go through `stopgate_config`.** Don't hand-roll JSON writes — the stop hook has its own schema expectations.

## Extension Points
- **New view:** add `new_view.py` (pure drawing) and `new_handler.py` (event loop iteration), then register the mode in `modulario-tui.py`'s dispatch.
- **New keybind on an existing view:** add a branch in the relevant handler's keypress dispatch and, if it needs a string input, use `prompt_input`.
- **New settings toggle:** add a feature key to `stopgate_config.py` first, then add a row to `_FEATURE_LABELS` in `settings_page.py`. The stop hook picks it up automatically.
- **New color band:** extend the `FILE_CPAIR` / `FILE_DOT` / `STATUS_LETTER` maps in `display.py` and the matching `_*` maps in `ranked_view.py` — these are intentionally duplicated so the ranked view can be re-skinned without touching main.

## Known Issues, Recurring Bugs, and Pain Points (and How to Prevent Them)

- **Terminal resize mid-draw** — `ACTIVE`
  - When it happens: user resizes the terminal while a handler is drawing; `getmaxyx` returns stale values for the current frame.
  - Symptom: partial redraw, occasional "addwstr returned ERR" on the bottom row.
  - Prevention/fix: always go through `safe_addstr`; accept one frame of garbled output, the next iteration will fetch the new size.
  - Status: `ACTIVE`.

- **`sys.path` shim in `settings_page.py`** — `ACTIVE`
  - When it happens: moving the TUI folder or renaming `scripts/` breaks the `sys.path.insert` that imports `stopgate_config`.
  - Symptom: settings page crashes on open with `ModuleNotFoundError`.
  - Prevention/fix: if you move things, update the `Path(__file__).resolve().parent.parent.parent / "scripts"` expression. Consider turning Modulario into a proper package if this becomes painful.
  - Status: `ACTIVE` — cost of avoiding a package refactor.

- **Run-all watches blocks UI until first result** — `RESOLVED`
  - Prior behavior: `run_watches` was called inline from the handler, freezing the TUI for seconds.
  - Fix: handler now spawns a thread and updates `state.data` under the lock as results come in.
  - Status: `RESOLVED`.

## Recent Changes
- 2026-04-14: Initial doc filled in.
