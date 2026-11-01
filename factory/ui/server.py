"""Read-only HTTP server for the dashboard: `ThreadingHTTPServer`, GET only.

One local operator, one page, no dependencies.  The server never accepts a
write, never reads `FACTORY_APPROVAL_KEY` and never leaves 127.0.0.1 unless the
operator asks for it with `--host`.
"""
from __future__ import annotations

import json
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..errors import FactoryError
from . import data, export

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8420

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
        segments = [segment for segment in path.split("/") if segment]
        if len(segments) >= 3 and segments[0] == "api" and segments[1] == "runs":
            run_id = segments[2]
            if len(segments) == 3:
                return self._run(run_id)
            if len(segments) == 4 and segments[3] == "chain":
                return self._chain(run_id, query)
            if len(segments) == 4 and segments[3] == "export.xlsx":
                return self._export_xlsx(run_id)
            if len(segments) == 5 and segments[3] == "export":
                return self._export_csv(run_id, segments[4], query)
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

    def _run(self, run_id: str) -> None:
        try:
            etag = data.revision(run_id, self.runs_dir)
        except data.RunNotFound as exc:
            return self._json(HTTPStatus.NOT_FOUND, {"error": str(exc), "run_id": run_id})
        # An unchanged run costs one small read and three stats, not a payload.
        if etag and self._not_modified(etag):
            return self._not_modified_response(etag)
        try:
            payload = data.run_payload(run_id, self.runs_dir)
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
        limit = None
        if dataset == "journal":
            raw = (query.get("limit") or [""])[0]
            if raw:
                if not raw.isdigit() or int(raw) < 1:
                    return self._json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": f"limit must be a positive integer, got {raw!r}", "limit": raw},
                    )
                limit = int(raw)
        try:
            if dataset == "journal":
                fields, rows = export.journal_table(run_path, limit)
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
