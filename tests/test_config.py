"""Configuration must fail closed before a network listener is started."""

import pytest
from pydantic import ValidationError

from recipe_mcp.config import Settings

TOKEN = "portfolio-test-token-" + "a" * 32


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "recipes.example.com", "192.168.1.5"])
def test_remote_bind_requires_authentication(host):
    with pytest.raises(ValidationError, match="Binding beyond loopback"):
        Settings(_env_file=None, host=host, auth_token=None)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_supports_local_development_without_token(host):
    settings = Settings(_env_file=None, host=host, auth_token=None)
    assert settings.host == host


@pytest.mark.parametrize("token", ["", "short", " " * 32, "a" * 32 + "\n", "é" * 32, "a" * 257])
def test_malformed_token_is_rejected(token):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, auth_token=token)


def test_remote_bind_with_token_keeps_secret_out_of_repr():
    settings = Settings(_env_file=None, host="0.0.0.0", auth_token=TOKEN)
    assert settings.auth_token.get_secret_value() == TOKEN
    assert TOKEN not in repr(settings)
    assert TOKEN not in settings.model_dump_json()


@pytest.mark.parametrize(
    "overrides",
    [
        {"allowed_hosts": []},
        {"allowed_hosts": ["*"]},
        {"allowed_origins": []},
        {"allowed_origins": ["https://*"]},
        {"port": 0},
        {"port": 65536},
        {"request_timeout": 0},
        {"retries": 5},
        {"mode": "unknown"},
    ],
)
def test_unsafe_or_invalid_settings_are_rejected(overrides):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)


def test_application_environment_is_explicitly_loaded(monkeypatch, tmp_path):
    monkeypatch.setenv("RECIPE_MODE", "demo")
    monkeypatch.setenv("RECIPE_DATABASE_PATH", str(tmp_path / "environment.sqlite3"))
    monkeypatch.setenv("RECIPE_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("RECIPE_ALLOWED_HOSTS", '["recipes.example.com"]')
    settings = Settings(_env_file=None)
    assert settings.mode == "demo"
    assert settings.database_path == tmp_path / "environment.sqlite3"
    assert settings.allowed_hosts == ["recipes.example.com"]
    assert settings.auth_token.get_secret_value() == TOKEN
