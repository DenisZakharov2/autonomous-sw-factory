"""Read-only surface of the factory: a dashboard served over HTTP.

`data` builds the JSON payload from the engine's own read functions, `server`
serves it on 127.0.0.1.  Nothing in this package writes to a run.
"""
from __future__ import annotations

from .data import chain_report, list_runs, run_payload
from .server import make_server, serve

__all__ = ["chain_report", "list_runs", "run_payload", "make_server", "serve"]
