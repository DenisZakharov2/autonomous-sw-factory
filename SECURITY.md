# Security policy

## Supported versions

| Version | Supported |
| :--- | :--- |
| `0.1.x` | yes |
| older | no |

## Reporting a vulnerability

Open a private GitHub security advisory:
<https://github.com/DenisZakharov2/autonomous-sw-factory/security/advisories/new>

Do not post secrets, tokens or customer data in a public issue.

Include:

- version and Python version;
- a minimal reproduction;
- whether the issue is in the engine, a template/schema, or a third-party skill;
- expected and actual behavior.

## Scope

In scope:

- path traversal or writes outside `runs/`;
- journal forgery, truncation or `PASS` spoofing;
- quarantine bypass (incomplete scan installed, lock hash bypass);
- secret leakage into artifacts or logs;
- command injection through artifact paths or run ids.

Out of scope:

- a hostile root user on the host;
- a model that ignores its role card without any engine bypass;
- vulnerabilities in third-party skills themselves (report upstream; the
  quarantine gate is our control);
- the unsiged hash chain being rewritable by a writer who recomputes it — this
  is a documented limitation, not a surprise.

## Controls in `0.2.0`

- strict run-id regex and resolved-path checks;
- `fcntl.flock` around journal writes;
- state anchor (`last_event_hash`, `event_count`);
- semantic `PASS` check at append and verify time;
- gate decisions accepted only from the declared approver, on approve and on reject;
- task submits checked against the task owner, the task id and `allowed_files`;
- stage submits checked against the declared artifact owner;
- fail-closed SkillSpector gate and directory hash lock;
- `.gitignore` excludes `runs/`, `.env`, keys and quarantine;
- no secrets are read by the engine.

### What the role checks are not

`--role` and `--approver` are strings typed on the command line. Owner,
approver and `allowed_files` checks enforce the protocol and catch mistakes;
they are not authentication and not RBAC. A caller who can run `factory` can
type any role name. Real isolation needs a trusted runner that resolves the
role from its own context, plus a signed checkpoint outside `runs/`.

## Residual risk

The hash chain is unkeyed. It detects accidental edits and naive tampering, not
a determined attacker with write access. Add an external HMAC/Ed25519
checkpoint or an append-only store for a stronger threat model.
