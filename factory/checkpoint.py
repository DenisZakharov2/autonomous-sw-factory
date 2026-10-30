"""Signed journal checkpoints.

The hash chain proves internal consistency, not authorship: any writer who can
open `runs/<id>/blackbox.jsonl` can recompute every `hash` and `prev_hash`.
A checkpoint is an HMAC-SHA256 over the chain head, keyed by a secret that
lives **outside** the run.  Verification compares the live head with the
signed one, so a rewritten chain no longer matches.

Why HMAC and not Ed25519: no new dependency, and the factory owner and the
auditor are the same party today.  HMAC requires the verifier to hold the key;
an asymmetric signature would let a third party verify with a public key.  That
upgrade is a separate change, not a silent one.

What a checkpoint does *not* do: it cannot stop a writer who can also rewrite
the store (the key would still be needed), and an unsigned run is unanchored,
not "verified".  `verify` reports the difference instead of hiding it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ConfigError, RunError
from .journal import canonical_json, read_events, verify_chain
from .paths import ensure_within

KEY_ENV = "FACTORY_JOURNAL_KEY"
STORE_ENV = "FACTORY_CHECKPOINT_DIR"
MIN_KEY_BYTES = 32
RUN_COPY = "checkpoint.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def key_id(key: bytes) -> str:
    """A public label for a secret key, so mismatches can be explained."""
    return hashlib.sha256(key).hexdigest()[:16]


def load_key(key_file: Path | None = None) -> bytes:
    """Read the HMAC key from a file or `FACTORY_JOURNAL_KEY`.

    Hex-encoded keys are decoded; anything else is used as raw bytes.  A short
    key is refused: an 8-character secret would make the signature decorative.
    """
    if key_file is not None:
        path = Path(key_file).expanduser()
        if not path.is_file():
            raise ConfigError(f"journal key file not found: {path}")
        raw = path.read_bytes().strip()
        source = str(path)
    else:
        env = os.environ.get(KEY_ENV, "")
        if not env:
            raise ConfigError(
                f"no journal key: pass --key-file or set {KEY_ENV} "
                f"(at least {MIN_KEY_BYTES} bytes, hex or raw)"
            )
        raw = env.strip().encode("utf-8")
        source = KEY_ENV
    try:
        decoded = bytes.fromhex(raw.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        decoded = raw
    if len(decoded) < MIN_KEY_BYTES:
        raise ConfigError(
            f"journal key from {source} is {len(decoded)} bytes; "
            f"need at least {MIN_KEY_BYTES}"
        )
    return decoded


def store_dir(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get(STORE_ENV)
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".local" / "state" / "factory" / "checkpoints").resolve()


def store_path(run_id: str, store: Path | None = None) -> Path:
    return (store or store_dir()) / f"{run_id}.json"


def _signature(key: bytes, body: dict[str, Any]) -> str:
    payload = canonical_json(
        {
            "run_id": body["run_id"],
            "key_id": body["key_id"],
            "event_count": body["event_count"],
            "head_hash": body["head_hash"],
            "created_at": body["created_at"],
        }
    )
    return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _write_atomic(path: Path, data: dict[str, Any], mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _refuse_key_inside_run(run_dir: Path, key_file: Path | None) -> None:
    if key_file is None:
        return
    try:
        ensure_within(Path(key_file).expanduser(), run_dir)
    except RunError:
        return
    raise ConfigError(
        "journal key must live outside the run directory: "
        "a key the run can read is a key the executor can use to re-sign"
    )


def _refuse_store_inside_run(run_dir: Path, store: Path) -> None:
    try:
        ensure_within(store, run_dir)
    except RunError:
        return
    raise ConfigError("checkpoint store must live outside the run directory")


def sign_run(
    run_dir: Path,
    *,
    key_file: Path | None = None,
    store: Path | None = None,
) -> dict[str, Any]:
    """Sign the current journal head and append it to the checkpoint store.

    The run cannot be signed while its chain is broken: a signature over a
    chain that already fails verification would launder the damage.
    """
    run_dir = run_dir.resolve()
    _refuse_key_inside_run(run_dir, key_file)
    key = load_key(key_file)
    target = store_path(run_dir.name, store)
    _refuse_store_inside_run(run_dir, store if store is not None else store_dir())

    chain_errors = verify_chain(run_dir / "blackbox.jsonl", run_dir / "state.json")
    if chain_errors:
        raise RunError(
            "refusing to sign a journal that does not verify: " + "; ".join(chain_errors[:3])
        )
    events = read_events(run_dir / "blackbox.jsonl")
    head = events[-1]
    entry = {
        "run_id": run_dir.name,
        "key_id": key_id(key),
        "event_count": len(events),
        "head_hash": str(head["hash"]),
        "created_at": utc_now(),
    }
    entry["hmac"] = _signature(key, entry)

    document: dict[str, Any] = {"run_id": run_dir.name, "key_id": entry["key_id"], "checkpoints": []}
    if target.is_file():
        try:
            previous = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"checkpoint store is not valid JSON: {target}: {exc}") from exc
        if previous.get("run_id") != run_dir.name:
            raise ConfigError(f"checkpoint store belongs to another run: {target}")
        document["checkpoints"] = list(previous.get("checkpoints") or [])
    document["checkpoints"].append(entry)
    _write_atomic(target, document, 0o600)

    run_copy = run_dir / RUN_COPY
    _write_atomic(run_copy, document, 0o644)
    return {"store": str(target), "run_copy": str(run_copy), **entry}


def read_store(run_id: str, store: Path | None = None) -> dict[str, Any] | None:
    path = store_path(run_id, store)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"checkpoint store is not valid JSON: {path}: {exc}") from exc


def checkpoint_state(run_dir: Path, store: Path | None = None) -> dict[str, Any]:
    """Describe how the journal relates to its signed checkpoints.

    `signed` — the head is exactly the newest signature;
    `stale` — the journal advanced past the newest signature (normal between
    gates; re-sign to move the anchor);
    `rolled_back` — the head is an *older* signed checkpoint, which is what a
    truncation looks like;
    `diverged` — the head matches no signature at all;
    `unsigned` — no checkpoint store exists for this run.
    """
    run_dir = run_dir.resolve()
    document = read_store(run_dir.name, store)
    if document is None:
        return {"status": "unsigned", "checkpoints": 0}
    checkpoints = list(document.get("checkpoints") or [])
    if not checkpoints:
        return {"status": "unsigned", "checkpoints": 0, "store": str(store_path(run_dir.name, store))}
    events = read_events(run_dir / "blackbox.jsonl")
    head = str(events[-1]["hash"]) if events else ""
    count = len(events)
    newest = checkpoints[-1]
    result = {
        "status": "diverged",
        "checkpoints": len(checkpoints),
        "key_id": newest.get("key_id"),
        "signed_event_count": newest.get("event_count"),
        "signed_head": newest.get("head_hash"),
        "event_count": count,
        "head_hash": head,
        "store": str(store_path(run_dir.name, store)),
    }
    for index, entry in enumerate(checkpoints, 1):
        if entry.get("head_hash") == head:
            result["status"] = "signed" if index == len(checkpoints) else "rolled_back"
            result["matched_checkpoint"] = index
            break
    else:
        if newest.get("head_hash") and count > int(newest.get("event_count") or 0):
            result["status"] = "stale"
    run_copy = run_dir / RUN_COPY
    if run_copy.is_file() and run_copy.read_text(encoding="utf-8") != json.dumps(
        document, indent=2, ensure_ascii=False, sort_keys=True
    ) + "\n":
        result["run_copy_edited"] = True
    return result


def verify_checkpoint(
    run_dir: Path,
    *,
    key_file: Path | None = None,
    store: Path | None = None,
    key: bytes | None = None,
) -> list[str]:
    """Return errors for a checkpoint that does not protect the journal.

    An unsigned run returns no errors: the absence of an anchor is reported by
    `checkpoint_state`, not turned into a failure of a journal that is still
    internally consistent.
    """
    state = checkpoint_state(run_dir, store)
    if state["status"] == "unsigned":
        return []
    errors: list[str] = []
    document = read_store(Path(run_dir).resolve().name, store) or {}
    checkpoints = list(document.get("checkpoints") or [])
    secret = key if key is not None else None
    if secret is None:
        try:
            secret = load_key(key_file)
        except ConfigError as exc:
            return [f"checkpoint exists but cannot be verified: {exc}"]
    expected_id = key_id(secret)
    for index, entry in enumerate(checkpoints, 1):
        if entry.get("key_id") != expected_id:
            errors.append(
                f"checkpoint #{index} was signed with key {entry.get('key_id')}, "
                f"the supplied key is {expected_id}"
            )
            continue
        body = {
            "run_id": document.get("run_id"),
            "key_id": entry.get("key_id"),
            "event_count": entry.get("event_count"),
            "head_hash": entry.get("head_hash"),
            "created_at": entry.get("created_at"),
        }
        if not hmac.compare_digest(str(entry.get("hmac")), _signature(secret, body)):
            errors.append(f"checkpoint #{index} signature does not match its own contents")
    status = state["status"]
    if status == "rolled_back":
        errors.append(
            f"journal head matches checkpoint #{state['matched_checkpoint']} of "
            f"{state['checkpoints']}: the journal was truncated after signing"
        )
    elif status == "diverged":
        errors.append(
            "journal head matches no signed checkpoint "
            f"(signed {state['signed_event_count']} events, journal has {state['event_count']}): "
            "the chain was rewritten after signing"
        )
    if state.get("run_copy_edited"):
        errors.append(
            f"{RUN_COPY} inside the run differs from the checkpoint store copy"
        )
    return errors
