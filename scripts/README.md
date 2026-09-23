# scripts

## Purpose
- The runtime engine of Modulario: every code-path that Claude/Codex hooks fire into lives here.
- Everything in this folder runs either from Claude/Codex hooks (`PreToolUse`, `PostToolUse`, `Stop`) or from the `mod` CLI. The TUI never imports from here except via `stopgate_config`.

## Owns
- Structural analysis (LOC, DEPS, status band classification, red hotspots).
- The Codex pre-edit gate that blocks edits to files already over the hard LOC limit.
- The stop gate: blocking Claude or Codex from ending a turn until watches pass, cycles are broken, touched folder docs are filled, and files are under the LOC limit.
- Auto-creation of `README.md` and `watch.py` templates in every tracked folder, plus the `[DOC]` and `[WATCH]` nag loop.
- The PostToolUse `[Modulario]` summary block and all `[!] / [VIOLATION] / [coupling] / [ALERT]` lines surfaced to the active agent.
- Per-session state and coupling history (`*_touched.json`, coupling JSONL).
- Provider-aware hook payload normalization, Git pre/post snapshots, and registry routing.
- The single source of truth for user-facing feature toggles (`stopgate_config.py` → `configs/stopgate.json`).

## Does NOT own
- The TUI rendering of analysis results — lives in `tui/` and only reads `state.json` + `stopgate_config`.
- Config defaults for thresholds (LOC/DEPS bands) — those live in `configs/thresholds.json`.
- Ignore-path persistence (`skip_dirs`) and `.gitignore` mirroring — exposed through `mod ignore ...`.
- Watch script *contents* for user projects — Modulario only generates the template; the user (or agent) fills in real checks.

## Key Files
- `modulario-analyze.py`: the analyzer. Walks the target, computes state, writes `state.json`, and (with `--print-summary`) emits the agent-facing PostToolUse block. Also does all folder auto-doc and auto-watch template creation.
- `stop_gate.py`: the Stop hook. Decides whether to block Claude or Codex from ending a turn. Reads `stopgate_config` and consults watches / cycles / touched-folder docs / oversized files.
- `stopgate_config.py`: single-file JSON-backed config for the stop gate **and** all agent-facing feature toggles (stored under the legacy `claude.*` key). Shared between `stop_gate.py`, `modulario-analyze.py`, and `tui/views/settings_page.py`. Any new toggle goes here first.
- `modulario-watch-runner.py`: executes every enabled `watch.py` in the active target and aggregates pass/fail.
- `modulario-hook.sh` / `modulario-analyze.sh`: shell wrappers the agent runtime invokes as hooks. Keep them dumb — real logic lives in the Python files.
- `post_tool_hook.py`: PostToolUse orchestrator. Routes changed paths to registered projects, analyzes, runs scoped watches, and merges hook context.
- `hook_scope.py`: Claude/Codex payload adapter plus Git dirty-file fingerprint snapshots for Bash tools.
- `session_scope.py`: provider/session touched-file persistence and one-hop import graph expansion.
- `project_registry.py`: enabled-project loading, deepest-root matching, usage history, and picker ranking.
- `folder_templates.py`: README/watch template text kept outside analyzer to preserve its LOC ceiling.
- `pre_edit_gate.py`: provider-neutral `PreToolUse` snapshot + hard LOC edit gate.
- `counters.py`: LOC + DEPS counters per language. Pure functions, no I/O.
- `violations.py`: import-graph analysis — cycles, private access, relative-path rules.
- `import_alerts.py`: per-change import alerts shown in the summary block.
- `coupling_tracker.py`: rolling co-change tracker. Emits `[coupling]` hints after enough session data accumulates.
- `run_critic.py`: reciprocal cross-model runner. Claude diffs go to Codex; Codex diffs go to Fable 5. Runs one reviewer from an isolated temporary directory, blocks nested reviews, fails closed on oversized/incomplete reviews, and persists normalized feature/refactor notes through `../critic_store.py`.
- `watch.py`: the watch script for this `scripts/` folder itself (same contract as the user-project template).

## Data & External Dependencies
- `configs/stopgate.json` — persisted toggles + `loc_limit`. Written by the TUI settings page, read by both `stop_gate.py` and `modulario-analyze.py`.
- `configs/thresholds.json` — LOC/DEPS band thresholds.
- `data/` — per-target `state.json`, `*_touched.json`, coupling JSONL, and per-session stop-gate state.
- `tmp/<session_id>` — session-scoped state for the stop gate (fix-attempt counter, `final_shown` flag).
- Claude/Codex hook contract: JSON payload on stdin, JSON response on stdout (`{"decision": "block", "reason": "..."}` or exit 0 to allow).

## How It Works (Flow)
1. `PreToolUse` fires `pre_edit_gate.py`. Direct edit paths get LOC-gated; Bash tools snapshot dirty/untracked Git fingerprints for matched project `cwd`.
2. `PostToolUse` fires `modulario-hook.sh` → `post_tool_hook.py`. Direct paths plus pre/post Git changes are matched against enabled project roots. No match → silent exit.
3. The analyzer walks the target, diffs against the previous `state.json`, writes the new one, updates coupling history, and walks every folder to ensure a `README.md` and `watch.py` exist (gated by `claude.auto_create_readme` / `claude.auto_create_watch`).
4. It assembles the `[Modulario]` block from `build_claude_summary()` plus `[!]`, `[VIOLATION]`, `[coupling]`, `[DOC]`, `[WATCH]`, and import alerts — each section independently gated by a `claude.*` flag.
5. The block is printed as `hookSpecificOutput.additionalContext`, which the active agent appends to the tool result it sees on the next turn.
6. `Stop` loads touched state keyed by `provider:session_id`. It checks only projects touched by that agent session; watches, cycles, and oversized files must intersect touched files plus direct import fan-in/fan-out.
7. After `MAX_FIX_ATTEMPTS` blocked stops the gate flips `final_shown` in session state and allows all further stops for that session — the agent is told to summarize + report remaining failures instead of looping forever.

## Invariants & Constraints
- **Never import from `tui/`**. This folder is the runtime, the TUI is a read-only consumer. A circular dep between `scripts/` and `tui/` will break the hook.
- **`stopgate_config.load()` must always return a fully-populated dict.** Missing keys in `configs/stopgate.json` fall through to `DEFAULTS` — do not return `None` or raise; hooks run in minimal environments and must be crash-proof.
- **New agent-facing feature toggles always get added in three places in lockstep**: `stopgate_config.CLAUDE_FEATURES`, `tui/views/settings_page.py::_CLAUDE_LABELS`, and the actual gate check in `stop_gate.py` / `modulario-analyze.py`. Missing any of the three leaves a dead toggle or an ungated feature.
- **The stop gate must default to safe-allow on any unexpected error.** A crashing gate would wedge every Claude session on the machine. Wrap anything that can raise and fall back to `allow()`.
- **Auto-created templates must always start with their marker** (`<!-- modulario:template -->` for README, `# modulario:template` for watch.py). The marker is the state flag — removing it is how users declare the file filled.
- **Per-session state keys use `provider:session_id`.** Claude/Codex sessions cannot inherit each other's scope or retry counters.
- **Do not read `state.json` during the stop gate's blocking path** unless strictly necessary — stop hooks run on every turn-end and stat/read churn adds up. Prefer the session-scoped `tmp/` files.

### `watch.py` design standard

- Keep watches structural, deterministic, dependency-light, and normally below one second. Browser, DB, network, and long workflow coverage belongs in the target project's test/check suite.
- Keep the generated `__main__` harness: each check runs independently and failures print `FAIL <check_name>: <reason>`. Every assertion needs a useful message.
- Pin stable contracts: importability, public exports, required files, data-safety guards, and forbidden dependency directions. Avoid cache-buster strings, exact statement formatting, CSS pixel values, and implementation trivia.
- Test behavior through the real production module with stubbed boundaries. Do not copy production logic into `watch.py`; link the folder README to the deeper behavioral check instead.
- `mod watch run` is interactive and prints totals. Hook execution stays quiet on PASS and emits only actionable FAIL lines.

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
- 2026-07-28: Added `max_agents` to `stopgate_config.py` (default `0`) and taught `~/.claude/hooks/pretooluse_guard.py` to read it. The no-subagents rule is now a per-session budget rather than a flat block: each `Agent`/`Task`/`Workflow` call spends one and the ledger lives in `data/state/agent_budget.json` keyed by Claude's `session_id` (entries older than a week are pruned). `0` keeps the standing "never spawn subagents" behavior, and the guard's config read fails CLOSED — an unreadable or missing config means 0, because a missing config is not permission. Editing the number is Seth's call — `mod agents [count|reset]` (`cli_agents.py`) or the TUI settings row; the agent must not raise its own allowance. Note that the two surfaces can fight: the settings page holds the whole config in memory and `save()` writes all of it, so a TUI left open will overwrite a CLI change the next time anything is toggled in it. Close the TUI before setting the value from the shell.
- 2026-07-18: Documented watcher design standards; interactive `mod watch run` now reports truthful totals, preserves quiet hook output, propagates failures, and surfaces useful assertion reasons.
- 2026-04-15: Added Codex `Stop` hook installation so stopgate enforcement matches Claude, and updated provider-facing wording in the config/docs/TUI while keeping the legacy `claude.*` config key for compatibility.
- 2026-04-14: Added `claude.*` toggle section to `stopgate_config.py`, gated every Claude-facing emit/enforce site in `modulario-analyze.py` and `stop_gate.py`, and added a "Claude-facing features" section to the TUI settings page covering 11 toggles (master gate, summary block, red hotspots, threshold/violation/import/coupling alerts, doc & watch nags, README & watch.py auto-creation).
- 2026-04-14: Initial doc created.
