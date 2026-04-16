# Modulario

**Structural health monitoring for AI-assisted codebases.**

Modulario is a CLI + TUI tool that watches your project as you (and your AI coding assistant) edit it, and tells you — in real time — when files are getting too big, too tangled, or too coupled to safely keep growing. It's designed to keep AI-generated code from quietly rotting into unmaintainable sprawl.

## Why it exists

When you code with an AI assistant, the assistant has no long-term memory of your architecture. Left alone, it will:

- Keep stuffing logic into the same file because that's where the last edit happened
- Add imports until a single module depends on half the codebase
- Drift away from the folder structure you originally designed
- Skip writing docs and health checks for new folders entirely

Modulario is the missing feedback loop. Every time a file is written, it scores the file on a LOC × DEPS diagonal (GREEN → LIME → YELLOW → ORANGE → RED), detects circular imports and boundary violations, and pipes the result back to the AI through a hook so the assistant sees the structural consequences of its own edits *immediately* — not three weeks later when you wonder why everything broke.

## What it gives you

- **Real-time structural feedback** after every file edit, surfaced directly to Claude Code / Codex via PostToolUse hooks
- **A live TUI** showing every file's status, the worst hotspots, and folder-level health
- **Auto-generated `README.md` and `watch.py`** templates in every folder, with persistent nags until they're filled in — so documentation and folder-level health checks actually get written
- **In-the-loop watch.py feedback for Claude Code** — once you fill in a folder's `watch.py`, it runs *between Claude Code's tool calls* via the PostToolUse hook, so a broken import or violated invariant is surfaced to Claude *before its next edit*, not after the session ends. This is the killer feature, and it's why **Claude Code is the recommended frontend** — Codex's hook model doesn't feed results back into the assistant's context the same way, so the live feedback loop is much weaker there
- **Boundary violation detection** — circular imports and private-API leaks across module boundaries
- **Import graph queries** (`mod graph <file>`) — fan-in and fan-out before you rename or split anything
- **Folder watch scripts** — per-folder health checks that run on every edit and fail loudly when an invariant breaks

## The core idea

You can't ask an AI to "write maintainable code." You have to *show* it, every single edit, what maintainable looks like in your repo right now. Modulario is that show-don't-tell layer.

## Status

Personal tool, actively used. Linux-first (uses `xclip`/`xsel` for clipboard, curses for the TUI). Python 3, no heavy dependencies.

## What's new — 2026-04-14

- **Stop-hook gate** — Claude Code can no longer end a turn while `mod watch run` is failing, cycles are detected, touched folders have unfilled `README.md`, or any file is over the LOC limit. Three fix attempts, then it releases with a forced summary.
- **Settings page** (`e` in the TUI) — toggle each stopgate check and edit the LOC limit live. Writes to `configs/stopgate.json`, shared with the hook.
- **`b` in the main view** — copy a plaintext bug report (cycles, private-API leaks, failing watches) to the clipboard.
- **Internal refactor** — `tui/` reorganized into `tui/core/` and `tui/views/`; the main entry point went from 820 LOC to 112. No user-facing keybinds changed.
