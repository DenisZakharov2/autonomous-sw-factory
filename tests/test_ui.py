"""The dashboard: payload shape, read-only promise, HTTP surface, chain check.

A1-A8 and A11 of the spec are tested here; A9-A10 (drag, resize, import/export)
are JS behaviour and are checked in the browser -- the repository has no JS test
harness and one file does not justify building one.
"""
import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from factory.engine import init_run
from factory.ui import data
from factory.ui.server import make_server

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples"


@pytest.fixture()
def served(tmp_path):
    run_dir, _state, _event = init_run("run-01", "nano", root=tmp_path)
    (run_dir / "idea.md").write_text("Нано- todo на stdlib\n", encoding="utf-8")
    data.clear_cache()
    httpd = make_server(runs_dir=tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path, run_dir
    finally:
        httpd.shutdown()
        httpd.server_close()
        data.clear_cache()


def get(base, path, headers=None):
    request = urllib.request.Request(base + path, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def page_text() -> str:
    return (ROOT / "factory" / "ui" / "static" / "index.html").read_text(encoding="utf-8")


# -- A1: stages -------------------------------------------------------------
def test_all_thirteen_stages_and_their_real_gate_ids():
    payload = data.run_payload("nano-todo", root=EXAMPLE)
    assert len(payload["stages"]) == 13
    by_id = {stage["id"]: stage for stage in payload["stages"]}
    assert by_id["s2_architecture"]["gate"] == "gate_s2"
    assert by_id["s2_architecture"]["gate_status"] == "approved"
    # The gate id is read from the pipeline, never derived from the stage id.
    assert by_id["s00_intake"]["gate"] == "gate_s00"


def test_greenfield_marks_s_in_as_not_applicable(served):
    base, _root, _run = served
    _status, _headers, body = get(base, "/api/runs/run-01")
    payload = json.loads(body)
    s_in = next(stage for stage in payload["stages"] if stage["id"] == "s_in")
    assert s_in["applicable"] is False
    assert len(payload["stages"]) == 13


# -- A2: project title ------------------------------------------------------
def test_project_title_is_the_first_line(tmp_path):
    run_dir, _state, _event = init_run("run-01", "nano", root=tmp_path)
    (run_dir / "idea.md").write_text("\n\n  Первая строка  \nвторая\n", encoding="utf-8")
    payload = data.run_payload("run-01", root=tmp_path)
    assert payload["header"]["project"] == "Первая строка"


def test_project_title_is_cut_at_120_and_falls_back_to_run_id(tmp_path):
    run_dir, _state, _event = init_run("run-01", "nano", root=tmp_path)
    (run_dir / "idea.md").write_text("я" * 300 + "\n", encoding="utf-8")
    assert data.run_payload("run-01", root=tmp_path)["header"]["project"] == "я" * 120
    (run_dir / "idea.md").unlink()
    assert data.run_payload("run-01", root=tmp_path)["header"]["project"] == "run-01"


# -- A3: revision and the polling contract ----------------------------------
def test_revision_changes_and_the_new_event_reaches_the_payload(served):
    base, root, run_dir = served
    _status, headers, body = get(base, "/api/runs/run-01")
    first = json.loads(body)
    etag = headers["ETag"]
    _status, _headers, body = get(base, "/api/runs/run-01", {"If-None-Match": etag})
    assert _status == 304

    with (root / "run-01" / "blackbox.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"seq": 2, "event_type": "APPENDED", "why": "новое событие"}) + "\n")

    status, headers, body = get(base, "/api/runs/run-01", {"If-None-Match": etag})
    assert status == 200
    fresh = json.loads(body)
    assert fresh["revision"] != first["revision"]
    assert fresh["journal"]["events"][-1]["event_type"] == "APPENDED"


def test_client_polls_every_two_seconds_only_when_visible():
    text = page_text()
    assert "const POLL_MS = 2000;" in text
    assert "document.visibilityState" in text


# -- A4: payload cost -------------------------------------------------------
def test_warm_payload_for_117_events_stays_under_250ms(tmp_path):
    run_dir, _state, _event = init_run("run-01", "nano", root=tmp_path)
    lines = [json.dumps({"seq": index, "event_type": "INIT", "why": f"event {index}"}) for index in range(117)]
    (run_dir / "blackbox.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    data.run_payload("run-01", root=tmp_path)  # warm the caches
    started = time.perf_counter()
    data.run_payload("run-01", root=tmp_path)
    assert (time.perf_counter() - started) < 0.250


# -- A5: air-gapped page ----------------------------------------------------
def test_page_has_no_external_urls(served):
    base, _root, _run = served
    status, headers, body = get(base, "/")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    text = body.decode("utf-8")
    assert "http://" not in text and "https://" not in text
    assert "//cdn" not in text and "@import" not in text


def test_page_is_under_250kb():
    assert len(page_text().encode("utf-8")) < 250 * 1024


# -- A6: read-only ----------------------------------------------------------
def test_get_requests_leave_the_run_byte_identical(served):
    base, _root, run_dir = served
    usage = run_dir / "runtime" / "usage.jsonl"
    usage.parent.mkdir(parents=True, exist_ok=True)
    usage.write_text(json.dumps({"tokens_total": 10, "seconds": 1.5}) + "\n", encoding="utf-8")
    watched = [run_dir / "state.json", run_dir / "blackbox.jsonl", usage]

    def digest():
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in watched}

    before = digest()
    for _ in range(3):
        for path in ("/", "/api/runs", "/api/runs/run-01", "/api/runs/run-01/chain?refresh=1"):
            get(base, path)
    assert digest() == before


def test_writes_are_refused_with_405(served):
    base, _root, _run = served
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        request = urllib.request.Request(base + "/api/runs", data=b"{}", method=method)
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        assert caught.value.code == 405
        assert caught.value.headers["Allow"] == "GET"


# -- A7: errors -------------------------------------------------------------
def test_unknown_run_is_a_404_json(served):
    base, _root, _run = served
    status, headers, body = get(base, "/api/runs/no-such-run")
    assert status == 404
    assert headers["Content-Type"].startswith("application/json")
    assert "no-such-run" in json.loads(body)["error"]


def test_run_id_outside_the_schema_is_a_404(served):
    base, _root, _run = served
    for run_id in ("../etc", "Run_01", "a" * 64):
        status, _headers, body = get(base, f"/api/runs/{urllib.parse.quote(run_id, safe='')}")
        assert status == 404, run_id
        assert "run_id must match" in json.loads(body)["error"] or "does not exist" in json.loads(body)["error"]


def test_broken_state_json_is_a_500_and_the_page_still_loads(served):
    base, _root, run_dir = served
    (run_dir / "state.json").write_text("{not json", encoding="utf-8")
    data.clear_cache()
    status, _headers, body = get(base, "/api/runs/run-01")
    assert status == 500
    assert json.loads(body)["error"]
    status, _headers, _body = get(base, "/")
    assert status == 200


def test_runs_index_survives_one_unreadable_run(served):
    base, _root, run_dir = served
    broken, _state, _event = init_run("run-02", "nano", root=run_dir.parent)
    (broken / "state.json").write_text("{not json", encoding="utf-8")
    status, _headers, body = get(base, "/api/runs")
    assert status == 200
    rows = {row["run_id"]: row for row in json.loads(body)["runs"]}
    assert rows["run-01"]["error"] is None
    assert rows["run-02"]["error"]


# -- A8: chain --------------------------------------------------------------
def test_tampered_journal_is_reported_with_the_exact_seq(served):
    base, _root, run_dir = served
    log = run_dir / "blackbox.jsonl"
    event = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    event["why"] = "подменено"
    log.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")
    status, _headers, body = get(base, "/api/runs/run-01/chain?refresh=1")
    report = json.loads(body)
    assert status == 200
    assert report["status"] == "TAMPERED"
    assert report["seq"] == 1


def test_intact_chain_is_ok_and_is_cached(served):
    base, _root, _run = served
    first = json.loads(get(base, "/api/runs/run-01/chain?refresh=1")[2])
    assert first["status"] == "OK"
    cached = json.loads(get(base, "/api/runs/run-01/chain")[2])
    assert cached["checked_at"] == first["checked_at"]  # no second verify_chain


# -- panels ---------------------------------------------------------------
def test_now_panel_maps_the_expected_action():
    payload = data.run_payload("nano-todo", root=EXAMPLE)
    assert payload["now"]["expected"] == "активной стадии нет"
    assert payload["now"]["last_event"]["event_type"] == "GATE_DECISION"


def test_tasks_panel_carries_the_state_fields(served):
    base, _root, run_dir = served
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    task = {
        "task_id": "TASK-001", "stage": "s4_build", "owner": "12-fullstack-developer",
        "reqs": ["REQ-001"], "allowed_files": ["product/src/a.py", "product/src/b.py", "product/src/c.py"],
        "deps": [], "status": "review", "attempts": 2, "artifacts": [], "lease": None,
        "reject_reasons": ["тесты не запускались"],
    }
    state["tasks"] = {"TASK-001": task}
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    data.clear_cache()
    payload = json.loads(get(base, "/api/runs/run-01")[2])
    assert payload["tasks"] == [{
        "task_id": "TASK-001", "stage": "s4_build", "owner": "12-fullstack-developer",
        "reqs": ["REQ-001"], "status": "review", "attempts": 2, "lease": None, "deps": [],
        "reject_reasons": ["тесты не запускались"],
        "allowed_files": ["product/src/a.py", "product/src/b.py", "product/src/c.py"],
    }]
    assert payload["now"]["task_counts"] == {"review": 1}


def test_journal_panel_is_capped_at_fifty_events(tmp_path):
    run_dir, _state, _event = init_run("run-01", "nano", root=tmp_path)
    lines = [json.dumps({"seq": index, "event_type": "INIT", "why": f"event {index}"}) for index in range(80)]
    (run_dir / "blackbox.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload = data.run_payload("run-01", root=tmp_path)
    assert len(payload["journal"]["events"]) == 50
    assert payload["journal"]["events"][-1]["seq"] == 79


def test_usage_panel_exposes_three_groupings_and_the_budget(served):
    base, _root, run_dir = served
    usage = run_dir / "runtime" / "usage.jsonl"
    usage.parent.mkdir(parents=True, exist_ok=True)
    usage.write_text(
        "\n".join([
            json.dumps({"stage": "s1_spec", "role": "01-smart-interviewer", "task_id": "TASK-001",
                        "tokens_in": 100, "tokens_out": 20, "tokens_total": 120, "seconds": 4.5, "usd": None}),
            json.dumps({"stage": "s1_spec", "role": "02-independent-verifier",
                        "tokens_in": 50, "tokens_out": 10, "tokens_total": 60, "seconds": 1.25, "usd": None}),
        ]) + "\n",
        encoding="utf-8",
    )
    data.clear_cache()
    usage_panel = json.loads(get(base, "/api/runs/run-01")[2])["usage"]
    assert usage_panel["totals"]["tokens"] == 180
    assert usage_panel["totals"]["seconds"] == 5.75
    assert usage_panel["totals"]["usd"] is None  # "не измерено", not zero
    assert [group["key"] for group in usage_panel["by_role"]] == ["01-smart-interviewer", "02-independent-verifier"]
    assert [group["key"] for group in usage_panel["by_task"]] == ["-", "TASK-001"]
    assert usage_panel["budget"]["tokens"] > 0


def test_trace_panel_reports_a_missing_spec_instead_of_failing(served):
    base, _root, _run = served
    payload = json.loads(get(base, "/api/runs/run-01")[2])
    assert payload["trace"]["available"] is False
    assert "spec.md" in payload["trace"]["error"]


# -- A11: packaging, CLI, help ---------------------------------------------
def test_static_page_is_reachable_from_the_installed_package():
    from importlib import resources

    page = resources.files("factory.ui") / "static" / "index.html"
    assert page.is_file()
    assert b"<!doctype html>" in page.read_bytes()


def test_cli_ui_help_lists_the_flags():
    from factory.cli import build_parser

    args = build_parser().parse_args(["ui"])
    assert args.func.__name__ == "cmd_ui"
    assert args.ui_command == "serve"
    assert (args.runs_dir, args.host, args.port, args.open) == (None, "127.0.0.1", 8420, False)
    parsed = build_parser().parse_args(["ui", "serve", "--port", "0", "--open", "--runs-dir", "/tmp/runs"])
    assert (parsed.port, parsed.open, parsed.runs_dir) == (0, True, "/tmp/runs")
