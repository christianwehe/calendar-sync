from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    """Shared configuration, read from the environment / ``.env``.

    In single-job setups this is all the configuration there is. In
    multi-job setups (see :class:`JobConfig`) it also supplies the
    defaults any job doesn't override itself — e.g. most jobs share one
    SpielerPlus login and one Google OAuth client, differing only in
    which profile/calendar they target.
    """

    spielerplus_email: str
    spielerplus_password: str
    google_calendar_id: str
    google_credentials_path: Path
    google_token_path: Path
    spielerplus_user_id: str | None = None
    timezone: str = "Europe/Berlin"
    sync_tag: str = "spielerplus-sync"
    jobs_file: Path | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        env = env if env is not None else os.environ
        try:
            spielerplus_email = env["SPIELERPLUS_EMAIL"]
            spielerplus_password = env["SPIELERPLUS_PASSWORD"]
        except KeyError as exc:
            raise ConfigError(f"missing required environment variable: {exc.args[0]}") from exc

        jobs_file = env.get("CALENDAR_SYNC_JOBS_FILE")

        return cls(
            spielerplus_email=spielerplus_email,
            spielerplus_password=spielerplus_password,
            spielerplus_user_id=env.get("SPIELERPLUS_USER_ID") or None,
            google_calendar_id=env.get("GOOGLE_CALENDAR_ID", "primary"),
            google_credentials_path=Path(env.get("GOOGLE_CREDENTIALS_PATH", "google_credentials.json")),
            google_token_path=Path(env.get("GOOGLE_TOKEN_PATH", "google_token.json")),
            timezone=env.get("CALENDAR_SYNC_TIMEZONE", "Europe/Berlin"),
            sync_tag=env.get("CALENDAR_SYNC_TAG", "spielerplus-sync"),
            jobs_file=Path(jobs_file) if jobs_file else None,
        )


@dataclass(frozen=True)
class JobConfig:
    """One sync target within a multi-job setup: a SpielerPlus profile
    paired with a Google Calendar.

    Every field but ``name`` is optional and falls back to the matching
    :class:`Settings` value when left unset — see :meth:`resolve`.
    """

    name: str
    spielerplus_user_id: str | None = None
    google_calendar_id: str | None = None
    google_token_path: Path | None = None
    sync_tag: str | None = None
    timezone: str | None = None

    def resolve(self, settings: Settings) -> "ResolvedJob":
        calendar_id = self.google_calendar_id or settings.google_calendar_id
        if not calendar_id:
            raise ConfigError(f"job {self.name!r} has no google_calendar_id, and none is set as a default")

        return ResolvedJob(
            name=self.name,
            spielerplus_user_id=self.spielerplus_user_id or settings.spielerplus_user_id,
            google_calendar_id=calendar_id,
            google_token_path=self.google_token_path or settings.google_token_path,
            sync_tag=self.sync_tag or settings.sync_tag,
            timezone=self.timezone or settings.timezone,
        )


@dataclass(frozen=True)
class ResolvedJob:
    """A :class:`JobConfig` with all defaults from :class:`Settings` filled in."""

    name: str
    spielerplus_user_id: str | None
    google_calendar_id: str
    google_token_path: Path
    sync_tag: str
    timezone: str


def load_jobs(path: str | Path) -> list[JobConfig]:
    """Parse a TOML file of ``[[job]]`` tables into :class:`JobConfig` objects.

    See ``jobs.example.toml`` for the expected format.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"could not read jobs file {path}: {exc}") from exc

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in jobs file {path}: {exc}") from exc

    raw_jobs = data.get("job", [])
    if not raw_jobs:
        raise ConfigError(f"jobs file {path} defines no [[job]] entries")

    jobs: list[JobConfig] = []
    seen_names: set[str] = set()
    for entry in raw_jobs:
        try:
            name = entry["name"]
        except KeyError as exc:
            raise ConfigError(f"job entry is missing the required 'name' field: {entry!r}") from exc
        if name in seen_names:
            raise ConfigError(f"duplicate job name {name!r} in {path}")
        seen_names.add(name)

        token_path = entry.get("google_token_path")
        jobs.append(
            JobConfig(
                name=name,
                spielerplus_user_id=entry.get("spielerplus_user_id"),
                google_calendar_id=entry.get("google_calendar_id"),
                google_token_path=Path(token_path) if token_path else None,
                sync_tag=entry.get("sync_tag"),
                timezone=entry.get("timezone"),
            )
        )
    return jobs
