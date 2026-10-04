# Semantics

## What "serializable" means here

AgentTx provides serializability RELATIVE TO THE TRACKED READ-SET.

It guarantees:
- Any committed transaction is a function of a snapshot that did not change
  under its reads.

It does NOT guarantee anything about:
- reads the tracer did not observe
- the external world outside the effect gateway
- nondeterministic reads; these are handled conservatively

If a probe was not tracked, AgentTx makes no claim about it. This sentence
is the boundary of the project.

## Core abstraction: a probe

A read is a probe: a function from a snapshot to a value.

    probe(key, snapshot) -> hash

Kinds of probes:
- file:  key = "file:src/api.py",        value = hash of content
- cmd:   key = "cmd:grep -rn foo src/",  value = hash of normalized stdout/stderr/exit
- query: key = "query:db:SELECT ...",    value = hash of normalized rows

## Determinism

- DETERMINISTIC — re-running on the same snapshot yields the same value.
                  Used for exact validation. E.g. file contents.
- OPAQUE        — output may vary across runs. E.g. timing-sensitive tests,
                  network calls, anything with a clock. Conservative:
                  invalidated by ANY overlapping write in the interval.

## Commit protocol

Given a transaction T with base snapshot S0 and current head S1:

1. For each DETERMINISTIC probe p in T.read_set:
       if probe(p.key, S1) != p.observed:
           ABORT — a concurrent commit invalidated this read.
2. For each OPAQUE probe p in T.read_set:
       if any write in (S0, S1] overlaps p.key:
           ABORT.
3. Otherwise APPLY T's writes. New head = S1 + T.write_set.

This is optimistic concurrency control (OCC) with value-based validation.

## On abort

Abort does not discard the transaction. The write set is buffered, never
applied to the base. On abort:

1. Re-read invalidated probes against the current head.
2. Re-run the portion of the plan that depended on those reads.
3. Retry commit.

Currently replan is whole-transaction. The target is DAG-based partial
replay: memoize every tool call by (prompt_hash, input_hash), re-run only
the sub-DAG downstream of an invalidated read.

## Open questions

- Abort policy: full restart vs DAG-based partial replay?
- Commit sequencer lease model (single-writer, multi-reader? leases?)
- Snapshot isolation vs serializable modes: which anomalies does SI allow
  that are unacceptable for coding agents?
- How to prove isolation claims? TLA+ model of the commit protocol is planned.

## Prior art (as of writing)

- OCC / STM (Kung & Robinson, TL2): the theory we borrow.
- Salsa / rust-analyzer / Bazel / Shake: incremental computation via
  dependency DAGs and early cutoff — the shape of our replay planner.
- Git worktrees + merge queues: isolation without read-set validation.
- Saga / SagaLLM: compensation, not serializability.
- Durable execution (Temporal, DBOS): replay requires determinism, which
  is the exact problem we're trying to relax.
