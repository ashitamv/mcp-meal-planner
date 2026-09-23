"""Explicit application configuration, independent of MCP SDK internals."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RECIPE_", env_file=".env", extra="ignore")

    mode: Literal["live", "demo"] = "live"
    database_path: Path = Path("data/recipes.sqlite3")
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    api_key: SecretStr = SecretStr("1")
    auth_token: SecretStr | None = None
    allowed_hosts: list[str] = ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"]
    allowed_origins: list[str] = ["http://localhost:*", "http://127.0.0.1:*"]
    cache_ttl: float = Field(default=300, ge=0, le=86400)
    cache_size: int = Field(default=128, ge=1, le=4096)
    request_timeout: float = Field(default=10, ge=0.1, le=60)
    retries: int = Field(default=2, ge=0, le=4)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("auth_token")
    @classmethod
    def token_length(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            token = value.get_secret_value()
            if not 32 <= len(token) <= 256 or any(not 33 <= ord(char) <= 126 for char in token):
                raise ValueError(
                    "RECIPE_AUTH_TOKEN must contain 32 to 256 visible ASCII characters"
                )
        return value

    @field_validator("allowed_hosts", "allowed_origins")
    @classmethod
    def no_wildcard(cls, value: list[str]) -> list[str]:
        if not value or any(item in ("*", "http://*", "https://*") for item in value):
            raise ValueError("Configure explicit hostnames and origins")
        return value

    @model_validator(mode="after")
    def require_remote_auth(self) -> "Settings":
        if self.host not in ("localhost", "127.0.0.1", "::1") and self.auth_token is None:
            raise ValueError("Binding beyond loopback requires RECIPE_AUTH_TOKEN")
        return self
