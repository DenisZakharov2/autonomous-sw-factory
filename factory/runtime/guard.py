"""Tripwires around an executor call.

The engine owns `state.json` and `blackbox.jsonl`; an agent that rewrites them
can forge its own progress.  The loop fingerprints the protected files and the
runs root before every step and refuses to continue if the executor touched
them.  This is a detector, not a sandbox: it proves tampering happened, it
does not prevent it.  Real isolation is `factory.runtime.sandbox`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..journal import sha256_file

PROTECTED_FILES = ("state.json", "blackbox.jsonl", "blackbox.lock", "idea.md")


@dataclass(frozen=True)
class Fingerprint:
    files: dict[str, str | None]
    entries: frozenset[str]
    run_dir: Path

    @classmethod
    def capture(cls, run_dir: Path) -> "Fingerprint":
        run_dir = run_dir.resolve()
        files: dict[str, str | None] = {}
        for name in PROTECTED_FILES:
            path = run_dir / name
            files[name] = sha256_file(path) if path.is_file() else None
        entries: set[str] = set()
        parent = run_dir.parent
        if parent.is_dir():
            for entry in parent.iterdir():
                if entry.name == run_dir.name:
                    continue
                if entry.name == ".archive":
                    continue
                entries.add(entry.name)
        return cls(files=files, entries=frozenset(entries), run_dir=run_dir)

    def drift(self, later: "Fingerprint") -> list[str]:
        problems: list[str] = []
        for name, before in self.files.items():
            after = later.files.get(name)
            if before == after:
                continue
            if before is None:
                problems.append(f"executor created protected file {name}")
            elif after is None:
                problems.append(f"executor deleted protected file {name}")
            else:
                problems.append(f"executor modified protected file {name}")
        for name in sorted(later.entries - self.entries):
            problems.append(f"executor wrote outside the run directory: {name}")
        for name in sorted(self.entries - later.entries):
            problems.append(f"executor removed a path outside the run directory: {name}")
        return problems
