"""Credential proxy for a live executor (PR-C2).

The executor must reach a model provider; the operator's key must not appear
in the sandbox.  The sandbox therefore talks to a local HTTP port that exists
only inside its network namespace, and the host does the provider call:

    hermes --(HTTP, throw-away token)--> bridge :PORT  (sandbox netns)
                                            |
                                      Unix socket
                                            |
    host credential proxy --(HTTPS, real key)--> allowlisted provider

The bridge is a byte pump: it holds no key and can only reach the socket that
`bwrap` mounted into the sandbox.  The host proxy checks the target host against
`credential_proxy.allowlist` and the path against the operator's configured
base path, replaces the sandbox token with the real credential (from the
operator's environment or `~/.hermes/.env`), opens its own TLS connection and
streams the response back -- SSE included.  A request to any other host is
refused before a byte leaves the machine.

The sandbox has `--unshare-net`, so the bridge is the only reachable address.
Its token is random per step, is compared in constant time, and is useless
once the proxy process exits.  The provider key is never written to the run
directory, the sandbox environment, or evidence.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import secrets
import shutil
import socketserver
import ssl
import tempfile
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import yaml

from .config import CREDENTIAL_PROXY_DEFAULT_KEYS, load_budget
from .runtime.base import RuntimeFailure

BRIDGE_MOUNT = "/tmp/factory-proxy"
BRIDGE_SCRIPT = "bridge.py"
SOCKET_NAME = "proxy.sock"
CONFIG_NAME = "config.yaml"
TOKEN_PREFIX = "factory-proxy-key-"
MAX_BODY = 32 * 1024 * 1024
TIMEOUT_S = 600.0
HOP_BY_HOP = frozenset(
    {
        "connection",
        "proxy-connection",
        "keep-alive",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "proxy-authorization",
        "proxy-authenticate",
        "content-length",
    }
)
AUTH_HEADERS = ("authorization", "x-api-key", "api-key", "x-goog-api-key")
ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


@dataclass(frozen=True)
class ProxySettings:
    """One upstream, one allowlist, one sandbox port."""

    port: int
    allowlist: tuple[str, ...]
    keys: dict[str, str]
    upstream_host: str
    upstream_port: int
    upstream_path: str
    model: str
    api_mode: str
    transport: str
    real_key: str = field(repr=False)
    token: str = field(repr=False)

    @property
    def path_prefix(self) -> str:
        return self.upstream_path.rstrip("/")

    @property
    def sandbox_base_url(self) -> str:
        return f"http://{self.upstream_host}{self.path_prefix}"

    @property
    def real_key_env(self) -> str:
        return self.keys.get(self.upstream_host, "")


def operator_config_path() -> Path | None:
    """The operator's Hermes profile config, not a copy inside a run."""
    explicit = os.environ.get("HERMES_CONFIG_PATH", "").strip()
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    home = os.environ.get("HERMES_HOME", "").strip()
    if home:
        candidates.append(Path(home).expanduser() / "config.yaml")
    candidates.append(Path.home() / ".hermes" / "config.yaml")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _dotenv_value(name: str) -> str:
    config = operator_config_path()
    path = (config.parent / ".env") if config is not None else (Path.home() / ".hermes" / ".env")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        item = line.strip()
        if not item or item.startswith("#") or "=" not in item:
            continue
        if item.startswith("export "):
            item = item[len("export ") :].lstrip()
        key, _, value = item.partition("=")
        if key.strip() != name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        return value
    return ""


def quote_yaml(value: str) -> str:
    """A JSON string is a valid YAML double-quoted scalar; use one."""
    return json.dumps(str(value), ensure_ascii=False)


def _transport_for(api_mode: str, host: str) -> str:
    if api_mode == "anthropic_messages" or host == "api.anthropic.com":
        return "anthropic_messages"
    if api_mode == "codex_responses":
        return "codex_responses"
    return "openai_chat"
def _operator_document() -> dict[str, Any]:
    path = operator_config_path()
    if path is None:
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeFailure(f"credential proxy: cannot read operator config {path}: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _validate_endpoint(parts: Any) -> None:
    if parts.scheme != "https":
        raise RuntimeFailure("credential proxy: upstream base URL must use https")
    if not parts.hostname:
        raise RuntimeFailure("credential proxy: upstream base URL has no host")
    if parts.username or parts.password:
        raise RuntimeFailure("credential proxy: upstream base URL may not carry user info")
    if parts.query or parts.fragment:
        raise RuntimeFailure("credential proxy: upstream base URL may not carry query or fragment")
    if parts.port not in (None, 443):
        raise RuntimeFailure("credential proxy: upstream base URL must use the default HTTPS port")


def load_settings(model: str | None = None) -> ProxySettings:
    """Resolve the budget policy and the operator's upstream credential.

    The credential is read here, in the host process, and kept in memory.  It
    is never put into the generated config: that file carries only the
    throw-away token the sandbox is allowed to see.
    """
    budget = load_budget()
    section = budget.get("credential_proxy")
    if not isinstance(section, dict):
        raise RuntimeFailure(
            "credential proxy is not configured: add `credential_proxy` to factory/budget.yaml"
        )
    port = int(section["port"])
    allowlist = tuple(str(host).lower() for host in section["allowlist"])
    keys = {str(host).lower(): str(name) for host, name in (section.get("keys") or {}).items()}

    document = _operator_document()
    model_cfg = document.get("model") if isinstance(document.get("model"), dict) else {}
    providers_cfg = document.get("providers") if isinstance(document.get("providers"), dict) else {}
    provider = str(model_cfg.get("provider") or "").strip()
    provider_key = provider.split(":", 1)[1] if provider.startswith("custom:") else provider
    entry = providers_cfg.get(provider_key) if isinstance(providers_cfg.get(provider_key), dict) else {}
    base = os.environ.get("FACTORY_PROXY_UPSTREAM", "").strip() or str(
        model_cfg.get("base_url") or entry.get("api") or entry.get("base_url") or entry.get("url") or ""
    ).strip()
    if not model:
        model = os.environ.get("FACTORY_PROXY_MODEL", "").strip() or str(
            model_cfg.get("default") or model_cfg.get("model") or entry.get("default_model") or ""
        ).strip()
    api_mode = str(model_cfg.get("api_mode") or "chat_completions").strip() or "chat_completions"

    parts = urlsplit(base)
    _validate_endpoint(parts)
    host = str(parts.hostname).lower()
    if host not in allowlist:
        raise RuntimeFailure(
            f"credential proxy: {host} is not in credential_proxy.allowlist "
            "(factory/budget.yaml); add it or switch the operator model"
        )
    env_name = keys.get(host) or str(entry.get("key_env") or "") or CREDENTIAL_PROXY_DEFAULT_KEYS.get(host, "")
    if not env_name:
        raise RuntimeFailure(
            f"credential proxy: no key_env for {host}; add "
            f"credential_proxy.keys.{host} to factory/budget.yaml"
        )

    key = os.environ.get("FACTORY_PROXY_API_KEY", "").strip() or str(
        model_cfg.get("api_key") or entry.get("api_key") or ""
    ).strip()
    key = key or os.environ.get(env_name, "").strip() or _dotenv_value(env_name)
    if not key:
        raise RuntimeFailure(
            f"credential proxy: {env_name} is empty and the operator config has no model.api_key"
        )
    if not model:
        raise RuntimeFailure(
            "credential proxy: no model; pass --executor-model or set model.default in the operator config"
        )

    return ProxySettings(
        port=port,
        allowlist=allowlist,
        keys=keys,
        upstream_host=host,
        upstream_port=parts.port or 443,
        upstream_path=parts.path.rstrip("/") or "/",
        model=str(model),
        api_mode=api_mode,
        transport=_transport_for(api_mode, host),
        real_key=key,
        token=TOKEN_PREFIX + secrets.token_urlsafe(32),
    )


def render_hermes_config(settings: ProxySettings) -> str:
    """A minimal Hermes config the sandbox can read; the key is the token."""
    config_version = ""
    path = operator_config_path()
    if path is not None:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            document = {}
        if isinstance(document, dict):
            config_version = str(document.get("_config_version") or "")
    lines = [
        "# Generated by the factory for one executor step.",
        "# The api_key is a throw-away proxy token, not a provider credential.",
        "model:",
        '  provider: "custom:factory-proxy"',
        f"  default: {quote_yaml(settings.model)}",
        f"  base_url: {quote_yaml(settings.sandbox_base_url)}",
        f"  api_mode: {quote_yaml(settings.api_mode)}",
        f"  api_key: {quote_yaml(settings.token)}",
        "providers:",
        "  factory-proxy:",
        f"    api: {quote_yaml(settings.sandbox_base_url)}",
        f"    api_key: {quote_yaml(settings.token)}",
        f"    transport: {quote_yaml(settings.transport)}",
        f"    default_model: {quote_yaml(settings.model)}",
    ]
    if config_version:
        lines.insert(0, f"_config_version: {quote_yaml(config_version)}")
        lines.insert(1, "")
    return "\n".join(lines) + "\n"


def _allowed_path(path: str, prefix: str) -> bool:
    return not prefix or path == prefix or path.startswith(prefix + "/")
class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, path: str, settings: ProxySettings) -> None:
        self.settings = settings
        self.request_queue_size = 64
        super().__init__(path, _Handler)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "factory-credential-proxy"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _fail(self, code: int, message: str) -> None:
        self.send_error(code, message)
        self.close_connection = True

    def _handle(self) -> None:
        settings = self.server.settings
        self._headers_sent = False
        method = self.command.upper()
        if method == "CONNECT":
            self._fail(405, "CONNECT is disabled: the proxy has no tunnel mode")
            return
        if method not in ALLOWED_METHODS:
            self._fail(405, f"method {method} is not allowed")
            return

        parts = urlsplit(self.path)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            self._fail(400, "an absolute-form http:// URL is required")
            return
        host = parts.hostname.lower().rstrip(".")
        if host not in settings.allowlist:
            self._fail(403, f"host {host!r} is not in the credential proxy allowlist")
            return
        path = parts.path or "/"
        if not _allowed_path(path, settings.path_prefix):
            self._fail(403, f"path {path!r} is outside the configured upstream prefix")
            return
        if parts.fragment:
            self._fail(400, "fragment is not allowed in a request target")
            return

        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) > 1:
            self._fail(400, "multiple Content-Length headers")
            return
        if self.headers.get("Transfer-Encoding"):
            self._fail(411, "chunked request bodies are not accepted")
            return
        length = 0
        if lengths:
            try:
                length = int(lengths[0])
            except ValueError:
                self._fail(400, "invalid Content-Length")
                return
            if length < 0 or length > MAX_BODY:
                self._fail(413, "request body is too large")
                return

        query = parts.query
        token = settings.token
        token_in_query = token in query or quote(token, safe="") in query
        auth_values = []
        for name in AUTH_HEADERS:
            auth_values.extend(self.headers.get_all(name) or [])
        token_in_header = any(token in value for value in auth_values)
        if not token_in_query and not token_in_header:
            self._fail(401, "missing the factory proxy token")
            return
        if token_in_query and not token_in_header:
            query = query.replace(quote(token, safe=""), quote(settings.real_key, safe=""))
            query = query.replace(token, settings.real_key)

        body = self.rfile.read(length) if length else b""
        if len(body) != length:
            self._fail(400, "request body ended early")
            return

        connection_tokens = {
            token.strip().lower()
            for value in self.headers.get_all("Connection", [])
            for token in value.split(",")
            if token.strip()
        }
        forwarded: list[tuple[str, str]] = []
        for name, value in self.headers.items():
            lower = name.lower()
            if lower in HOP_BY_HOP or lower in AUTH_HEADERS or lower == "host":
                continue
            if lower in connection_tokens:
                continue
            if "\r" in value or "\n" in value:
                self._fail(400, "header value carries a newline")
                return
            forwarded.append((name, value))
        forwarded.append(("Connection", "close"))

        if host == "api.anthropic.com":
            forwarded.append(("x-api-key", settings.real_key))
        elif host == "generativelanguage.googleapis.com":
            forwarded.append(("x-goog-api-key", settings.real_key))
        else:
            forwarded.append(("Authorization", f"Bearer {settings.real_key}"))

        target = path + (f"?{query}" if query else "")
        connection = http.client.HTTPSConnection(
            settings.upstream_host,
            settings.upstream_port,
            timeout=TIMEOUT_S,
            context=ssl.create_default_context(),
        )
        try:
            connection.putrequest(method, target, skip_host=True, skip_accept_encoding=True)
            connection.putheader("Host", settings.upstream_host)
            for name, value in forwarded:
                connection.putheader(name, value)
            connection.putheader("Content-Length", str(len(body)))
            connection.endheaders()
            if body:
                connection.send(body)

            response = connection.getresponse()
            self.send_response_only(response.status, response.reason)
            for name, value in response.getheaders():
                lower = name.lower()
                if lower in HOP_BY_HOP and lower != "content-length":
                    continue
                if lower in {"content-length", "transfer-encoding", "connection"}:
                    continue
                self.send_header(name, value)
            self.send_header("Connection", "close")
            self.end_headers()
            self._headers_sent = True
            while True:
                chunk = response.read1(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (OSError, http.client.HTTPException) as exc:
            if not self._headers_sent:
                self._fail(502, f"upstream request failed: {type(exc).__name__}")
        finally:
            connection.close()
            self.close_connection = True

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_PATCH = _handle
    do_DELETE = _handle
    do_HEAD = _handle
    do_OPTIONS = _handle


class CredentialProxy:
    """Run the host-side proxy for one step and describe the sandbox side."""

    def __init__(self, settings: ProxySettings) -> None:
        self.settings = settings
        self.directory: Path | None = None
        self.config_path: Path | None = None
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "CredentialProxy":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def start(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="factory-credential-proxy-"))
        os.chmod(directory, 0o700)
        self.directory = directory
        self.config_path = directory / CONFIG_NAME
        self.config_path.write_text(render_hermes_config(self.settings), encoding="utf-8")
        os.chmod(self.config_path, 0o600)
        bridge = directory / BRIDGE_SCRIPT
        source = Path(__file__).with_name("runtime") / "proxy_bridge.py"
        shutil.copyfile(source, bridge)
        os.chmod(bridge, 0o644)
        socket_path = directory / SOCKET_NAME
        self._server = _Server(str(socket_path), self.settings)
        os.chmod(socket_path, 0o600)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if self.directory is not None:
            shutil.rmtree(self.directory, ignore_errors=True)
            self.directory = None
        self.config_path = None

    def environment(self) -> dict[str, str]:
        """The env the sandboxed Hermes sees: a proxy, not a key."""
        address = f"http://127.0.0.1:{self.settings.port}"
        return {
            "HTTP_PROXY": address,
            "http_proxy": address,
            "NO_PROXY": "",
            "no_proxy": "",
        }

    def wrapper(self, command: list[str]) -> list[str]:
        return [
            "python3",
            f"{BRIDGE_MOUNT}/{BRIDGE_SCRIPT}",
            "--unix",
            f"{BRIDGE_MOUNT}/{SOCKET_NAME}",
            "--port",
            str(self.settings.port),
            "--",
            *command,
        ]

    def config_and_socket(self) -> tuple[Path, Path]:
        if self.directory is None or self.config_path is None:
            raise RuntimeFailure("credential proxy has not been started")
        return self.config_path, self.directory / SOCKET_NAME


def start_credential_proxy(model: str | None = None) -> CredentialProxy:
    """Resolve the operator credential and start the one-step proxy."""
    return CredentialProxy(load_settings(model))

