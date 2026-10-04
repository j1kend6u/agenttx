#!/usr/bin/env python3
"""
AgentTx — memoized partial replay, v2.

Lesson from v1: you can't know a step's read-set until you run it.
The fix: REMEMBER what each step read last time, re-probe those keys,
use the result as the cache signature.

This is exactly what Bazel does with depfiles and Salsa does with queries.
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
    s = re.sub(r"\d+\.\d+s", "T", s)
    s = re.sub(r"/tmp/\S+", "/tmp/X", s)
    return s.strip()


def probe(key: str) -> str:
    kind, rest = key.split(":", 1)
    if kind == "file":
        return hash_file(REPO / rest)
    if kind == "cmd":
        r = subprocess.run(rest, shell=True, cwd=REPO,
                           capture_output=True, text=True)
        raw = normalize(r.stdout + r.stderr + f"\n[exit {r.returncode}]")
        return hashlib.sha256(raw.encode()).hexdigest()[:12]
    raise ValueError(f"unknown probe kind: {kind}")


class Tracer:
    """Records what a step reads, so we can replay it later."""
    def __init__(self):
        self.reads: list[str] = []
        self.values: dict[str, str] = {}

    def _record(self, key: str):
        v = probe(key)
        self.reads.append(key)
        self.values[key] = v
        return v

    def read_file(self, path: str) -> str:
        self._record(f"file:{path}")
        full = REPO / path
        return full.read_text() if full.exists() else ""

    def run_cmd(self, command: str) -> str:
        self._record(f"cmd:{command}")
        r = subprocess.run(command, shell=True, cwd=REPO,
                           capture_output=True, text=True)
        return normalize(r.stdout + r.stderr + f"\n[exit {r.returncode}]")


class Step:
    def __init__(self, sid: str, fn, depends_on=()):
        self.id = sid
        self.fn = fn
        self.depends_on = list(depends_on)
        self.last_reads: list[str] = []       # probes touched last time
        self.last_values: dict[str, str] = {} # observed values last time

    def sig_if_rerun_now(self) -> str:
        """If this step re-ran now, reading the same keys as last time,
        what would its signature be? THIS is the memoization key."""
        payload = self.id + "|"
        for k in sorted(self.last_reads):
            payload += f"{k}={probe(k)};"
        return hashlib.sha256(payload.encode()).hexdigest()[:12]

    def sig_when_it_ran(self) -> str:
        payload = self.id + "|"
        for k in sorted(self.last_reads):
            payload += f"{k}={self.last_values[k]};"
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


class Plan:
    def __init__(self, name: str):
        self.name = name
        self.steps: list[Step] = []
        self.cache: dict[str, object] = {}   # sig_when_it_ran -> result

    def add(self, step: Step) -> Step:
        self.steps.append(step)
        return step

    def run(self, verbose=True):
        ran: set[str] = set()
        log = []
        for step in self.steps:
            # If a dependency re-ran this pass, its output may have changed.
            forced = any(d in ran for d in step.depends_on)

            if not step.last_reads:
                self._execute(step)
                ran.add(step.id)
                log.append((step.id, "executed (cold — never run)"))
                continue

            if forced:
                self._execute(step)
                ran.add(step.id)
                log.append((step.id, "executed (dep re-ran)"))
                continue

            sig = step.sig_if_rerun_now()
            if sig in self.cache:
                log.append((step.id, "skipped (cache hit)"))
                continue

            self._execute(step)
            ran.add(step.id)
            log.append((step.id, "executed (probe changed)"))
        if verbose:
            for sid, action in log:
                print(f"    {sid:20s} {action}")
        return log

    def _execute(self, step: Step):
        t = Tracer()
        result = step.fn(t)
        step.last_reads = t.reads
        step.last_values = t.values
        self.cache[step.sig_when_it_ran()] = result

    def first_invalidated(self):
        for step in self.steps:
            for k, old in step.last_values.items():
                now = probe(k)
                if now != old:
                    return step.id, k, old, now
        return None


# ---------- the demo ----------

def reset_repo():
    if REPO.exists():
        shutil.rmtree(REPO)
    REPO.mkdir(parents=True)
    (REPO / "api.py").write_text("def get_user():\n    return 'alice'\n")
    (REPO / "client.py").write_text(
        "from api import get_user\n\nprint(get_user())\n"
    )
    (REPO / "README.md").write_text("# My project\n")


def show_repo(label: str):
    print(f"\n--- {label} ---")
    for p in sorted(REPO.iterdir()):
        body = p.read_text().rstrip().replace("\n", " | ")
        print(f"  {p.name}: {body}")


def build_plan_B() -> Plan:
    p = Plan("B")

    def s1(t):
        return {"client_src": t.read_file("client.py")}

    def s2(t):
        # THE step that A's rename invalidates. B never opens api.py.
        return {"grep": t.run_cmd("grep -rn 'def get_user' . | sort")}

    def s3(t):
        # Analysis step. No direct reads — it consumes s2's output.
        return {"analysis": "grep confirms the API name"}

    def s4(t):
        # Drafts notes based on what's actually in api.py right now.
        api = t.read_file("api.py")
        name = "fetch_user" if "fetch_user" in api else "get_user"
        return {"notes": f"API: {name} lives in api.py\n"}

    def s5(t):
        # Completely independent work. Should survive replay untouched.
        return {"todo": t.read_file("README.md") + "\nTODO: ship it\n"}

    p.add(Step("s1_read_client", s1))
    p.add(Step("s2_grep",        s2))
    p.add(Step("s3_analyze",     s3, depends_on=["s2_grep"]))
    p.add(Step("s4_draft",       s4, depends_on=["s3_analyze"]))
    p.add(Step("s5_other",       s5))
    return p


def main():
    print("AgentTx — memoized partial replay (v2)")
    print("Agent B has 5 steps. Only the grep dependency should re-run.\n")
    reset_repo()

    print("=== COLD RUN — Agent B executes all 5 steps ===")
    b = build_plan_B()
    b.run()

    print("\n=== Agent A commits a rename in parallel ===")
    api = (REPO / "api.py").read_text()
    (REPO / "api.py").write_text(api.replace("get_user", "fetch_user"))
    print("  [Agent A] ✓ COMMIT — wrote ['api.py']")

    print("\n=== VALIDATE Agent B against the new head ===")
    inv = b.first_invalidated()
    if inv is None:
        print("  [Agent B] ✓ COMMIT (nothing invalidated)")
        return
    sid, k, old, now = inv
    print(f"  [Agent B] ✗ ABORT — step {sid} read {k}")
    print(f"            (was {old}, now {now})")

    print("\n=== REPLAY Agent B — same 5-step plan, warm cache ===")
    b.run()

    print("\n=== Final repo state ===")
    show_repo("after replay")


if __name__ == "__main__":
    main()
