# Skills

`skills/.quarantine/` receives untrusted skill directories. Nothing here is
installed until `factory quarantine scan` returns `AUTO_ALLOW` or a human
approves a `CAUTION` decision.

`skills/verified/` is the local install target for approved skills. The lock is
`skills-lock.json` at the repository root (or the run root when the example
sets `FACTORY_ROOT`).

Never commit quarantined content. `.gitignore` keeps the directories but keeps
their `.gitkeep` files.
