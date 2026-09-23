"""`mod agents` — read and set how many subagents a session may spawn.

The number lives in configs/stopgate.json (`max_agents`) and is enforced by
~/.claude/hooks/pretooluse_guard.py, which spends one per Agent/Task/Workflow
call and blocks once a session is out. 0 is the standing "never spawn
subagents" rule.

This is Seth's switch, not the agent's. The guard says as much and so does the
card at the top of it: an agent that raises its own allowance has defeated the
control, not used it.
"""
import json
import time
from pathlib import Path

import stopgate_config

LEDGER_PATH = Path(__file__).resolve().parent.parent / "data" / "state" / "agent_budget.json"


def _read_ledger():
    try:
        data = json.loads(LEDGER_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _show():
    limit = stopgate_config.max_agents()
    if limit == 0:
        print("Max agents: 0 — every subagent spawn is blocked.")
    else:
        print(f"Max agents: {limit} per session.")

    ledger = _read_ledger()
    if not ledger:
        print("No session has spent any of its budget yet.")
        return
    now = int(time.time())
    print("\nSpent so far:")
    for session, row in sorted(ledger.items(), key=lambda kv: -int(kv[1].get("ts", 0))):
        spent = int(row.get("spent", 0))
        mins = (now - int(row.get("ts", 0))) // 60
        left = max(0, limit - spent)
        print(f"  {session[:12]:14s} {spent} spent, {left} left   ({mins} min ago)")
    print("\n`mod agents reset` clears this ledger.")


def _set(raw):
    if not raw.isdigit():
        print(f"Not a count: {raw!r}. Give 0 or a positive integer.")
        return 1
    value = int(raw)
    cfg = stopgate_config.load()
    previous = cfg["max_agents"]
    cfg["max_agents"] = value
    stopgate_config.save(cfg)
    if value == 0:
        print(f"Max agents: {previous} → 0. Subagents are blocked again.")
    else:
        word = "subagent" if value == 1 else "subagents"
        print(f"Max agents: {previous} → {value}. Each session may now spawn {value} {word}.")
        print("Takes effect on the next spawn — no restart needed.")
    return 0


def _reset():
    if not LEDGER_PATH.exists():
        print("Nothing to reset — no budget has been spent.")
        return 0
    try:
        LEDGER_PATH.unlink()
    except OSError as exc:
        print(f"Could not clear the ledger: {exc}")
        return 1
    print("Budget ledger cleared. Every session starts from its full allowance.")
    return 0


def cmd_agents(args):
    if not args:
        _show()
        return 0
    if args[0] in ("reset", "clear"):
        return _reset()
    if args[0] in ("set", "-n", "--set"):
        if len(args) < 2:
            print("Usage: mod agents set <count>")
            return 1
        return _set(args[1])
    return _set(args[0])
