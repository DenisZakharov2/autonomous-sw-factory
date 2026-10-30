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
