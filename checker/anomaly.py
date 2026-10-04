#!/usr/bin/env python3
"""
AgentTx — Elle-style history checker.

Input: a committed history: ordered list of (txn_id, op, key, value) events.
Output: whether the history is serializable, and any anomaly if not.

Serializability theorem (Papadimitriou 1979):
    A history is conflict-serializable iff its serialization graph is acyclic.

We build the graph, then detect cycles.
"""
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from typing import Any


@dataclass
class Event:
    txn: str
    op: str          # "read" | "write"
    key: str
    value: Any


def build_graph(history: list[Event]) -> dict[str, set[str]]:
    """Serialization graph: edge T1 -> T2 if T1 and T2 conflict on a key
    and T1's event came first in the history."""
    graph: dict[str, set[str]] = defaultdict(set)
    txns = {e.txn for e in history}
    for t in txns:
        graph[t]  # ensure every txn is a node

    # For each pair of events on the same key by different txns, where at
    # least one is a write, add an edge from the earlier to the later.
    for i, ei in enumerate(history):
        for ej in history[i + 1:]:
            if ei.txn == ej.txn:
                continue
            if ei.key != ej.key:
                continue
            if ei.op == "read" and ej.op == "read":
                continue  # read-read is not a conflict
            graph[ei.txn].add(ej.txn)
    return graph


def find_cycle(graph: dict[str, set[str]]):
    """DFS for a cycle. Returns the cycle as a list, or None."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}
    stack: list[str] = []

    def dfs(u):
        color[u] = GRAY
        stack.append(u)
        for v in graph[u]:
            if color[v] == GRAY:
                # found a back edge — cycle from v to u, then back
                idx = stack.index(v)
                return stack[idx:] + [v]
            if color[v] == WHITE:
                result = dfs(v)
                if result:
                    return result
        stack.pop()
        color[u] = BLACK
        return None

    for n in graph:
        if color[n] == WHITE:
            result = dfs(n)
            if result:
                return result
    return None


def check(history: list[Event]) -> dict:
    graph = build_graph(history)
    cycle = find_cycle(graph)
    return {
        "serializable": cycle is None,
        "cycle": cycle,
        "edges": {k: sorted(v) for k, v in graph.items() if v},
    }


# ---------- tests / demonstrations ----------

def show(label: str, history: list[Event]):
    print(f"\n--- {label} ---")
    for e in history:
        print(f"  {e.txn:4s} {e.op:5s} {e.key:12s} = {e.value}")
    result = check(history)
    print(f"  serializable: {result['serializable']}")
    if result["cycle"]:
        print(f"  ANOMALY: cycle {result['cycle']}")
    if result["edges"]:
        print(f"  edges: {result['edges']}")


def main():
    print("AgentTx — serialization graph checker")
    print("We test the checker itself against known anomalies, then feed it")
    print("histories that look like AgentTx commit logs.")

    # --- 1. Trivially serial: T1 then T2, no overlap ---
    show("SERIAL — no overlap", [
        Event("T1", "read",  "x", 0),
        Event("T1", "write", "x", 1),
        Event("T2", "read",  "y", 0),
        Event("T2", "write", "y", 2),
    ])

    # --- 2. Lost update: both read x, both write x ---
    # This is what a naive "last write wins" merge produces. NOT serializable.
    show("LOST UPDATE — both read x, both write x", [
        Event("T1", "read",  "x", 0),
        Event("T2", "read",  "x", 0),
        Event("T1", "write", "x", 1),
        Event("T2", "write", "x", 2),
    ])

    # --- 3. Write skew: both read x and y, each writes a different one ---
    # Classic example: two doctors on call. Both go off-call.
    # Each txn is consistent with what it read. Combined, invariant violated.
    show("WRITE SKEW — both read x+y, each writes a different one", [
        Event("T1", "read",  "x", 1),
        Event("T2", "read",  "y", 1),
        Event("T1", "read",  "y", 1),
        Event("T2", "read",  "x", 1),
        Event("T1", "write", "x", 0),
        Event("T2", "write", "y", 0),
    ])

    # --- 4. Reader-writer, no cycle, despite overlap ---
    show("READ-WRITE, acyclic", [
        Event("T1", "write", "x", 1),
        Event("T2", "read",  "x", 1),   # T2 read T1's write -> edge T1->T2
    ])

    # --- 5. AgentTx's own scenario ---
    # A wrote api.py (rename). B read api.py (grep hit it). A commits, B aborts.
    # So B is never in the history. Nothing to check. Trivially serial.
    show("AgentTx happy path — B aborted, only A committed", [
        Event("A", "read",  "api.py", "old"),
        Event("A", "write", "api.py", "new"),
    ])

    # --- 6. Counterfactual: what if AgentTx DIDN'T abort? ---
    # Suppose we skipped validation and committed B anyway. Then:
    show("COUNTERFACTUAL — if AgentTx hadn't aborted B", [
        Event("A", "read",  "api.py", "old"),
        Event("B", "read",  "api.py", "old"),   # stale read
        Event("A", "write", "api.py", "new"),
        Event("B", "write", "NOTES.md", "old name"),
    ])

    print("\n(Note: last one is acyclic by the graph alone because B writes a")
    print(" different key. Real anomalies need semantic constraints. This is")
    print(" why checkers alone don't catch everything — see the paper Elle for")
    print(" how to enrich histories with dependencies.)")


if __name__ == "__main__":
    main()
