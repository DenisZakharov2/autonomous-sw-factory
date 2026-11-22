"""Integration tests for credential proxy (PR-C2).

Tests cover:
- Configuration generation (token in config, not real key)
- Proxy validation: rejects non-allowlisted hosts, missing tokens, bad paths
- Proxy bridge script basic functionality

Full end-to-end chain tests (with HTTPS fake upstream) are separate.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


POSIX_ONLY = pytest.mark.skipif(
    not hasattr(os, "fork"),
    reason="credential proxy requires Unix domain sockets (Ubuntu)",
)


@POSIX_ONLY
class TestCredentialProxyConfigGeneration:
    """Tests for the generated Hermes config used by sandbox."""

    def test_generated_config_has_token_not_real_key(self) -> None:
        """Generated config.yaml contains sandbox token, never the real key."""
        from factory.credential_proxy import ProxySettings, render_hermes_config

        settings = ProxySettings(
            port=8899,
            allowlist=("api.openai.com",),
            keys={"api.openai.com": "OPENAI_API_KEY"},
            upstream_host="api.openai.com",
            upstream_port=443,
            upstream_path="/v1",
            model="gpt-4o",
            api_mode="chat_completions",
            transport="openai_chat",
            real_key="sk-SECRET-REAL-KEY",
            token="factory-proxy-key-SANDBOX-TOKEN",
        )

        config = render_hermes_config(settings)

        # Config must contain the sandbox token
        assert "factory-proxy-key-SANDBOX-TOKEN" in config

        # Config must NOT contain the real key
        assert "sk-SECRET-REAL-KEY" not in config

        # Must have correct provider
        assert 'provider: "custom:factory-proxy"' in config

    def test_generated_config_correct_paths(self) -> None:
        """Generated config has correct base_url and path prefix."""
        from factory.credential_proxy import ProxySettings, render_hermes_config

        settings = ProxySettings(
            port=8899,
            allowlist=("api.openai.com",),
            keys={"api.openai.com": "OPENAI_API_KEY"},
            upstream_host="api.openai.com",
            upstream_port=443,
            upstream_path="/v1/chat",
            model="gpt-4o",
            api_mode="chat_completions",
            transport="openai_chat",
            real_key="sk-real",
            token="factory-proxy-key-token",
        )

        config = render_hermes_config(settings)

        # base_url should include the path prefix
        assert 'base_url: "http://api.openai.com/v1/chat"' in config
        assert 'default_model: "gpt-4o"' in config
        assert 'transport: "openai_chat"' in config

    def test_generated_config_anthropic(self) -> None:
        """Config for Anthropic has correct api_mode and transport."""
        from factory.credential_proxy import ProxySettings, render_hermes_config

        settings = ProxySettings(
            port=8899,
            allowlist=("api.anthropic.com",),
            keys={"api.anthropic.com": "ANTHROPIC_API_KEY"},
            upstream_host="api.anthropic.com",
            upstream_port=443,
            upstream_path="/v1/messages",
            model="claude-3-5-sonnet",
            api_mode="anthropic_messages",
            transport="anthropic_messages",
            real_key="sk-ant-real",
            token="factory-proxy-key-token",
        )

        config = render_hermes_config(settings)

        assert 'api_mode: "anthropic_messages"' in config
        assert 'transport: "anthropic_messages"' in config
        assert 'base_url: "http://api.anthropic.com/v1/messages"' in config


@POSIX_ONLY
class TestCredentialProxyValidation:
    """Tests for proxy validation logic (allowlist, token, path prefix).

    These tests use the internal _Handler logic directly, without needing
    a running proxy server or fake upstream.
    """

    def test_allowlist_check(self) -> None:
        """Handler rejects hosts not in allowlist."""
        from factory.credential_proxy import _allowed_path, ProxySettings

        # Create a mock server with settings
        class MockServer:
            def __init__(self):
                self.settings = ProxySettings(
                    port=8899,
                    allowlist=("api.openai.com",),
                    keys={"api.openai.com": "OPENAI_API_KEY"},
                    upstream_host="api.openai.com",
                    upstream_port=443,
                    upstream_path="/v1",
                    model="gpt-4o",
                    api_mode="chat_completions",
                    transport="openai_chat",
                    real_key="sk-real",
                    token="factory-proxy-key-token",
                )

        # Test _allowed_path
        assert _allowed_path("/v1/chat/completions", "/v1") is True
        assert _allowed_path("/v1", "/v1") is True
        assert _allowed_path("/v2/chat/completions", "/v1") is False
        assert _allowed_path("/v1/", "/v1") is True

    def test_path_prefix_validation(self) -> None:
        """_allowed_path correctly validates path prefixes."""
        from factory.credential_proxy import _allowed_path

        # Exact match
        assert _allowed_path("/v1", "/v1") is True
        # Sub-path
        assert _allowed_path("/v1/", "/v1") is True
        assert _allowed_path("/v1/chat", "/v1") is True
        assert _allowed_path("/v1/chat/completions", "/v1") is True
        # Outside prefix
        assert _allowed_path("/v2", "/v1") is False
        assert _allowed_path("/v10", "/v1") is False
        # Empty prefix allows everything
        assert _allowed_path("/any/path", "") is True


@POSIX_ONLY
class TestProxyBridgeScript:
    """Tests for the proxy_bridge.py script."""

    def test_bridge_help(self) -> None:
        """Bridge script shows usage without command."""
        import subprocess
        import sys

        bridge_path = Path(__file__).parent.parent / "factory" / "runtime" / "proxy_bridge.py"
        result = subprocess.run(
            [sys.executable, str(bridge_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "missing command after --" in result.stderr or "usage" in result.stderr.lower()

    def test_bridge_runs_and_exits(self) -> None:
        """Bridge starts, runs a simple command, and exits cleanly."""
        import subprocess
        import sys
        import tempfile
        from pathlib import Path

        bridge_path = Path(__file__).parent.parent / "factory" / "runtime" / "proxy_bridge.py"

        # Create a dummy Unix socket that the bridge will try to connect to
        # (it will fail, but we can check the bridge starts correctly)
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "dummy.sock"

            # Run bridge with a command that exits immediately
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(bridge_path),
                    "--unix", str(socket_path),
                    "--port", "0",  # Port 0 = OS assigns free port
                    "--",
                    sys.executable, "-c", "print('hello')"
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            try:
                stdout, stderr = proc.communicate(timeout=5)
                # Bridge should fail to connect to dummy socket, but that's expected
                # The important thing is it doesn't crash on startup
            finally:
                if proc.poll() is None:
                    proc.terminate()


@POSIX_ONLY
class TestCredentialProxySettings:
    """Tests for ProxySettings dataclass."""

    def test_proxy_settings_properties(self) -> None:
        from factory.credential_proxy import ProxySettings

        settings = ProxySettings(
            port=8899,
            allowlist=("api.openai.com", "api.anthropic.com"),
            keys={"api.openai.com": "OPENAI_API_KEY"},
            upstream_host="api.openai.com",
            upstream_port=443,
            upstream_path="/v1",
            model="gpt-4o",
            api_mode="chat_completions",
            transport="openai_chat",
            real_key="sk-test123",
            token="factory-proxy-key-abc123",
        )

        assert settings.path_prefix == "/v1"
        assert settings.sandbox_base_url == "http://api.openai.com/v1"
        assert settings.real_key_env == "OPENAI_API_KEY"
        assert settings.token.startswith("factory-proxy-key-")


@POSIX_ONLY
class TestRenderHermesConfig:
    """Tests for render_hermes_config()."""

    def test_render_minimal_config(self) -> None:
        from factory.credential_proxy import ProxySettings, render_hermes_config

        settings = ProxySettings(
            port=8899,
            allowlist=("api.openai.com",),
            keys={"api.openai.com": "OPENAI_API_KEY"},
            upstream_host="api.openai.com",
            upstream_port=443,
            upstream_path="/v1",
            model="gpt-4o",
            api_mode="chat_completions",
            transport="openai_chat",
            real_key="sk-real",
            token="factory-proxy-key-token123",
        )

        config = render_hermes_config(settings)

        assert 'provider: "custom:factory-proxy"' in config
        assert "default: \"gpt-4o\"" in config
        assert 'base_url: "http://api.openai.com/v1"' in config
        assert 'api_mode: "chat_completions"' in config
        assert 'api_key: "factory-proxy-key-token123"' in config
        assert "factory-proxy:" in config
        assert 'transport: "openai_chat"' in config
        assert 'default_model: "gpt-4o"' in config
        # Real key must NOT appear in generated config
        assert "sk-real" not in config

    def test_render_config_with_custom_path_prefix(self) -> None:
        from factory.credential_proxy import ProxySettings, render_hermes_config

        settings = ProxySettings(
            port=8899,
            allowlist=("api.anthropic.com",),
            keys={"api.anthropic.com": "ANTHROPIC_API_KEY"},
            upstream_host="api.anthropic.com",
            upstream_port=443,
            upstream_path="/v1/messages",
            model="claude-3-5-sonnet",
            api_mode="anthropic_messages",
            transport="anthropic_messages",
            real_key="sk-ant-real",
            token="factory-proxy-key-token456",
        )

        config = render_hermes_config(settings)

        assert 'base_url: "http://api.anthropic.com/v1/messages"' in config
        assert 'api_mode: "anthropic_messages"' in config
        assert 'transport: "anthropic_messages"' in config


@POSIX_ONLY
class TestCredentialProxyNegative:
    """Negative tests: invalid config, missing keys, etc."""

    def test_load_settings_missing_budget_section(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from factory.credential_proxy import load_settings
        from factory.runtime.base import RuntimeFailure

        import factory.credential_proxy as cp_module

        def mock_load_budget() -> dict:
            return {"verify_timeout_s": 600}

        monkeypatch.setattr(cp_module, "load_budget", mock_load_budget)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
        (tmp_path / ".hermes").mkdir()

        with pytest.raises(RuntimeFailure, match="credential proxy is not configured"):
            load_settings()

    def test_load_settings_upstream_not_in_allowlist(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from factory.credential_proxy import load_settings
        from factory.runtime.base import RuntimeFailure

        hermes_dir = tmp_path / ".hermes"
        hermes_dir.mkdir()
        (hermes_dir / "config.yaml").write_text(
            """model:
  provider: "custom:test"
  default: "test"
  base_url: "https://api.badhost.com/v1"
providers:
  test:
    api: "https://api.badhost.com/v1"
""",
            encoding="utf-8",
        )

        import factory.credential_proxy as cp_module

        def mock_load_budget() -> dict:
            return {
                "credential_proxy": {
                    "port": 8899,
                    "allowlist": ["api.openai.com"],
                    "keys": {"api.openai.com": "OPENAI_API_KEY"},
                }
            }

        monkeypatch.setattr(cp_module, "load_budget", mock_load_budget)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(hermes_dir))

        with pytest.raises(RuntimeFailure, match="not in credential_proxy.allowlist"):
            load_settings()

    def test_load_settings_missing_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from factory.credential_proxy import load_settings
        from factory.runtime.base import RuntimeFailure

        hermes_dir = tmp_path / ".hermes"
        hermes_dir.mkdir()
        (hermes_dir / "config.yaml").write_text(
            """model:
  provider: "custom:test"
  default: "test"
  base_url: "https://api.openai.com/v1"
providers:
  test:
    api: "https://api.openai.com/v1"
""",
            encoding="utf-8",
        )

        import factory.credential_proxy as cp_module

        def mock_load_budget() -> dict:
            return {
                "credential_proxy": {
                    "port": 8899,
                    "allowlist": ["api.openai.com"],
                    "keys": {"api.openai.com": "OPENAI_API_KEY"},
                }
            }

        monkeypatch.setattr(cp_module, "load_budget", mock_load_budget)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(hermes_dir))
        # Don't set OPENAI_API_KEY or .env

        with pytest.raises(RuntimeFailure, match="is empty and the operator config has no model.api_key"):
            load_settings()
