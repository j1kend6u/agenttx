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

Working MVP. All core ideas implemented:

- Overlayfs snapshots — each transaction gets its own copy-on-write view
- OCC validation — read-set re-probing at commit
- Command probes — grep/test output counts as a read (git can't see these)
- Memoized partial replay — re-executes only steps downstream of an invalidated probe
- Rebase — aborted transactions re-open against the new head and replan
- Elle-style checker — verifies the committed history has no cycle

## Demo

    $ python3 agenttx_demo.py
    --- Commit A ---
      A committed. New head: snap_A1
    --- Commit B ---
      B ABORT: read of cmd:grep...invalidated
      [B] REPLAN: rebasing against snap_A1, then re-running
      B committed after replan. New head: snap_B2
    --- Elle checker on committed history ---
      6 events in log
      serializable: True
      edges: {'A': ['B']}

Agent B never opened api.py. Its only knowledge of the API came from a
grep. Git worktrees would merge B's change cleanly and leave main broken.
AgentTx caught it, aborted B, rebased B against A's snapshot, and let B
re-run with correct information. The checker verified the result is
serializable.

## Known limits

- client.py still calls get_user after A's rename. No agent claimed to fix
  it; a third agent "update callers" would close this. This is a workload
  property, not a system bug.
- The checker catches concurrency anomalies (cycles in the serialization
  graph). It does not catch semantic violations on distinct keys — see
  docs/semantics.md for why.
- Nondeterministic command output is currently treated as deterministic.
  Opaque probes are designed but not implemented.

## Layout

    agentx/
      occ.py         toy OCC validator (this week)
      sandbox/       scratch repo for the toy demo
      docs/          semantics and transaction model

## Honest limits

See docs/semantics.md. Short version: AgentTx gives serializability
RELATIVE TO THE TRACKED READ-SET. It makes no claim about reads the
tracer did not observe, or about the outside world.

## Try it

    git clone https://github.com/j1kend6u/agenttx.git
    cd agenttx
    sudo apt install -y python3
    python3 agenttx_demo.py

Requires Linux with overlayfs (WSL2 is fine). Expects sudo for mount/umount.
