#!/usr/bin/env python3
"""
AgentTx — Leg 6: integration.

Overlayfs snapshots meet the OCC validator.
Every transaction gets its own overlay mount. lowerdir = committed head.
upperdir = the transaction's private write buffer. Commit promotes upper
onto a new snapshot; abort throws it away.
"""
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

SANDBOX = Path.home() / "agentx" / "sandbox"


def sh_ok(cmd, **kw):
    return subprocess.run(cmd, shell=True, text=True,
                         capture_output=True, **kw)


def normalize(s):
    s = re.sub(r"\d+\.\d+s", "T", s)
    s = re.sub(r"/tmp/\S+", "/tmp/X", s)
    return s.strip()


class Snapshot:
    """Immutable committed state on disk."""

    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def create(cls, path: Path, files: dict) -> "Snapshot":
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
        for name, content in files.items():
            (path / name).write_text(content)
        return cls(path)

    def hash_file(self, name: str) -> str:
        p = self.path / name
        if not p.exists():
            return "MISSING"
        return hashlib.sha256(p.read_bytes()).hexdigest()[:12]

    def probe(self, key: str) -> str:
        kind, rest = key.split(":", 1)
        if kind == "file":
            return self.hash_file(rest)
        if kind == "cmd":
            r = sh_ok(rest, cwd=str(self.path))
            raw = normalize(r.stdout + r.stderr + f"\n[exit {r.returncode}]")
            return hashlib.sha256(raw.encode()).hexdigest()[:12]
        raise ValueError(f"unknown probe: {key}")


class Txn:
    """One agent's transaction, backed by an overlayfs mount."""

    _counter = 0

    def __init__(self, name: str, base: Snapshot):
        self.name = name
        self.base = base
        Txn._counter += 1
        self.tag = f"{name}{Txn._counter}"
        self.dir = SANDBOX / f"txn_{self.tag}"
        self.upper = self.dir / "upper"
        self.work = self.dir / "work"
        self.merged = self.dir / "merged"
        self.upper.mkdir(parents=True)
        self.work.mkdir()
        self.merged.mkdir()
        self.mounted = False
        self.reads: dict[str, str] = {}
        self.writes: list[str] = []
        self._mount()

    def _mount(self):
        opts = (
            f"lowerdir={self.base.path},"
            f"upperdir={self.upper},"
            f"workdir={self.work}"
        )
        r = sh_ok(f"sudo mount -t overlay overlay -o {opts} {self.merged}")
        if r.returncode != 0:
            raise RuntimeError(f"mount failed: {r.stderr.strip()}")
        self.mounted = True

    def _umount(self):
        if self.mounted:
            sh_ok(f"sudo umount {self.merged}")
            self.mounted = False

    def read(self, name: str) -> str:
        # Record what the BASE looked like — that is what validation checks.
        self.reads[f"file:{name}"] = self.base.hash_file(name)
        p = self.merged / name
        return p.read_text() if p.exists() else ""

    def write(self, name: str, content: str):
        self.writes.append(name)
        (self.merged / name).write_text(content)

    def cmd(self, command: str) -> str:
        # Probe against BASE (for validation). Run against MERGED (for the agent).
        # In practice these agree unless the agent wrote to what the command reads.
        r_base = sh_ok(command, cwd=str(self.base.path))
        raw = normalize(r_base.stdout + r_base.stderr + f"\n[exit {r_base.returncode}]")
        self.reads[f"cmd:{command}"] = hashlib.sha256(raw.encode()).hexdigest()[:12]
        r = sh_ok(command, cwd=str(self.merged))
        return normalize(r.stdout + r.stderr + f"\n[exit {r.returncode}]")

    def validate(self, head: Snapshot):
        for key, observed in self.reads.items():
            now = head.probe(key)
            if now != observed:
                return False, f"read of {key} invalidated ({observed} -> {now})"
        return True, None

    def commit(self, head: Snapshot):
        ok, why = self.validate(head)
        if not ok:
            return None, why
        new_path = SANDBOX / f"snap_{self.tag}"
        if new_path.exists():
            shutil.rmtree(new_path)
        shutil.copytree(head.path, new_path)
        for src in self.upper.rglob("*"):
            if src.is_file():
                rel = src.relative_to(self.upper)
                dst = new_path / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        return Snapshot(new_path), None

    def cleanup(self):
        self._umount()
        sh_ok(f"sudo rm -rf {self.dir}")


def show(snap: Snapshot, label: str):
    print(f"\n--- {label}  ({snap.path.name}) ---")
    for p in sorted(snap.path.iterdir()):
        if p.is_file():
            body = p.read_text().rstrip().replace("\n", " | ")
            print(f"  {p.name}: {body}")


def cleanup_all_mounts():
    if not SANDBOX.exists():
        return
    for d in SANDBOX.rglob("merged"):
        sh_ok(f"sudo umount {d}")


def main():
    print("AgentTx — Leg 6: overlayfs + OCC\n")

    cleanup_all_mounts()
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    SANDBOX.mkdir(parents=True)

    s0 = Snapshot.create(SANDBOX / "snapshots" / "s0", {
        "api.py": "def get_user():\n    return 'alice'\n",
        "client.py": "from api import get_user\n\nprint(get_user())\n",
    })
    show(s0, "Head S0 (base)")

    print("\n--- Both agents begin transactions against S0 ---")
    a = Txn("A", s0)
    b = Txn("B", s0)

    print("\n--- Agent A works in its overlay ---")
    api = a.read("api.py")
    a.write("api.py", api.replace("get_user", "fetch_user"))
    upper_files = [str(p.relative_to(a.upper)) for p in a.upper.rglob("*") if p.is_file()]
    print(f"  A's upper layer: {upper_files}")
    print(f"  base api.py still says: {s0.path.joinpath('api.py').read_text().strip()}")

    print("\n--- Agent B works in its overlay ---")
    grep_out = b.cmd("grep -rn 'def get_user' . | sort")
    first = grep_out.splitlines()[0] if grep_out else "(empty)"
    print(f"  B's grep sees: {first}")
    print(f"  B recorded a cmd: probe — no file: probe for api.py")

    print("\n--- Commit Agent A ---")
    head, why = a.commit(s0)
    if head is None:
        print(f"  A ABORT: {why}")
        return
    print(f"  A committed. New head: {head.path.name}")
    show(head, "Head S1 (after A)")

    print("\n--- Commit Agent B ---")
    head2, why = b.commit(head)
    if head2 is None:
        print(f"  B ABORT: {why}")
        print(f"  B's upper layer discarded. Head unchanged.")
    else:
        print(f"  B committed. New head: {head2.path.name}")
        head = head2

    show(head, "Final head")

    a.cleanup()
    b.cleanup()
    print("\nDone. Mounts cleaned up.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        cleanup_all_mounts()
        raise
