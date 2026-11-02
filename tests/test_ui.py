"""The dashboard: payload shape, read-only promise, HTTP surface, chain check.

A1-A8 and A11 of the spec are tested here, M1 export in A12; A9-A10 (drag,
resize, layout import/export) are JS behaviour and are checked in the browser --
the repository has no JS test harness and one file does not justify building one.
"""
import csv
import hashlib
import io
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest

from factory.engine import init_run
from factory.ui import data, export
from factory.ui.server import make_server
from factory.usage_report import build_usage_report

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
        "artifacts": [],
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


def test_metrics_panel_has_all_nine_values_including_honest_nulls():
    payload = data.run_payload("nano-todo", root=EXAMPLE)
    metrics = payload["metrics"]
    assert {
        "first_pass_rate", "rework_rate", "escaped_defect_rate",
        "cycle_time_p50_minutes", "cycle_time_p90_minutes", "cost_per_artifact_usd",
        "top_reject_reasons", "verification_coverage", "traceability_coverage",
    } <= set(metrics)
    assert metrics["escaped_defect_rate"] is None  # "не измерено", not zero
    assert metrics["cost_per_artifact_usd"] is None
    assert metrics["first_pass_rate"] == 1.0
    assert metrics["traceability_coverage"] == 1.0


def test_metrics_panel_is_catalogued_and_visible_in_the_audit_preset():
    text = page_text()
    assert 'const PANEL_IDS = ["p2", "p3", "p4", "p5", "p6", "p7", "p8"];' in text
    assert 'p8: { title: "Метрики", badge: "9" }' in text
    assert 'order: ["p2", "p6", "p8", "p7"]' in text
    assert "renderMetrics(data);" in text
    for label in ("первый проход", "переделки", "утекшие дефекты", "время цикла p50",
                  "время цикла p90", "цена артефакта", "частые отказы",
                  "покрытие проверкой", "покрытие трассировкой"):
        assert label in text


def test_runs_table_scrolls_inside_its_own_container():
    text = page_text()
    assert '<div class="runs-scroll"><table class="runs-table">' in text
    assert ".runs-scroll{overflow-x:auto}" in text


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


def test_cli_ui_passes_the_runs_dir_to_the_server(monkeypatch):
    from factory.cli import build_parser, cmd_ui

    seen = {}
    monkeypatch.setattr("factory.ui.server.serve", lambda **kwargs: seen.update(kwargs) or 0)
    args = build_parser().parse_args(["ui", "--runs-dir", "/tmp/factory-runs"])
    assert cmd_ui(args) == 0
    assert seen["runs_dir"] == "/tmp/factory-runs"


# -- A12: CSV and .xlsx export ---------------------------------------------
def csv_rows(body: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(body.decode("utf-8-sig")), delimiter=";"))


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def test_every_csv_is_utf8_bom_semicolon_and_crlf(served):
    base, _root, _run = served
    paths = [
        "/api/export/runs.csv",
        "/api/runs/run-01/export/journal.csv",
        "/api/runs/run-01/export/tasks.csv",
        "/api/runs/run-01/export/usage.csv",
        "/api/runs/run-01/export/trace.csv",
    ]
    for path in paths:
        status, headers, body = get(base, path)
        assert status == 200, path
        assert headers["Content-Type"] == "text/csv; charset=utf-8", path
        assert headers["Content-Disposition"].startswith("attachment; filename="), path
        # Excel in a Russian locale reads Cyrillic only with the BOM and CRLF.
        assert body[:3] == b"\xef\xbb\xbf", path
        text = body[3:].decode("utf-8")
        assert text.endswith("\r\n"), path
        assert "\n" not in text.replace("\r\n", ""), path


def test_csv_filename_carries_run_dataset_and_date(served):
    base, _root, _run = served
    _status, headers, _body = get(base, "/api/runs/run-01/export/journal.csv")
    assert headers["Content-Disposition"] == f'attachment; filename="run-01-journal-{today()}.csv"'
    _status, headers, _body = get(base, "/api/export/runs.csv")
    assert headers["Content-Disposition"] == f'attachment; filename="runs-{today()}.csv"'


def test_runs_csv_has_one_row_per_run_directory(served):
    base, root, _run = served
    run_two, _state, _event = init_run("run-02", "nano", root=root)
    (run_two / "idea.md").write_text("Второй прогон\n", encoding="utf-8")
    data.clear_cache()
    status, _headers, body = get(base, "/api/export/runs.csv")
    assert status == 200
    rows = csv_rows(body)
    assert rows[0] == list(export.RUN_FIELDS)
    directories = [entry for entry in root.iterdir() if (entry / "state.json").is_file()]
    assert len(rows) - 1 == len(directories) == 2
    by_id = {row[0]: row for row in rows[1:]}
    assert by_id["run-01"][1] == "Нано- todo на stdlib"
    assert by_id["run-02"][1] == "Второй прогон"
    assert by_id["run-01"][4] == "pending"  # stage status from state.json


def test_journal_export_is_the_whole_log_not_the_tail(served):
    base, _root, run_dir = served
    lines = [json.dumps({"seq": index, "event_type": "INIT", "why": f"event {index}"}) for index in range(80)]
    (run_dir / "blackbox.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    state["event_count"] = 80
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    data.clear_cache()
    rows = csv_rows(get(base, "/api/runs/run-01/export/journal.csv")[2])
    assert rows[0] == list(export.JOURNAL_FIELDS)
    assert len(rows) - 1 == 80 == json.loads(get(base, "/api/runs/run-01")[2])["header"]["event_count"]
    # The screen still shows 50; only the file carries all 80.
    assert json.loads(get(base, "/api/runs/run-01")[2])["journal"]["limit"] == 50
    limited = csv_rows(get(base, "/api/runs/run-01/export/journal.csv?limit=7")[2])
    assert [row[0] for row in limited[1:]] == [str(index) for index in range(73, 80)]
    status, _headers, body = get(base, "/api/runs/run-01/export/journal.csv?limit=0")
    assert status == 400 and "positive integer" in json.loads(body)["error"]


def test_journal_filter_hides_fail_outside_the_tail(served):
    base, _root, run_dir = served
    # Build 117 events: first 67 contain FAIL, tail 68..117 are all PASS.
    lines = []
    for index in range(1, 118):
        verdict = "FAIL" if index <= 67 else "PASS"
        stage = "s5_acceptance" if index <= 67 else "s_in"
        lines.append(json.dumps({"seq": index, "event_type": "VERIFY", "stage": stage, "verdict": {"status": verdict}}))
    (run_dir / "blackbox.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    state["event_count"] = 117
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    data.clear_cache()

    # Without filters the tail shows PASS events only.
    p = json.loads(get(base, "/api/runs/run-01")[2])
    assert p["journal"]["matched"] == 117
    assert p["journal"]["shown"] == 50
    assert all(e["verdict"] == "PASS" for e in p["journal"]["events"])

    # With verdict=FAIL the tail is the 67 FAIL events, and the count is 117 total.
    p = json.loads(get(base, "/api/runs/run-01?verdict=FAIL")[2])
    assert p["journal"]["matched"] == 67
    assert p["journal"]["shown"] == 50
    assert p["journal"]["total"] == 117
    assert all(e["verdict"] == "FAIL" for e in p["journal"]["events"])
    assert [e["seq"] for e in p["journal"]["events"]] == list(range(1, 118))[17:67]

    # With verdict=FAIL&stage=s5_acceptance the result is the same.
    p = json.loads(get(base, "/api/runs/run-01?verdict=FAIL&stage=s5_acceptance")[2])
    assert p["journal"]["matched"] == 67

    # With an unknown verdict the response is 400 with an allowed list.
    status, _headers, body = get(base, "/api/runs/run-01?verdict=NOPE")
    assert status == 400
    assert "allowed" in json.loads(body)

    # The limit applies to the filtered result.
    p = json.loads(get(base, "/api/runs/run-01?verdict=FAIL&limit=10")[2])
    assert p["journal"]["shown"] == 10
    assert len(p["journal"]["events"]) == 10

    # ETag differs between filters, and a repeat of the same filter is 304.
    etag_fail = get(base, "/api/runs/run-01?verdict=FAIL")[1]["ETag"]
    etag_all = get(base, "/api/runs/run-01")[1]["ETag"]
    assert etag_fail != etag_all
    status, _headers, _body = get(base, "/api/runs/run-01?verdict=FAIL", headers={"If-None-Match": etag_fail})
    assert status == 304
    status, _headers, _body = get(base, "/api/runs/run-01?verdict=FAIL&stage=s5_acceptance", headers={"If-None-Match": etag_fail})
    assert status == 200

    # The CSV export honors the filter and the limit.
    rows = csv_rows(get(base, "/api/runs/run-01/export/journal.csv?verdict=FAIL")[2])
    assert len(rows) - 1 == 50
    assert all(row[7] == "FAIL" for row in rows[1:])
    rows = csv_rows(get(base, "/api/runs/run-01/export/journal.csv?verdict=FAIL&limit=10")[2])
    assert len(rows) - 1 == 10
    rows = csv_rows(get(base, "/api/runs/run-01/export/journal.csv")[2])
    assert len(rows) - 1 == 117

    # The event-by-seq endpoint returns the full event.
    ev = json.loads(get(base, "/api/runs/run-01/events/35")[2])
    assert ev["seq"] == 35
    assert ev["event_type"] == "VERIFY"
    assert ev["verdict"]["status"] == "FAIL"

    # Bad seqs are rejected.
    status, _headers, _body = get(base, "/api/runs/run-01/events/abc")
    assert status == 400
    status, _headers, _body = get(base, "/api/runs/run-01/events/999999")
    assert status == 404


def test_usage_export_matches_the_usage_report(served):
    base, root, run_dir = served
    usage = run_dir / "runtime" / "usage.jsonl"
    usage.parent.mkdir(parents=True, exist_ok=True)
    usage.write_text(
        "\n".join(
            [
                json.dumps({"stage": "s1_spec", "role": "01-smart-interviewer", "task_id": "TASK-001",
                            "tokens_in": 100, "tokens_out": 20, "tokens_total": 120, "seconds": 4.5, "usd": None}),
                json.dumps({"stage": "s1_spec", "role": "02-independent-verifier",
                            "tokens_in": 50, "tokens_out": 10, "tokens_total": 60, "seconds": 1.25, "usd": None}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    data.clear_cache()
    for query, grouping in (("", "stage"), ("?by=role", "role"), ("?by=task", "task")):
        status, _headers, body = get(base, f"/api/runs/run-01/export/usage.csv{query}")
        assert status == 200
        rows = csv_rows(body)
        assert rows[0] == list(export.USAGE_FIELDS)
        report, _warning = build_usage_report("run-01", group_by=grouping, root=root)
        expected = [
            [group["key"]] + ["" if group[name] is None else str(group[name]) for name in export.USAGE_FIELDS[1:]]
            for group in report["groups"]
        ]
        assert rows[1:] == expected, grouping
    status, _headers, body = get(base, "/api/runs/run-01/export/usage.csv?by=nope")
    assert status == 400
    assert "unknown usage grouping" in json.loads(body)["error"]


def test_tasks_export_has_one_row_per_task_with_joined_lists(served):
    base, _root, run_dir = served
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    state["tasks"] = {
        "TASK-001": {
            "task_id": "TASK-001", "stage": "s4_build", "owner": "12-fullstack-developer",
            "reqs": ["REQ-001", "REQ-002"], "allowed_files": ["product/src/a.py", "product/src/b.py"],
            "deps": [], "status": "review", "attempts": 2, "artifacts": [], "lease": None,
            "reject_reasons": ["тесты не запускались"],
        }
    }
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    data.clear_cache()
    rows = csv_rows(get(base, "/api/runs/run-01/export/tasks.csv")[2])
    assert rows[0] == list(export.TASK_FIELDS)
    assert len(rows) == 2
    row = dict(zip(rows[0], rows[1]))
    assert row["reqs"] == "REQ-001; REQ-002"
    assert row["allowed_files"] == "product/src/a.py; product/src/b.py"
    assert row["deps"] == ""  # null, not the word "none"
    assert row["reject_reasons"] == "тесты не запускались"


def test_trace_export_has_one_row_per_requirement():
    payload = data.run_payload("nano-todo", root=EXAMPLE)
    fields, rows = export.trace_table(payload)
    assert fields == export.TRACE_FIELDS
    assert len(rows) == payload["trace"]["total"] == 3
    parsed = csv_rows(export.csv_bytes(fields, rows))
    assert parsed[0] == list(fields)
    assert [row[0] for row in parsed[1:]] == [row["req"] for row in rows]


def test_trace_export_without_a_spec_is_a_header_only_csv(served):
    base, _root, _run = served
    status, _headers, body = get(base, "/api/runs/run-01/export/trace.csv")
    assert status == 200
    assert csv_rows(body) == [list(export.TRACE_FIELDS)]


def test_workbook_has_six_sheets_and_the_csv_headers(served):
    openpyxl = pytest.importorskip("openpyxl")
    base, _root, _run = served
    status, headers, body = get(base, "/api/runs/run-01/export.xlsx")
    assert status == 200
    assert headers["Content-Type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert headers["Content-Disposition"] == f'attachment; filename="run-01-{today()}.xlsx"'
    book = openpyxl.load_workbook(io.BytesIO(body))
    assert book.sheetnames == ["Прогон", "Стадии", "Задачи", "Журнал", "Расход", "Трассировка"]
    for sheet, fields in (
        ("Прогон", export.RUN_FIELDS),
        ("Стадии", export.STAGE_FIELDS),
        ("Задачи", export.TASK_FIELDS),
        ("Журнал", export.JOURNAL_FIELDS),
        ("Расход", export.USAGE_FIELDS),
        ("Трассировка", export.TRACE_FIELDS),
    ):
        assert [cell.value for cell in book[sheet][1]] == list(fields), sheet
        assert book[sheet].freeze_panes == "A2", sheet
    # A null is an empty cell, not the string "None".
    journal = {cell.value: book["Журнал"].cell(row=2, column=index).value for index, cell in enumerate(book["Журнал"][1], 1)}
    assert journal["task_id"] is None


def test_xlsx_without_openpyxl_is_501_with_the_extra_name(served, monkeypatch):
    base, _root, _run = served
    monkeypatch.setitem(sys.modules, "openpyxl", None)
    status, headers, body = get(base, "/api/runs/run-01/export.xlsx")
    assert status == 501
    assert headers["Content-Type"].startswith("application/json")
    assert "autonomous-sw-factory[xlsx]" in json.loads(body)["error"]


def test_exports_leave_the_run_byte_identical(served):
    base, _root, run_dir = served
    usage = run_dir / "runtime" / "usage.jsonl"
    usage.parent.mkdir(parents=True, exist_ok=True)
    usage.write_text(json.dumps({"stage": "s1_spec", "tokens_total": 10, "seconds": 1.5}) + "\n", encoding="utf-8")
    data.clear_cache()
    watched = [run_dir / "state.json", run_dir / "blackbox.jsonl", usage]

    def digest():
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in watched}

    before = digest()
    for _ in range(2):
        for path in (
            "/api/export/runs.csv",
            "/api/runs/run-01/export/journal.csv",
            "/api/runs/run-01/export/tasks.csv",
            "/api/runs/run-01/export/usage.csv",
            "/api/runs/run-01/export/trace.csv",
        ):
            assert get(base, path)[0] == 200, path
        # The CI `build` job has no openpyxl: a 501 must not touch the run either.
        assert get(base, "/api/runs/run-01/export.xlsx")[0] in (200, 501)
    assert digest() == before


def test_export_is_one_icon_menu_in_the_header():
    text = page_text()
    assert text.count('id="menu-export"') == 1
    assert 'aria-label="Экспорт ▾"' in text
    assert text.count('id="btn-export"') == 1
    for dataset in ("journal", "tasks", "usage", "trace", "xlsx", "runs"):
        assert f'data-export="{dataset}"' in text
    # The icon keeps the bar on one line: no text button next to it.
    assert ".btn.icon{width:28px;height:28px" in text


# -- A13: compare endpoint --------------------------------------------------
def test_compare_requires_both_params(served):
    base, _root, _run = served
    status, _headers, body = get(base, "/api/compare")
    assert status == 400
    assert "both 'a' and 'b'" in json.loads(body)["error"]
    status, _headers, body = get(base, "/api/compare?a=run-01")
    assert status == 400
    status, _headers, body = get(base, "/api/compare?b=run-01")
    assert status == 400


def test_compare_same_run_returns_400(served):
    base, _root, _run = served
    status, _headers, body = get(base, "/api/compare?a=run-01&b=run-01")
    assert status == 400
    assert "differ" in json.loads(body)["error"]


def test_compare_unknown_run_returns_404(served):
    base, _root, _run = served
    status, _headers, body = get(base, "/api/compare?a=run-01&b=nonexistent")
    assert status == 404


def test_compare_returns_deltas(served):
    base, root, _run = served
    # Create a second run with some data
    run2_dir, _state2, _event2 = init_run("run-02", "nano", root=root)
    (run2_dir / "idea.md").write_text("Второй прогон\n", encoding="utf-8")
    # Add some usage data to both runs
    for run_id in ("run-01", "run-02"):
        usage = root / run_id / "runtime" / "usage.jsonl"
        usage.parent.mkdir(parents=True, exist_ok=True)
        usage.write_text(
            json.dumps({
                "stage": "s1_spec",
                "role": "01-smart-interviewer",
                "task_id": "TASK-001",
                "tokens_in": 100,
                "tokens_out": 20,
                "tokens_total": 120,
                "seconds": 4.5,
                "usd": None,
            }) + "\n",
            encoding="utf-8",
        )
    data.clear_cache()

    status, headers, body = get(base, "/api/compare?a=run-01&b=run-02")
    assert status == 200
    payload = json.loads(body)
    assert payload["a"]["run_id"] == "run-01"
    assert payload["b"]["run_id"] == "run-02"
    assert "metrics" in payload
    assert "stages" in payload
    assert "trace" in payload
    assert "totals" in payload
    # ETag should be present
    assert headers.get("ETag")

    # 304 on repeat with same ETag
    etag = headers["ETag"]
    status, _headers, _body = get(base, "/api/compare?a=run-01&b=run-02", headers={"If-None-Match": etag})
    assert status == 304


def test_compare_ui_elements_in_page():
    text = page_text()
    # Compare view section
    assert 'id="view-compare"' in text
    assert 'id="c-title"' in text
    assert 'id="c-metrics-body"' in text
    assert 'id="c-totals-body"' in text
    assert 'id="c-stages-body"' in text
    assert 'id="c-trace-body"' in text
    assert 'id="c-back"' in text
    assert 'id="c-pause"' in text
    assert 'id="c-dot"' in text
    assert 'id="c-live"' in text
    # Compare button in runs view
    assert 'id="btn-compare"' in text
    # Checkbox class for selection
    assert 'class="compare-checkbox"' in text
    # Delta badge function
    assert "function deltaBadge" in text
    assert "loadCompare" in text
    assert "renderCompare" in text
