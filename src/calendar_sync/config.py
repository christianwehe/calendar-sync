from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    spielerplus_email: str
    spielerplus_password: str
    google_calendar_id: str
    google_credentials_path: Path
    google_token_path: Path
    spielerplus_user_id: str | None = None
    timezone: str = "Europe/Berlin"
    sync_tag: str = "spielerplus-sync"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        env = env if env is not None else os.environ
        try:
            spielerplus_email = env["SPIELERPLUS_EMAIL"]
            spielerplus_password = env["SPIELERPLUS_PASSWORD"]
        except KeyError as exc:
            raise ConfigError(f"missing required environment variable: {exc.args[0]}") from exc

        return cls(
            spielerplus_email=spielerplus_email,
            spielerplus_password=spielerplus_password,
            spielerplus_user_id=env.get("SPIELERPLUS_USER_ID") or None,
            google_calendar_id=env.get("GOOGLE_CALENDAR_ID", "primary"),
            google_credentials_path=Path(env.get("GOOGLE_CREDENTIALS_PATH", "google_credentials.json")),
            google_token_path=Path(env.get("GOOGLE_TOKEN_PATH", "google_token.json")),
            timezone=env.get("CALENDAR_SYNC_TIMEZONE", "Europe/Berlin"),
            sync_tag=env.get("CALENDAR_SYNC_TAG", "spielerplus-sync"),
        )
