"""Read-only HTTP server for the dashboard: `ThreadingHTTPServer`, GET only.

One local operator, one page, no dependencies.  The server never accepts a
write, never reads `FACTORY_APPROVAL_KEY` and never leaves 127.0.0.1 unless the
operator asks for it with `--host`.
"""
from __future__ import annotations

import json
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..errors import FactoryError
from . import data, export
from .data import RunNotFound, FilterError, compare_runs, compare_revision, TIMESERIES_METRICS, TIMESERIES_BUCKETS, compare_artifact_revision, compare_artifact_data, ARTIFACT_NAMES

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8420

# /events/stream: how often the journal file is polled for appends, and how
# often a `: ping` comment is sent so a dead client surfaces as a write error
# instead of a thread that lives forever.
SSE_POLL_INTERVAL_S = 0.25
SSE_HEARTBEAT_INTERVAL_S = 15.0

CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)


def page_bytes() -> bytes:
    return (resources.files("factory.ui") / "static" / "index.html").read_bytes()


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "factory-ui"
    sys_version = ""

    # -- plumbing ---------------------------------------------------------
    @property
    def runs_dir(self) -> str | None:
        return getattr(self.server, "runs_dir", None)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """A local dashboard polled every 2 s does not need an access log."""

    def _send(
        self,
        status: HTTPStatus,
        body: bytes = b"",
        content_type: str = "application/json; charset=utf-8",
        etag: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if status is HTTPStatus.METHOD_NOT_ALLOWED:
            self.send_header("Allow", "GET")
        if etag:
            self.send_header("ETag", f'"{etag}"')
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, status: HTTPStatus, payload: dict[str, Any], etag: str | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, etag=etag)

    def _not_modified(self, etag: str) -> bool:
        offered = self.headers.get("If-None-Match", "")
        if not offered:
            return False
        tags = [item.strip().strip('"') for item in offered.split(",")]
        return "*" in tags or etag in tags

    # -- routes -----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        parts = urlsplit(self.path)
        path = parts.path
        query = parse_qs(parts.query)
        if path in ("/", "/index.html"):
            return self._page()
        if path == "/api/runs":
            return self._runs()
        if path == "/api/export/runs.csv":
            return self._export_runs()
        if path == "/api/compare":
            return self._compare(query)
        if path == "/api/compare/timeseries":
            return self._compare_timeseries(query)
        if path == "/api/compare/artifact":
            return self._compare_artifact(query)
        segments = [segment for segment in path.split("/") if segment]
        if len(segments) >= 3 and segments[0] == "api" and segments[1] == "runs":
            run_id = segments[2]
            if len(segments) == 3:
                return self._run(run_id, query)
            if len(segments) == 4 and segments[3] == "chain":
                return self._chain(run_id, query)
            if len(segments) == 4 and segments[3] == "timeseries":
                return self._timeseries(run_id, query)
            if len(segments) == 4 and segments[3] == "export.xlsx":
                return self._export_xlsx(run_id)
            if len(segments) == 5 and segments[3] == "export":
                return self._export_csv(run_id, segments[4], query)
            if len(segments) == 5 and segments[3] == "events" and segments[4] == "stream":
                return self._events_stream(run_id, query)
            if len(segments) == 5 and segments[3] == "events":
                return self._event(run_id, segments[4])
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found", "path": path})

    def _write_refused(self) -> None:
        self._json(
            HTTPStatus.METHOD_NOT_ALLOWED,
            {"error": "the dashboard is read-only; use the factory CLI to change a run"},
        )

    do_POST = do_PUT = do_DELETE = do_PATCH = _write_refused

    # -- handlers ---------------------------------------------------------
    def _page(self) -> None:
        try:
            body = page_bytes()
        except (OSError, ModuleNotFoundError) as exc:  # a broken install, not a run
            self._send(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"dashboard page is missing from the package: {exc}".encode("utf-8"),
                content_type="text/plain; charset=utf-8",
            )
            return
        self._send(
            HTTPStatus.OK,
            body,
            content_type="text/html; charset=utf-8",
            headers={"Content-Security-Policy": CSP},
        )

    def _runs(self) -> None:
        try:
            payload = data.list_runs(self.runs_dir)
        except FactoryError as exc:
            return self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc), "runs_root": str(self.runs_dir)}
            )
        etag = payload["revision"]
        if self._not_modified(etag):
            return self._not_modified_response(etag)
        self._json(HTTPStatus.OK, payload, etag=etag)

    def _run(self, run_id: str, query: dict[str, list[str]]) -> None:
        # Validate journal filter params first; unknown values -> 400.
        try:
            filters, limit = data.parse_journal_filters(run_id, self.runs_dir, query)
        except data.FilterError as exc:
            return self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": str(exc), "allowed": exc.allowed},
            )
        except data.RunNotFound as exc:
            return self._json(HTTPStatus.NOT_FOUND, {"error": str(exc), "run_id": run_id})
        except FactoryError as exc:
            # A broken state.json must surface as 500, not as a filter error.
            return self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc), "run_id": run_id}
            )
        # An unchanged run costs one small read and three stats, not a payload.
        etag = data.revision(run_id, self.runs_dir, filters=filters)
        if etag and self._not_modified(etag):
            return self._not_modified_response(etag)
        try:
            payload = data.run_payload(run_id, self.runs_dir, filters=filters, limit=limit)
        except data.RunNotFound as exc:
            return self._json(HTTPStatus.NOT_FOUND, {"error": str(exc), "run_id": run_id})
        except FactoryError as exc:
            return self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc), "run_id": run_id}
            )
        etag = payload["revision"]
        if self._not_modified(etag):
            return self._not_modified_response(etag)
        self._json(HTTPStatus.OK, payload, etag=etag)

    def _event(self, run_id: str, seq_str: str) -> None:
        try:
            seq = int(seq_str)
            if seq < 1:
                raise ValueError
        except ValueError:
            return self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": f"seq must be a positive integer, got {seq_str!r}"},
            )
        try:
            event = data.event_by_seq(run_id, seq, self.runs_dir)
        except data.RunNotFound as exc:
            return self._json(HTTPStatus.NOT_FOUND, {"error": str(exc), "run_id": run_id})
        if event is None:
            return self._json(
                HTTPStatus.NOT_FOUND,
                {"error": f"event seq {seq} not found", "run_id": run_id, "seq": seq},
            )
        self._json(HTTPStatus.OK, event)

    def _events_stream(self, run_id: str, query: dict[str, list[str]]) -> None:
        """SSE stream of journal appends for one run (read-only, stdlib only).

        Resume contract: the query ``from_seq`` or the ``Last-Event-ID``
        header selects the first event as ``seq > value``; absent means "only
        events appended after this connection" (the UI already holds the tail
        from the payload, so replaying it would duplicate rows).  ``from_seq=0``
        replays the whole journal.  Frames carry ``id: <seq>`` when the event
        has an integer ``seq`` so the browser reconnect resumes by itself.
        """
        try:
            run_path = data.run_path(run_id, self.runs_dir)
        except data.RunNotFound as exc:
            return self._json(HTTPStatus.NOT_FOUND, {"error": str(exc), "run_id": run_id})
        raw_from = (query.get("from_seq") or [""])[0].strip()
        if not raw_from:
            raw_from = (self.headers.get("Last-Event-ID") or "").strip()
        from_seq = 0
        if raw_from:
            try:
                from_seq = int(raw_from)
                if from_seq < 0:
                    raise ValueError
            except ValueError:
                return self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": f"from_seq must be a non-negative integer, got {raw_from!r}"},
                )
        # Without an explicit resume point the stream starts at the current
        # end of the journal: live appends only.
        if not raw_from:
            from_seq = self._journal_max_seq(run_path)

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        journal = run_path / "blackbox.jsonl"
        offset = 0
        last_beat = time.monotonic()
        try:
            while True:
                events, offset = data.read_events_from(journal, offset)
                for event in events:
                    seq = event.get("seq")
                    if isinstance(seq, int) and seq <= from_seq:
                        continue
                    self._sse_frame(event)
                    last_beat = time.monotonic()
                now = time.monotonic()
                if now - last_beat >= SSE_HEARTBEAT_INTERVAL_S:
                    self.wfile.write(f": ping {int(time.time())}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    last_beat = now
                time.sleep(SSE_POLL_INTERVAL_S)
        except (BrokenPipeError, ConnectionResetError):
            return  # the client went away; nothing to log on a local console
        except FactoryError as exc:
            # The stream is already open, so the error can only ride as an
            # SSE comment; the client's next poll reports it as JSON too.
            try:
                self._sse_comment(f"error: {exc}")
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _sse_frame(self, event: dict[str, Any]) -> None:
        seq = event.get("seq")
        lines = []
        if isinstance(seq, int):
            lines.append(f"id: {seq}")
        lines.append("data: " + json.dumps(event, ensure_ascii=False, separators=(",", ":")))
        self.wfile.write(("\n".join(lines) + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def _sse_comment(self, text: str) -> None:
        for line in text.splitlines() or [""]:
            self.wfile.write(f": {line}\n".encode("utf-8"))
        self.wfile.write(b"\n")
        self.wfile.flush()

    @staticmethod
    def _journal_max_seq(run_path: Path) -> int:
        journal = run_path / "blackbox.jsonl"
        max_seq = 0
        for event in data.tail_events(journal, limit=50):
            seq = event.get("seq")
            if isinstance(seq, int):
                max_seq = max(max_seq, seq)
        return max_seq

    def _compare(self, query: dict[str, list[str]]) -> None:
        run_a = (query.get("a") or [""])[0].strip()
        run_b = (query.get("b") or [""])[0].strip()
        if not run_a or not run_b:
            return self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": "both 'a' and 'b' query parameters are required"},
            )
        if run_a == run_b:
            return self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": "run ids must differ"},
            )
        try:
            etag = compare_revision(run_a, run_b, self.runs_dir)
        except RunNotFound as exc:
            return self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except FactoryError as exc:
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        if etag and self._not_modified(etag):
            return self._not_modified_response(etag)
        try:
            payload = compare_runs(run_a, run_b, self.runs_dir)
        except RunNotFound as exc:
            return self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except ValueError as exc:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except FactoryError as exc:
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        etag = compare_revision(run_a, run_b, self.runs_dir)
        if etag and self._not_modified(etag):
            return self._not_modified_response(etag)
        self._json(HTTPStatus.OK, payload, etag=etag)

    def _compare_timeseries(self, query: dict[str, list[str]]) -> None:
        run_a = (query.get("a") or [""])[0].strip()
        run_b = (query.get("b") or [""])[0].strip()
        if not run_a or not run_b:
            return self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": "both 'a' and 'b' query parameters are required"},
            )
        if run_a == run_b:
            return self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": "run ids must differ"},
            )
        parsed = self._parse_timeseries_query(query)
        if parsed is None:
            return
        metrics, bucket = parsed
        try:
            data.run_path(run_a, self.runs_dir)
            data.run_path(run_b, self.runs_dir)
        except data.RunNotFound as exc:
            return self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        etag = data.compare_timeseries_revision(run_a, run_b, self.runs_dir, metrics, bucket)
        if etag and self._not_modified(etag):
            return self._not_modified_response(etag)
        try:
            payload = data.compare_timeseries_data(run_a, run_b, self.runs_dir, metrics, bucket)
        except data.RunNotFound as exc:
            return self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except ValueError as exc:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except FactoryError as exc:
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        etag = data.compare_timeseries_revision(run_a, run_b, self.runs_dir, metrics, bucket)
        if etag and self._not_modified(etag):
            return self._not_modified_response(etag)
        self._json(HTTPStatus.OK, payload, etag=etag)

    def _compare_artifact(self, query: dict[str, list[str]]) -> None:
        run_a = (query.get("a") or [""])[0].strip()
        run_b = (query.get("b") or [""])[0].strip()
        name = (query.get("name") or ["spec"])[0].strip()
        if not run_a or not run_b:
            return self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": "both 'a' and 'b' query parameters are required"},
            )
        if run_a == run_b:
            return self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": "run ids must differ"},
            )
        if name not in ARTIFACT_NAMES:
            return self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": f"unknown artifact name: {name!r}", "allowed": list(ARTIFACT_NAMES)},
            )
        try:
            etag = compare_artifact_revision(run_a, run_b, self.runs_dir, name)
        except RunNotFound as exc:
            return self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except FactoryError as exc:
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        if etag and self._not_modified(etag):
            return self._not_modified_response(etag)
        try:
            payload = compare_artifact_data(run_a, run_b, self.runs_dir, name)
        except RunNotFound as exc:
            return self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except ValueError as exc:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except FactoryError as exc:
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        etag = compare_artifact_revision(run_a, run_b, self.runs_dir, name)
        if etag and self._not_modified(etag):
            return self._not_modified_response(etag)
        self._json(HTTPStatus.OK, payload, etag=etag)

    def _chain(self, run_id: str, query: dict[str, list[str]]) -> None:
        refresh = (query.get("refresh") or [""])[0] not in ("", "0", "false")
        try:
            report = data.chain_report(run_id, self.runs_dir, refresh=refresh)
        except data.RunNotFound as exc:
            return self._json(HTTPStatus.NOT_FOUND, {"error": str(exc), "run_id": run_id})
        except FactoryError as exc:
            return self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc), "run_id": run_id}
            )
        self._json(HTTPStatus.OK, report)

    def _parse_timeseries_query(self, query: dict[str, list[str]]) -> tuple[list[str], str] | None:
        """Validated ``(metrics, bucket)``; ``None`` after the 400 was written."""
        raw_metrics = (query.get("metrics") or [""])[0].strip()
        if not raw_metrics:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": "metrics parameter is required", "allowed": list(TIMESERIES_METRICS)},
            )
            return None
        metrics = [m.strip() for m in raw_metrics.split(",") if m.strip()]
        invalid_metrics = [m for m in metrics if m not in TIMESERIES_METRICS]
        if invalid_metrics:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": f"unknown metric(s): {', '.join(invalid_metrics)}", "allowed": list(TIMESERIES_METRICS)},
            )
            return None
        bucket = (query.get("bucket") or ["stage"])[0].strip()
        if bucket not in TIMESERIES_BUCKETS:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": f"unknown bucket: {bucket!r}", "allowed": list(TIMESERIES_BUCKETS)},
            )
            return None
        return metrics, bucket

    def _timeseries(self, run_id: str, query: dict[str, list[str]]) -> None:
        parsed = self._parse_timeseries_query(query)
        if parsed is None:
            return
        metrics, bucket = parsed

        # Validate run exists
        try:
            run_path = data.run_path(run_id, self.runs_dir)
        except data.RunNotFound as exc:
            return self._json(HTTPStatus.NOT_FOUND, {"error": str(exc), "run_id": run_id})

        # ETag based on journal stamp + bucket
        etag = data.timeseries_revision(run_id, self.runs_dir, bucket)
        if etag and self._not_modified(etag):
            return self._not_modified_response(etag)

        try:
            payload = data.timeseries_data(run_id, self.runs_dir, metrics, bucket)
        except data.RunNotFound as exc:
            return self._json(HTTPStatus.NOT_FOUND, {"error": str(exc), "run_id": run_id})
        except FactoryError as exc:
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc), "run_id": run_id})

        etag = data.timeseries_revision(run_id, self.runs_dir, bucket)
        if etag and self._not_modified(etag):
            return self._not_modified_response(etag)
        self._json(HTTPStatus.OK, payload, etag=etag)

    # -- exports ----------------------------------------------------------
    def _download(
        self,
        body: bytes,
        filename: str,
        content_type: str = "text/csv; charset=utf-8",
    ) -> None:
        self._send(
            HTTPStatus.OK,
            body,
            content_type=content_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    def _export_runs(self) -> None:
        try:
            fields, rows = export.runs_table(self.runs_dir)
        except FactoryError as exc:
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        self._download(export.csv_bytes(fields, rows), export.csv_filename("runs"))

    def _export_csv(self, run_id: str, name: str, query: dict[str, list[str]]) -> None:
        dataset = name[: -len(".csv")] if name.endswith(".csv") else name
        if dataset not in export.DATASETS:
            return self._json(
                HTTPStatus.NOT_FOUND, {"error": "unknown export dataset", "dataset": dataset}
            )
        try:
            run_path = data.run_path(run_id, self.runs_dir)
        except data.RunNotFound as exc:
            return self._json(HTTPStatus.NOT_FOUND, {"error": str(exc), "run_id": run_id})
        by = (query.get("by") or ["stage"])[0]
        if dataset == "usage" and by not in export.GROUPINGS:
            return self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": f"unknown usage grouping: {by!r} (stage, role or task)", "by": by},
            )
        filters = {}
        limit = None
        if dataset == "journal":
            # Validate filter params (same rules as the payload endpoint).
            try:
                filters = data.read_journal_filters(run_id, self.runs_dir, query)
            except data.FilterError as exc:
                return self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": str(exc), "allowed": exc.allowed},
                )
            except data.RunNotFound as exc:
                return self._json(HTTPStatus.NOT_FOUND, {"error": str(exc), "run_id": run_id})
            raw = (query.get("limit") or [""])[0].strip()
            if raw:
                if not raw.isdigit() or int(raw) < 1:
                    return self._json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": f"limit must be a positive integer, got {raw!r}", "limit": raw},
                    )
                limit = int(raw)
            elif filters:
                # The export must match the screen: when filters are active the
                # payload limit applies.
                limit = data.JOURNAL_LIMIT_DEFAULT
        try:
            if dataset == "journal":
                fields, rows = export.journal_table(run_path, limit, filters=filters)
            else:
                payload = data.run_payload(run_id, self.runs_dir)
                if dataset == "tasks":
                    fields, rows = export.tasks_table(payload)
                elif dataset == "usage":
                    fields, rows = export.usage_table(payload, by)
                else:
                    fields, rows = export.trace_table(payload)
        except data.RunNotFound as exc:
            return self._json(HTTPStatus.NOT_FOUND, {"error": str(exc), "run_id": run_id})
        except FactoryError as exc:
            return self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc), "run_id": run_id}
            )
        self._download(export.csv_bytes(fields, rows), export.csv_filename(dataset, run_id))

    def _export_xlsx(self, run_id: str) -> None:
        try:
            run_path = data.run_path(run_id, self.runs_dir)
            payload = data.run_payload(run_id, self.runs_dir)
            sheets = {
                "Прогон": (export.RUN_FIELDS, [export.run_row(payload)]),
                "Стадии": export.stages_table(payload),
                "Задачи": export.tasks_table(payload),
                "Журнал": export.journal_table(run_path),
                "Расход": export.usage_table(payload, "stage"),
                "Трассировка": export.trace_table(payload),
            }
            body = export.xlsx_bytes(sheets)
        except export.ExportUnavailable as exc:
            return self._json(
                HTTPStatus.NOT_IMPLEMENTED, {"error": str(exc), "extra": "xlsx"}
            )
        except data.RunNotFound as exc:
            return self._json(HTTPStatus.NOT_FOUND, {"error": str(exc), "run_id": run_id})
        except FactoryError as exc:
            return self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc), "run_id": run_id}
            )
        self._download(
            body,
            export.xlsx_filename(run_id),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def _not_modified_response(self, etag: str) -> None:
        self.send_response(HTTPStatus.NOT_MODIFIED)
        self.send_header("ETag", f'"{etag}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    runs_dir: str | None = None


def make_server(
    runs_dir: str | Path | None = None, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT
) -> DashboardServer:
    httpd = DashboardServer((host, port), DashboardHandler)
    httpd.runs_dir = str(Path(runs_dir).expanduser().resolve()) if runs_dir else None
    return httpd


def serve(
    runs_dir: str | Path | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = False,
) -> int:
    httpd = make_server(runs_dir, host, port)
    url = f"http://{host}:{httpd.server_address[1]}/"
    print(f"factory ui: {url}  (read-only; do not expose this port to the network)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nfactory ui: stopped")
    finally:
        httpd.server_close()
    return 0
