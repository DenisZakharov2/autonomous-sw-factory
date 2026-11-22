# Next Session Prompt — PR-E + PR-F Complete, Integration Tests Needed

## What was done (committed & pushed to `main`)

### PR-E: root-helper hardening (`factory/runtime/root_helper.py`)
- **`_stop`**: pidfd_open → read UID from `/proc/self/fd/<pidfd>/status` → pidfd_send_signal(SIGKILL). Fail-closed on old kernels (< 5.1).
- **`_run_as`**: `prctl(PR_SET_NO_NEW_PRIVS,1)` before privilege drop, empty capset via `capsh --drop=all`, validate `command[0]` is absolute + root-owned + parent chain root-owned.
- **Doctor**: SHA256 of installed `/usr/local/libexec/factory-root-helper` vs `render_helper_script()`.

### PR-F: human gate fail-closed + JTI (`factory/engine/gates.py`)
- **Fail-closed**: `FACTORY_APPROVAL_KEY` required, no "unauthenticated" fallback.
- **Token format**: `exp.jti.hmac` (JTI = `secrets.token_urlsafe(16)`), JTI included in HMAC payload.
- **Replay protection**: spent JTI stored in `~/.local/state/factory/checkpoints/approval-jti.json` (outside `runs/`), atomic writes via temp+rename.
- **`make_approval_token`**: generates JTI, includes in HMAC.

### Tests passing (105+)
```
test_root_helper.py (15)
test_engine.py (26)
test_cli.py (23)
test_verify.py (32)
test_credential_proxy.py (9)
test_pipeline.py (9)
test_paths.py (16)
test_journal.py (27)
test_checkpoint.py (11)
test_host_verify.py (16)
test_rerecord.py (13)
test_symlink_writes.py (13)
test_runtime_loop.py (2 updated)
test_runtime_verifier.py (2 updated)
```

### Lint / typecheck
```
ruff check factory/ tests/  → All checks passed!
mypy  → Success: no issues found in 4 source files
```

---

## What's left

### 1. Remaining test files using human gates without approval key
Need to update these to set `FACTORY_APPROVAL_KEY` + pass `--approval-token`:
- `tests/test_v012_regressions.py` (8 failures)
- `tests/test_verdict_freshness.py` (2 tests with `human:denis`)
- `tests/test_quarantine.py` (1 test with `human:test`)
- `tests/test_runtime_standard.py` (1 test with `human:test`)

Pattern (see `tests/test_runtime_loop.py`):
```python
monkeypatch.setenv("FACTORY_APPROVAL_KEY", "a" * 32)
# ...
token = make_approval_token(key=b"a"*32, run_id=..., subject=..., approver="human:test", action="approve")
gate_approve(..., approval_token=token)
```

### 2. Integration run (PR-C2 credential proxy + PR-E + PR-F)
```bash
factory run --run-id live --executor hermes --yolo --max-steps 1
```
Requires working `hermes` CLI (user says it's being updated).

### 3. Credential proxy integration tests
Add tests with fake upstream (analogous to `fake_openai.py`) exercising the full proxy chain:
- Host proxy → proxy_bridge → sandbox → upstream

### 4. Install helper for doctor to pass
```bash
factory setup-executor
# or manually:
sudo cp factory/runtime/root_helper.py /usr/local/libexec/factory-root-helper
sudo chmod 755 /usr/local/libexec/factory-root-helper
```

---

## Quick verification commands
```bash
cd /home/anony/Documents/Личное/Проект\ фабрика\ программного\ обеспечения/autonomous-sw-factory
.venv/bin/ruff check factory/ tests/
.venv/bin/mypy
.venv/bin/pytest tests/test_root_helper.py tests/test_engine.py tests/test_cli.py tests/test_verify.py -v
factory doctor
```