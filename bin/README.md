<!-- modulario:template -->
# bin

## Purpose
- One or two sentences on what this folder is responsible for.
- Describe the business/domain concern, not the technical details.

## Owns
- List the main responsibilities this folder **does own**.
- Each item should be something that changes when this folder changes.

## Does NOT own
- List responsibilities that live elsewhere to prevent scope creep.
- Link to the other folder/module if relevant.

## Key Files
- `example.js`: short description of what this file is and when it runs.

## Data & External Dependencies
- What data models or types this area works with.
- What external services or libraries it directly touches.
- Any important shared modules it depends on.

## How It Works (Flow)
1. Brief step-by-step of the main flow.
2. Optional secondary flows if they are important.

## Invariants & Constraints
- Rules that **must** remain true.
- Performance or security constraints.
- "Never do X" type rules that are easy to forget.

## Extension Points
- How to add a new feature in this area.
- What file to start from when extending behavior.

## Known Issues, Recurring Bugs, and Pain Points (and How to Prevent Them)

- **Short name of issue** — `ACTIVE` or `RESOLVED`
  - When it happens: one line about the situation/context.
  - Symptom: what you see break.
  - Root cause: the underlying mistake or assumption.
  - Prevention/fix: the rule, pattern, or helper to use so it doesn't come back.
  - Status: `ACTIVE` = still a risk, `RESOLVED` = was an issue, now fixed (keep for history).

## Recent Changes
- 2026-04-14: Initial doc created.
