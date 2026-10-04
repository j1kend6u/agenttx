#!/usr/bin/env python3
"""
AgentTx — toy OCC validator with command probes.
Leg 4: command output (grep, pytest) counts as a first-class read.
"""
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

REPO = Path.home() / "agentx" / "sandbox" / "repo"


def hash_file(p: Path) -> str:
    if not p.exists():
        return "MISSING"
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


def normalize(s: str) -> str:
    """Strip volatile bytes so semantically-identical outputs hash equal.
    This is where false conflicts are won or lost."""
    s = re.sub(r"\d+\.\d+s", "T", s)      # pytest timing
    s = re.sub(r"/tmp/\S+", "/tmp/X", s)  # temp paths
    return s.strip()


class Txn:
    def __init__(self, name: str):
        self.name = name
        self.read_set: dict[str, str] = {}
        self.write_set: dict[str, str] = {}
        self.aborted = False
        self.reason = None

    def read(self, path: str) -> str:
        self.read_set[f"file:{path}"] = hash_file(REPO / path)
        full = REPO / path
        return full.read_text() if full.exists() else ""

    def write(self, path: str, content: str) -> None:
        self.write_set[path] = content  # buffered

    def cmd(self, command: str) -> str:
        """Run a shell command. Its output becomes a read probe."""
        out = self._run(command)
        key = f"cmd:{command}"
        self.read_set[key] = hashlib.sha256(out.encode()).hexdigest()[:12]
        return out

    def _run(self, command: str) -> str:
        r = subprocess.run(
            command, shell=True, cwd=REPO,
            capture_output=True, text=True,
        )
        combined = r.stdout + "\n--\n" + r.stderr + f"\n[exit {r.returncode}]"
        return normalize(combined)

    def _probe(self, key: str) -> str:
        kind, rest = key.split(":", 1)
        if kind == "file":
            return hash_file(REPO / rest)
        if kind == "cmd":
            return hashlib.sha256(self._run(rest).encode()).hexdigest()[:12]
        raise ValueError(f"unknown probe kind: {kind}")

    def validate(self) -> bool:
        for key, observed in self.read_set.items():
            current = self._probe(key)
            if current != observed:
                self.aborted = True
                self.reason = (
                    f"read of {key} invalidated (was {observed}, now {current})"
                )
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

    # A reads api.py directly.
    a_content = a.read("api.py")

    # B does NOT read api.py. B's only view of the world is a grep.
    # This is the read git worktrees cannot see.
    print("  [Agent B] probe: grep -rn 'def get_user' . | sort")
    b.cmd("grep -rn 'def get_user' . | sort")

    # A renames the function.
    a.write("api.py", a_content.replace("get_user", "fetch_user"))

    # B writes a note based on what grep told it.
    b.write("NOTES.md", "API: get_user lives in api.py\n")

    if interleave:
        print("Both ran in parallel. Committing A, then B:")
        a.commit()
        if not b.commit():
            print(f"  [{b.name}] ↻ REPLAN — re-running invalidated probe...")
            b.cmd("grep -rn 'def get_user' . | sort")
            print(f"  [{b.name}] ✎ adapting plan to new API name")
            b.write_set["NOTES.md"] = "API: fetch_user lives in api.py\n"
            b.aborted = False
            b.reason = None
            b.commit()
    else:
        print("Serial execution. A commits, then B runs:")
        a.commit()
        b.cmd("grep -rn 'def get_user' . | sort")
        b.write_set["NOTES.md"] = "API: fetch_user lives in api.py\n"
        b.commit()

    show_repo("Final repo state")


if __name__ == "__main__":
    print("AgentTx — OCC with command probes")
    print("Scenario: A renames get_user; B writes a doc based on what grep told it.\n")
    run_case("SERIAL — B runs after A commits", interleave=False)
    run_case("PARALLEL — A and B ran concurrently", interleave=True)
