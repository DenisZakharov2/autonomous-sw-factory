# Audit records

Files here are evidence, not active configuration. Nothing in this directory is
read by the factory at runtime.

## `2026-10-29-pfu-impl-lock.json`

A SkillSpector scan of the local Hermes skill `~/.hermes/skills/pfu-impl`,
recorded with `factory quarantine scan --lock skills-lock.json` from the factory
repository root on 2026-10-29. It is kept as history because it documents a
rejected scan, not an installed skill:

- verdict `CRITICAL` / `DO_NOT_INSTALL`, `is_complete: false`, coverage 85.7%;
- the locked path `pfu-impl` is relative to the factory root and does not exist
  there, so `factory quarantine verify-lock` rejects it;
- the factory policy requires `REJECT_AND_PURGE` for this verdict, so such an
  entry must never appear in the active lock.

The active [`skills-lock.json`](../../skills-lock.json) is empty: the factory
repository installs no external skills. To record a real install, run
`factory quarantine scan` against the skill inside this repository and let the
decision matrix write the lock.

## `handoff-1.0.9.md`

The root `handoff.md` session log up to release 1.0.9, archived here so the
working history survives without claiming a permanent root path. References
from `DESIGN.md`, `TECHNICAL.md`, `docs/runtime.md` and `factory/trace.py`
point at this file. New milestones start a fresh root `handoff.md`.

## `grok-2026-11-10.md`

An independent code review of release 1.0.11 (`d40148d`), written outside the
project. It found that the polkit rule granted the operator passwordless root
through `systemd-run --system`, that a checkpoint signed for one run was
accepted for another, that a human gate token was neither bound to a decision
nor expiring, and that the journal append followed a symlink. The review also
listed what it did not consider covered: the executor keeps network access and
can still read world-readable files of the operator.

The review came with a patch. The patch was not taken as-is: its root helper
ran under the operator's interpreter and imported the operator's package,
which would have given passwordless root back, and it left the helper
uninstallable. The findings were confirmed, the patch was rewritten, and the
divergences are listed in `fix-plan-1.0.12.md`.

## `fix-plan-1.0.12.md`

The plan the 1.0.12 fixes were made from: what the audit confirmed, what the
audit overstated, what the patch got wrong, and the order of the seven
commits. Kept as the record of why the code looks the way it does.

## `fix-plan-1.0.10-implemented.md`

The plan of the previous cycle (1.0.10 → 1.0.11), archived after it was
implemented. It is the origin of the package-data, worktree-symlink,
isolation, sandbox and checkpoint fixes listed under 1.0.11 in the changelog.
