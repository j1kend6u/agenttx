#!/usr/bin/env python3
"""
AgentTx — end-to-end demo.

Two agents run in parallel against isolated overlayfs snapshots.
Each run is a Txn. Commits are logged. The Elle checker verifies the
final committed history is serializable.

Agent execution is pluggable:
  - Default: mock (deterministic, free)
  - Set AGENT_CMD to shell out to a real LLM CLI:
      AGENT_CMD='aider --yes --message-file /dev/stdin' python3 agenttx_demo.py
      AGENT_CMD='claude -p'                             python3 agenttx_demo.py
The CLI is invoked with cwd=txn.merged and the task prompt on stdin.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ---- reuse the Leg 6 machinery ------------------------------------------

from agenttx import Snapshot, Txn, SANDBOX, sh_ok, normalize
from checker.anomaly import Event, check


LOG_PATH = SANDBOX / "log.jsonl"


# ---- logging ------------------------------------------------------------

def log_event(txn_name: str, op: str, key: str, value):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(json.dumps({
            "txn": txn_name, "op": op, "key": key, "value": value,
        }) + "\n")


def log_txn_reads_writes(txn: Txn):
    """On commit, replay the transaction's recorded reads + writes into the log."""
    for key, observed in txn.reads.items():
        log_event(txn.name, "read", key, observed)
    for name in txn.writes:
        p = txn.upper / name
        content = p.read_text() if p.exists() else ""
        h = hashlib.sha256(content.encode()).hexdigest()[:12]
        log_event(txn.name, "write", f"file:{name}", h)


def load_history() -> list[Event]:
    if not LOG_PATH.exists():
        return []
    out = []
    for line in LOG_PATH.read_text().splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        out.append(Event(d["txn"], d["op"], d["key"], d["value"]))
    return out


# ---- pluggable agent backend -------------------------------------------

def run_agent(txn: Txn, task: str, scripted_actions: list[dict]):
    """Execute an agent inside txn.merged.
    If AGENT_CMD is set, shells out to it. Otherwise runs scripted_actions.
    """
    cmd = os.environ.get("AGENT_CMD")
    if cmd:
        print(f"  [{txn.name}] running external agent: {cmd}")
        r = subprocess.run(
            cmd, shell=True, cwd=str(txn.merged), input=task,
            capture_output=True, text=True,
        )
        if r.stdout.strip():
            print(f"    stdout: {r.stdout.strip()[:200]}")
        if r.returncode != 0:
            print(f"    exit {r.returncode}: {r.stderr.strip()[:200]}")
            if r.returncode == 127:
                print(f"    (hint: '{cmd.split()[0]}' not installed — "
                      f"unset AGENT_CMD to use the mock backend)")
        # After the external agent runs, treat all files it touched as writes.
        for p in txn.upper.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(txn.upper))
                if rel not in txn.writes:
                    txn.writes.append(rel)
        return

    # Mock backend: replay the scripted action list against the txn.
    for a in scripted_actions:
        kind = a["kind"]
        if kind == "read_file":
            txn.read(a["path"])
        elif kind == "cmd":
            txn.cmd(a["command"])
        elif kind == "write_file":
            txn.write(a["path"], a["content"])
        else:
            raise ValueError(f"unknown mock action: {kind}")


# ---- the demo -----------------------------------------------------------

def main():
    print("AgentTx — end-to-end demo")
    print("Two agents in parallel overlays. Elle checker verifies the result.")
    backend = "external (" + os.environ["AGENT_CMD"] + ")" if "AGENT_CMD" in os.environ else "mock (set AGENT_CMD for a real agent)"
    print(f"Backend: {backend}\n")

    # Clean slate
    sh_ok("sudo umount -l ~/agentx/sandbox/txn_*/merged 2>/dev/null")
    if SANDBOX.exists():
        sh_ok(f"sudo rm -rf {SANDBOX}")
    SANDBOX.mkdir(parents=True)
    LOG_PATH.write_text("")  # fresh log

    # Base snapshot
    s0 = Snapshot.create(SANDBOX / "snapshots" / "s0", {
        "api.py":    "def get_user():\n    return 'alice'\n",
        "client.py": "from api import get_user\n\nprint(get_user())\n",
        "NOTES.md":  "# Notes\n\n",
    })

    print(f"Head S0 created with {len(list(s0.path.iterdir()))} files.\n")

    # --- Both agents start against S0 -----------------------------------
    print("--- Opening two transactions ---")
    a = Txn("A", s0)
    b = Txn("B", s0)
    print(f"  A working dir: {a.merged}")
    print(f"  B working dir: {b.merged}")
    print(f"  Both see identical repos. Neither sees the other.\n")

    # --- Agent A: rename the API ----------------------------------------
    print("--- Agent A: rename get_user -> fetch_user ---")
    run_agent(a, "Rename get_user to fetch_user everywhere.", [
        {"kind": "read_file",  "path": "api.py"},
        {"kind": "cmd",        "command": "grep -rn 'get_user' . | sort"},
        {"kind": "write_file", "path": "api.py",
         "content": "def fetch_user():\n    return 'alice'\n"},
    ])
    print(f"  A read: {list(a.reads)}")
    print(f"  A wrote: {a.writes}\n")

    # --- Agent B: document the current API ------------------------------
    print("--- Agent B: document the API by reading grep output ---")
    run_agent(b, "Write a note describing the current API.", [
        {"kind": "cmd",        "command": "grep -rn 'def ' . | sort"},
        {"kind": "read_file",  "path": "NOTES.md"},
        {"kind": "write_file", "path": "NOTES.md",
         "content": "# Notes\n\nAPI: get_user lives in api.py\n"},
    ])
    print(f"  B read: {list(b.reads)}")
    print(f"  B wrote: {b.writes}\n")

    # --- Commit A ------------------------------------------------------
    print("--- Commit A ---")
    new_head, why = a.commit(s0)
    if new_head is None:
        print(f"  A ABORT: {why}")
        return
    log_txn_reads_writes(a)
    print(f"  A committed. New head: {new_head.path.name}\n")

    # --- Commit B ------------------------------------------------------
    print("--- Commit B ---")
    result, why = b.commit(new_head)
    if result is None:
        print(f"  B ABORT: {why}")
        print(f"  B's reads will not enter the log — the world did not change.\n")
        print(f"  [B] REPLAN: rebasing against {new_head.path.name}, then re-running")
        b.rebase(new_head)
        run_agent(b, "Write a note describing the current API.", [
            {"kind": "cmd",        "command": "grep -rn 'def ' . | sort"},
            {"kind": "read_file",  "path": "api.py"},
            {"kind": "write_file", "path": "NOTES.md",
             "content": "# Notes\n\nAPI: fetch_user lives in api.py\n"},
        ])
        result, why2 = b.commit(new_head)
        if result is None:
            print(f"  B REPLAN FAILED: {why2}")
            return
        print(f"  B committed after replan. New head: {result.path.name}\n")
        new_head = result
    else:
        print(f"  B committed. New head: {result.path.name}\n")
        new_head = result

    log_txn_reads_writes(b)

    # --- Final state ----------------------------------------------------
    print("--- Final head ---")
    for p in sorted(new_head.path.iterdir()):
        if p.is_file():
            body = p.read_text().rstrip().replace("\n", " | ")
            print(f"  {p.name}: {body}")

    # --- Checker --------------------------------------------------------
    print("\n--- Elle checker on committed history ---")
    history = load_history()
    print(f"  {len(history)} events in log")
    result = check(history)
    print(f"  serializable: {result['serializable']}")
    if result["cycle"]:
        print(f"  ANOMALY: {result['cycle']}")
    print(f"  edges: {result['edges']}")

    # --- Cleanup --------------------------------------------------------
    a.cleanup()
    b.cleanup()
    print("\nDone.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sh_ok("sudo umount -l ~/agentx/sandbox/txn_*/merged 2>/dev/null")
        raise
