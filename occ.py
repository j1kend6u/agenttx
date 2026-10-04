#!/usr/bin/env python3
"""
AgentTx — toy optimistic concurrency control.
No overlayfs yet. Just: read-set, validate, commit-or-abort.
The "repo" is a plain directory. The "agents" are plain functions.
"""
import hashlib
import shutil
from pathlib import Path

REPO = Path.home() / "agentx" / "sandbox" / "repo"


def hash_file(p: Path) -> str:
    if not p.exists():
        return "MISSING"
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


class Txn:
    """One agent's transaction. Buffers writes until commit."""

    def __init__(self, name: str):
        self.name = name
        self.read_set: dict[str, str] = {}   # path -> hash at read time
        self.write_set: dict[str, str] = {}  # path -> content (buffered)
        self.aborted = False
        self.reason = None

    def read(self, path: str) -> str:
        full = REPO / path
        self.read_set[path] = hash_file(full)
        return full.read_text() if full.exists() else ""

    def write(self, path: str, content: str) -> None:
        self.write_set[path] = content  # buffered — NOT on disk yet

    def validate(self) -> bool:
        """Re-check every read against the current state of the repo."""
        for path, observed in self.read_set.items():
            current = hash_file(REPO / path)
            if current != observed:
                self.aborted = True
                self.reason = f"read of {path} invalidated (was {observed}, now {current})"
                return False
        return True

    def commit(self) -> bool:
        if not self.validate():
            print(f"  [{self.name}] ✗ ABORT — {self.reason}")
            return False
        for path, content in self.write_set.items():
            (REPO / path).write_text(content)
        print(f"  [{self.name}] ✓ COMMIT — wrote {sorted(self.write_set)}")
        return True


def reset_repo():
    if REPO.exists():
        shutil.rmtree(REPO)
    REPO.mkdir(parents=True)
    (REPO / "api.py").write_text("def get_user():\n    return 'alice'\n")
    (REPO / "client.py").write_text(
        "from api import get_user\n\nprint(get_user())\n"
    )


def show_repo(label: str):
    print(f"\n--- {label} ---")
    for p in sorted(REPO.iterdir()):
        body = p.read_text().rstrip().replace("\n", " | ")
        print(f"  {p.name}: {body}")


def run_case(title: str, interleave: bool):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")
    reset_repo()

    a = Txn("Agent A")
    b = Txn("Agent B")

    # Both agents read api.py (this is what makes them conflict)
    a_content = a.read("api.py")
    b.read("api.py")

    # Agent A: rename get_user -> fetch_user in api.py
    a.write("api.py", a_content.replace("get_user", "fetch_user"))

    # Agent B: write a new client that uses the OLD name
    b.write("client.py", "from api import get_user\n\nprint(get_user())\n")

    if interleave:
        # Concurrent: both ran to completion, now they race to commit
        print("Both agents ran in parallel. Committing in order A, B:")
        a.commit()
        b.commit()
    else:
        # Serial: A finishes and commits, then B runs
        print("Serial execution. A commits, then B runs and commits:")
        a.commit()
        # In serial mode, B would have re-read after A's commit — simulate that:
        b.read_set["api.py"] = hash_file(REPO / "api.py")
        b.commit()

    show_repo("Final repo state")


if __name__ == "__main__":
    print("AgentTx — toy OCC demo")
    print("Scenario: A renames an API, B writes a caller for the old name.")

    run_case("SERIAL — B runs after A commits (correct)", interleave=False)
    run_case("PARALLEL — A and B ran concurrently (AgentTx catches it)",
             interleave=True)

