# AgentTx

Serializable transactions for parallel AI agents.

## Problem

Developers run multiple coding agents on one repo. Git worktrees isolate
them but don't make them correct. Agent A renames an API while Agent B
writes code against the old name. Both pass their own tests, the textual
merge succeeds, and main breaks. Discard-and-redo wastes tokens and time.

## Thesis

Treat each agent run as a database transaction over a real environment.
Track what it read, validate at commit, and re-execute only what a
concurrent commit invalidated.

## Status

Pre-alpha. Weeks 1-2 of a 6-8 week MVP.

What works today:
- Overlayfs snapshot prototype (base + upper write layer + merged view)
- Toy OCC validator on a plain directory: read-set, value-based validation,
  commit-or-abort, and abort-with-replan
- Reproduced the "rename API vs new caller" conflict; AgentTx catches it
  and recovers without discarding the transaction

What's next:
- Command probes (trace `grep`, `pytest` output as reads)
- DAG-based partial replay (re-execute only invalidated steps)
- Wrap one real CLI agent (final demo)

## Demo

Scenario: two agents run in parallel against the same repo.

    [Agent A] ✓ COMMIT — wrote ['api.py']
    [Agent B] ✗ ABORT — read of api.py invalidated (was 13b94dd6..., now b6193698...)
    [Agent B] ↻ REPLAN — re-reading invalidated probes...
    [Agent B] ✎ adapting plan to new API name
    [Agent B] ✓ COMMIT — wrote ['client.py']

Final state: both sides of the rename are consistent. Neither agent's work
was thrown away. A git-worktree baseline silently leaves main broken in
the same scenario.

## Layout

    agentx/
      occ.py         toy OCC validator (this week)
      sandbox/       scratch repo for the toy demo
      docs/          semantics and transaction model

## Honest limits

See docs/semantics.md. Short version: AgentTx gives serializability
RELATIVE TO THE TRACKED READ-SET. It makes no claim about reads the
tracer did not observe, or about the outside world.
