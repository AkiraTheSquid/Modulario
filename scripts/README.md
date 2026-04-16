# scripts

## Purpose
- The runtime engine of Modulario: every code-path that Claude/Codex hooks fire into lives here.
- Everything in this folder runs either from Claude/Codex hooks (`PostToolUse`, `Stop`) or from the `mod` CLI. The TUI never imports from here except via `stopgate_config`.

## Owns
- Structural analysis (LOC, DEPS, status band classification, red hotspots).
- The stop gate: blocking Claude or Codex from ending a turn until watches pass, cycles are broken, touched folder docs are filled, and files are under the LOC limit.
- Auto-creation of `README.md` and `watch.py` templates in every tracked folder, plus the `[DOC]` and `[WATCH]` nag loop.
- The PostToolUse `[Modulario]` summary block and all `[!] / [VIOLATION] / [coupling] / [ALERT]` lines surfaced to the active agent.
- Per-session state and coupling history (`*_touched.json`, coupling JSONL).
- The single source of truth for user-facing feature toggles (`stopgate_config.py` → `configs/stopgate.json`).

## Does NOT own
- The TUI rendering of analysis results — lives in `tui/` and only reads `state.json` + `stopgate_config`.
- Config defaults for thresholds (LOC/DEPS bands) — those live in `configs/thresholds.json`.
- Watch script *contents* for user projects — Modulario only generates the template; the user (or agent) fills in real checks.

## Key Files
- `modulario-analyze.py`: the analyzer. Walks the target, computes state, writes `state.json`, and (with `--print-summary`) emits the agent-facing PostToolUse block. Also does all folder auto-doc and auto-watch template creation.
- `stop_gate.py`: the Stop hook. Decides whether to block Claude or Codex from ending a turn. Reads `stopgate_config` and consults watches / cycles / touched-folder docs / oversized files.
- `stopgate_config.py`: single-file JSON-backed config for the stop gate **and** all agent-facing feature toggles (stored under the legacy `claude.*` key). Shared between `stop_gate.py`, `modulario-analyze.py`, and `tui/views/settings_page.py`. Any new toggle goes here first.
- `modulario-watch-runner.py`: executes every enabled `watch.py` in the active target and aggregates pass/fail.
- `modulario-hook.sh` / `modulario-analyze.sh`: shell wrappers the agent runtime invokes as hooks. Keep them dumb — real logic lives in the Python files.
- `counters.py`: LOC + DEPS counters per language. Pure functions, no I/O.
- `violations.py`: import-graph analysis — cycles, private access, relative-path rules.
- `import_alerts.py`: per-change import alerts shown in the summary block.
- `coupling_tracker.py`: rolling co-change tracker. Emits `[coupling]` hints after enough session data accumulates.
- `watch.py`: the watch script for this `scripts/` folder itself (same contract as the user-project template).

## Data & External Dependencies
- `configs/stopgate.json` — persisted toggles + `loc_limit`. Written by the TUI settings page, read by both `stop_gate.py` and `modulario-analyze.py`.
- `configs/thresholds.json` — LOC/DEPS band thresholds.
- `data/` — per-target `state.json`, `*_touched.json`, coupling JSONL, and per-session stop-gate state.
- `tmp/<session_id>` — session-scoped state for the stop gate (fix-attempt counter, `final_shown` flag).
- Claude/Codex hook contract: JSON payload on stdin, JSON response on stdout (`{"decision": "block", "reason": "..."}` or exit 0 to allow).

## How It Works (Flow)
1. The agent writes a file. `PostToolUse` fires `modulario-hook.sh` → `modulario-analyze.py --print-summary --changed-file <path>`.
2. The analyzer walks the target, diffs against the previous `state.json`, writes the new one, updates coupling history, and walks every folder to ensure a `README.md` and `watch.py` exist (gated by `claude.auto_create_readme` / `claude.auto_create_watch`).
3. It assembles the `[Modulario]` block from `build_claude_summary()` plus `[!]`, `[VIOLATION]`, `[coupling]`, `[DOC]`, `[WATCH]`, and import alerts — each section independently gated by a `claude.*` flag.
4. The block is printed as `hookSpecificOutput.additionalContext`, which the active agent appends to the tool result it sees on the next turn.
5. When Claude or Codex tries to end its turn, `Stop` fires `stop_gate.py`. If `claude.gate_master` is off, the gate allows immediately. Otherwise it runs each enabled check (watches, cycles, unfilled docs, oversized files) and either blocks with a fix-list or allows.
6. After `MAX_FIX_ATTEMPTS` blocked stops the gate flips `final_shown` in session state and allows all further stops for that session — the agent is told to summarize + report remaining failures instead of looping forever.

## Invariants & Constraints
- **Never import from `tui/`**. This folder is the runtime, the TUI is a read-only consumer. A circular dep between `scripts/` and `tui/` will break the hook.
- **`stopgate_config.load()` must always return a fully-populated dict.** Missing keys in `configs/stopgate.json` fall through to `DEFAULTS` — do not return `None` or raise; hooks run in minimal environments and must be crash-proof.
- **New agent-facing feature toggles always get added in three places in lockstep**: `stopgate_config.CLAUDE_FEATURES`, `tui/views/settings_page.py::_CLAUDE_LABELS`, and the actual gate check in `stop_gate.py` / `modulario-analyze.py`. Missing any of the three leaves a dead toggle or an ungated feature.
- **The stop gate must default to safe-allow on any unexpected error.** A crashing gate would wedge every Claude session on the machine. Wrap anything that can raise and fall back to `allow()`.
- **Auto-created templates must always start with their marker** (`<!-- modulario:template -->` for README, `# modulario:template` for watch.py). The marker is the state flag — removing it is how users declare the file filled.
- **Per-session state keys come from the hook payload's `session_id`.** Never reuse a single global state file — parallel agent sessions would clobber each other's fix-attempt counters.
- **Do not read `state.json` during the stop gate's blocking path** unless strictly necessary — stop hooks run on every turn-end and stat/read churn adds up. Prefer the session-scoped `tmp/` files.

## Extension Points
- **Adding a new agent-facing toggle**: add the key to `CLAUDE_FEATURES` in `stopgate_config.py`, a row to `_CLAUDE_LABELS` in `tui/views/settings_page.py`, and an `if claude_cfg.get("your_key", True):` guard at the emit/enforce site.
- **Adding a new blocking check to the stop gate**: add the key to `FEATURES` in `stopgate_config.py`, a row to `_FEATURE_LABELS` in the settings page, and the check logic in `stop_gate.py::main()` behind `enabled.get("your_key", True)`.
- **Adding a new language to LOC/DEPS counting**: extend `counters.py` with `count_loc_<lang>` and `count_deps_<lang>` functions and wire them into `modulario-analyze.py::walk_target()` via `SUPPORTED_EXTENSIONS`.
- **Adding a new alert line type to the summary block**: add it inside `build_claude_summary()` behind a new `cc.get("your_alerts", True)` guard and add the matching toggle per the first bullet.

## Known Issues, Recurring Bugs, and Pain Points (and How to Prevent Them)

- **Stale `state.json` breaks `mod graph`** — `ACTIVE`
  - When it happens: user runs `mod graph <file>` right after moving/renaming files without re-running `mod query`.
  - Symptom: graph shows ghost imports or missing fan-in entries.
  - Root cause: `mod graph` reads `state.json` directly for speed and doesn't trigger re-analysis.
  - Prevention/fix: always re-run `mod query <dir>` before trusting graph output on recently changed code. Documented in the top-level CLAUDE.md.

- **`watch.py` runs in a bare env and chokes on heavy parent-package imports** — `ACTIVE`
  - When it happens: a folder's `watch.py` does `from app_modules.db import X` and `app_modules/__init__.py` eagerly imports PySide6 / psycopg2 / Qt.
  - Symptom: `mod watch run` fails with `ModuleNotFoundError` even though the code is fine at runtime.
  - Root cause: the watch runner deliberately runs in a minimal environment; transitive imports pull in deps that aren't installed there.
  - Prevention/fix: guard checks with `_<dep>_available()` helpers; for pure-Python files that can't be imported in isolation (e.g. `from __future__ import annotations` + `@dataclass`), fall back to `open()` + text-grep for the required symbol instead of `importlib`.

- **Fix-attempt loop wedged a session that couldn't reach 0 failures** — `RESOLVED`
  - When it happens: a watch or a touched-folder doc could not be fixed (e.g. external system broken, user not around to decide).
  - Symptom: the agent kept retrying and blocking its own Stop forever.
  - Root cause: the gate had no escape hatch.
  - Prevention/fix: `MAX_FIX_ATTEMPTS` + `final_shown` flag in session state. After N blocked stops, the gate flips to allow-mode for the rest of the session and tells Claude to summarize + report remaining failures. Do not remove this escape hatch.

## Recent Changes
- 2026-04-15: Added Codex `Stop` hook installation so stopgate enforcement matches Claude, and updated provider-facing wording in the config/docs/TUI while keeping the legacy `claude.*` config key for compatibility.
- 2026-04-14: Added `claude.*` toggle section to `stopgate_config.py`, gated every Claude-facing emit/enforce site in `modulario-analyze.py` and `stop_gate.py`, and added a "Claude-facing features" section to the TUI settings page covering 11 toggles (master gate, summary block, red hotspots, threshold/violation/import/coupling alerts, doc & watch nags, README & watch.py auto-creation).
- 2026-04-14: Initial doc created.
