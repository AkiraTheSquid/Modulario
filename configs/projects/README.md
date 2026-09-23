# Project registry

## Purpose

Defines projects eligible for always-on Modulario hook routing. Global Claude/Codex hooks never use `current-target.txt` to decide whether an agent edit belongs to Modulario; they match edited paths against enabled registry entries.

## Schema

Each `<name>.json` supports:

- `name`: stable CLI token (`mod delta-note`).
- `display_name`: project-picker label.
- `target_dir`: absolute project root.
- `enabled`: optional; defaults true.
- `priority`: cold-start picker order before usage history exists.
- Extra project-specific metadata is preserved and ignored by routing.

## Flow

1. Bare `mod` loads enabled entries, removes missing/duplicate roots, ranks them using `data/project-history.json`, then opens curses picker.
2. Pre/PostToolUse adapters choose deepest registry root containing changed path or hook `cwd`.
3. Post hook records provider/session touched files for matched project only.
4. Stop hook loads only matching provider/session scopes across registered projects.

## Invariants

- Nested projects allowed; deepest matching root wins.
- Missing directories never appear in picker or hook routing.
- Runtime usage history belongs under `data/`, never in these checked-in configs.
- Global hook presence does not imply global scanning; registry match required.
