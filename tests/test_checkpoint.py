"""Signed journal checkpoints: a signature that survives a chain rewrite."""
import json
import os
from pathlib import Path

import pytest

from factory.checkpoint import (
    checkpoint_state,
    load_key,
    sign_run,
    store_path,
    verify_checkpoint,
)
from factory.engine import init_run
from factory.errors import ConfigError, FactoryError
from factory.journal import append_event, canonical_json, compute_event_hash, read_events

KEY = bytes(range(32))


@pytest.fixture()
def signed_run(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    run_dir, _state, _event = init_run("run-01", "nano", root=runs)
    key_file = tmp_path / "journal.key"
    key_file.write_bytes(KEY.hex().encode("ascii"))
    store = tmp_path / "checkpoints"
    return run_dir, key_file, store


def _more_events(run_dir: Path, count: int) -> None:
    for index in range(count):
        append_event(
            run_dir,
            stage="s00_intake",
            task_id=None,
            role="00-orchestrator",
            event_type="STAGE_RESET",
            why=f"test event {index}",
            for_what="checkpoint test",
            action="noop",
            verdict_status="DRAFT",
            verified_by="00-orchestrator",
        )


def _rewrite_chain(run_dir: Path, mutate) -> None:
    """Recompute every hash after tampering, as a writer with the run would."""
    events = read_events(run_dir / "blackbox.jsonl")
    previous = "0" * 64
    rewritten = []
    for event in events:
        payload = {key: value for key, value in event.items() if key != "hash"}
        mutate(payload)
        payload["prev_hash"] = previous
        payload["hash"] = compute_event_hash(previous, payload)
        previous = payload["hash"]
        rewritten.append(payload)
    (run_dir / "blackbox.jsonl").write_text(
        "".join(canonical_json(event) + "\n" for event in rewritten), encoding="utf-8"
    )
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["last_event_hash"] = previous
    state["event_count"] = len(rewritten)
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _truncate(run_dir: Path, keep: int) -> None:
    events = read_events(run_dir / "blackbox.jsonl")
    kept = events[:keep]
    (run_dir / "blackbox.jsonl").write_text(
        "".join(canonical_json(event) + "\n" for event in kept), encoding="utf-8"
    )
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["last_event_hash"] = kept[-1]["hash"]
    state["event_count"] = len(kept)
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def test_sign_then_verify_is_clean(signed_run):
    run_dir, key_file, store = signed_run
    _more_events(run_dir, 2)
    result = sign_run(run_dir, key_file=key_file, store=store)
    assert result["event_count"] == 3
    assert store_path("run-01", store).is_file()
    if os.name != "nt":
        # Windows does not enforce POSIX mode bits; the owner ACL is the OS
        # equivalent, and the signature itself is checked below.
        assert (store / "run-01.json").stat().st_mode & 0o777 == 0o600
    status = checkpoint_state(run_dir, store)
    assert status["status"] == "signed"
    assert verify_checkpoint(run_dir, key_file=key_file, store=store) == []


def test_new_events_make_the_checkpoint_stale_not_broken(signed_run):
    run_dir, key_file, store = signed_run
    sign_run(run_dir, key_file=key_file, store=store)
    _more_events(run_dir, 1)
    assert checkpoint_state(run_dir, store)["status"] == "stale"
    assert verify_checkpoint(run_dir, key_file=key_file, store=store) == []


def test_rewritten_chain_no_longer_matches_the_signature(signed_run):
    run_dir, key_file, store = signed_run
    sign_run(run_dir, key_file=key_file, store=store)

    def mutate(payload):
        payload["verdict"]["status"] = "PASS"
        payload["role"] = "02-independent-verifier"
        payload["verdict"]["verified_by"] = "02-independent-verifier"
        payload["verdict"]["evidence"] = ["forged"]

    _rewrite_chain(run_dir, mutate)
    status = checkpoint_state(run_dir, store)
    assert status["status"] == "diverged"
    errors = verify_checkpoint(run_dir, key_file=key_file, store=store)
    assert any("rewritten after signing" in error for error in errors)


def test_truncation_to_a_signed_head_is_rolled_back(signed_run):
    run_dir, key_file, store = signed_run
    sign_run(run_dir, key_file=key_file, store=store)
    _more_events(run_dir, 1)
    sign_run(run_dir, key_file=key_file, store=store)
    _truncate(run_dir, keep=1)
    status = checkpoint_state(run_dir, store)
    assert status["status"] == "rolled_back"
    errors = verify_checkpoint(run_dir, key_file=key_file, store=store)
    assert any("truncated after signing" in error for error in errors)


def test_a_different_key_is_named_in_the_error(signed_run):
    run_dir, key_file, store = signed_run
    sign_run(run_dir, key_file=key_file, store=store)
    other = key_file.with_name("other.key")
    other.write_bytes(bytes([7]) * 32)
    errors = verify_checkpoint(run_dir, key_file=other, store=store)
    assert errors and "the supplied key is" in errors[0]


def test_missing_checkpoint_is_unsigned_not_an_error(signed_run):
    run_dir, key_file, store = signed_run
    assert checkpoint_state(run_dir, store)["status"] == "unsigned"
    assert verify_checkpoint(run_dir, key_file=key_file, store=store) == []


def test_key_inside_the_run_is_refused(signed_run, tmp_path):
    run_dir, key_file, store = signed_run
    inside = run_dir / "journal.key"
    inside.write_bytes(KEY.hex().encode("ascii"))
    with pytest.raises(ConfigError, match="outside the run directory"):
        sign_run(run_dir, key_file=inside, store=store)


def test_store_inside_the_run_is_refused(signed_run):
    run_dir, key_file, _store = signed_run
    with pytest.raises(ConfigError, match="outside the run directory"):
        sign_run(run_dir, key_file=key_file, store=run_dir / "checkpoints")


def test_signing_a_broken_chain_is_refused(signed_run):
    run_dir, key_file, store = signed_run
    _more_events(run_dir, 1)
    events = read_events(run_dir / "blackbox.jsonl")
    events[0]["why"] = "edited without recomputing the chain"
    (run_dir / "blackbox.jsonl").write_text(
        "".join(canonical_json(event) + "\n" for event in events), encoding="utf-8"
    )
    with pytest.raises(FactoryError, match="refusing to sign"):
        sign_run(run_dir, key_file=key_file, store=store)


def test_short_key_is_refused(tmp_path, monkeypatch):
    short = tmp_path / "short.key"
    short.write_bytes(b"12345678")
    with pytest.raises(ConfigError, match="need at least 32"):
        load_key(short)


def test_env_key_is_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_JOURNAL_KEY", KEY.hex())
    assert load_key() == KEY


def test_edited_run_copy_is_reported(signed_run):
    run_dir, key_file, store = signed_run
    sign_run(run_dir, key_file=key_file, store=store)
    copy = run_dir / "checkpoint.json"
    document = json.loads(copy.read_text(encoding="utf-8"))
    document["checkpoints"][-1]["event_count"] = 99
    copy.write_text(json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    status = checkpoint_state(run_dir, store)
    assert status.get("run_copy_edited") is True
    assert any("checkpoint.json inside the run differs" in error for error in verify_checkpoint(
        run_dir, key_file=key_file, store=store
    ))
