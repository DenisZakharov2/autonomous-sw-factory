"""Tests for credential proxy (PR-C2)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Windows has no Unix sockets in socketserver; the proxy class refuses to start there.
# These tests run on POSIX only.
POSIX_ONLY = pytest.mark.skipif(
    not hasattr(os, "fork"),
    reason="credential proxy requires Unix domain sockets (Ubuntu)",
)


@POSIX_ONLY
class TestCredentialProxySettings:
    """Tests for ProxySettings dataclass and load_settings()."""

    def test_proxy_settings_properties(self, monkeypatch: pytest.MonkeyPatch) -> None:
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
class TestCredentialProxyIntegration:
    """Integration-style tests with mocked operator config."""

    def test_load_settings_with_mocked_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_settings() resolves budget + operator config without network."""
        from factory.credential_proxy import load_settings

        # Create a fake operator config
        hermes_dir = tmp_path / ".hermes"
        hermes_dir.mkdir()
        config_yaml = hermes_dir / "config.yaml"
        config_yaml.write_text(
            """model:
  provider: "custom:test-provider"
  default: "test-model"
  base_url: "https://api.openai.com/v1"
  api_mode: "chat_completions"
providers:
  test-provider:
    api: "https://api.openai.com/v1"
    key_env: "OPENAI_API_KEY"
""",
            encoding="utf-8",
        )
        (hermes_dir / ".env").write_text(
            'OPENAI_API_KEY="sk-fake-key-for-testing"\n',
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

        settings = load_settings()

        assert settings.upstream_host == "api.openai.com"
        assert settings.model == "test-model"
        assert settings.api_mode == "chat_completions"
        assert settings.transport == "openai_chat"
        assert settings.real_key == "sk-fake-key-for-testing"
        assert settings.token.startswith("factory-proxy-key-")
        assert settings.port == 8899

    def test_credential_proxy_start_stop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """CredentialProxy.start() creates dir, config, socket; stop() cleans up."""
        from factory.credential_proxy import CredentialProxy, ProxySettings

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
            real_key="sk-test",
            token="factory-proxy-key-token123",
        )

        proxy = CredentialProxy(settings)
        proxy.start()

        try:
            assert proxy.directory is not None
            dir_path = proxy.directory
            assert dir_path.exists()
            assert proxy.config_path is not None
            assert proxy.config_path.exists()
            assert proxy.config_path.read_text().find("factory-proxy-key-token123") != -1

            socket_path = dir_path / "proxy.sock"
            assert socket_path.exists()
            assert oct(socket_path.stat().st_mode)[-3:] == "600"

            config_path = dir_path / "config.yaml"
            assert oct(config_path.stat().st_mode)[-3:] == "600"

            # Environment for sandbox
            env = proxy.environment()
            assert env["HTTP_PROXY"] == "http://127.0.0.1:8899"
            assert env["http_proxy"] == "http://127.0.0.1:8899"
            assert env["NO_PROXY"] == ""
            assert env["no_proxy"] == ""

            # Wrapper command
            wrapper = proxy.wrapper(["hermes", "chat"])
            # Bridge mount path is constant
            assert wrapper[:2] == ["python3", "/tmp/factory-proxy/bridge.py"]
            assert "--unix" in wrapper
            assert "--port" in wrapper
            assert wrapper[-3:] == ["--", "hermes", "chat"]
        finally:
            proxy.stop()

        # Directory must be gone after stop()
        assert not dir_path.exists()


@POSIX_ONLY
class TestBridgeScript:
    """Tests for the TCP→Unix bridge script."""

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


@POSIX_ONLY
class TestCredentialProxyNegative:
    """Negative tests: invalid config, missing keys, etc."""

    def test_load_settings_missing_budget_section(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from factory.credential_proxy import load_settings
        from factory.runtime.base import RuntimeFailure

        # Patch load_budget in the credential_proxy module directly
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