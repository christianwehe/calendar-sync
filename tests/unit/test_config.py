from pathlib import Path

import pytest

from calendar_sync.config import ConfigError, Settings


def test_from_env_applies_defaults_for_optional_values():
    settings = Settings.from_env(
        {"SPIELERPLUS_EMAIL": "me@example.com", "SPIELERPLUS_PASSWORD": "hunter2"}
    )

    assert settings.spielerplus_email == "me@example.com"
    assert settings.spielerplus_user_id is None
    assert settings.google_calendar_id == "primary"
    assert settings.google_credentials_path == Path("google_credentials.json")
    assert settings.timezone == "Europe/Berlin"
    assert settings.sync_tag == "spielerplus-sync"


def test_from_env_reads_overrides():
    settings = Settings.from_env(
        {
            "SPIELERPLUS_EMAIL": "me@example.com",
            "SPIELERPLUS_PASSWORD": "hunter2",
            "SPIELERPLUS_USER_ID": "1001",
            "GOOGLE_CALENDAR_ID": "team@group.calendar.google.com",
            "GOOGLE_CREDENTIALS_PATH": "/etc/creds.json",
            "GOOGLE_TOKEN_PATH": "/etc/token.json",
            "CALENDAR_SYNC_TIMEZONE": "UTC",
            "CALENDAR_SYNC_TAG": "custom-tag",
        }
    )

    assert settings.spielerplus_user_id == "1001"
    assert settings.google_calendar_id == "team@group.calendar.google.com"
    assert settings.google_credentials_path == Path("/etc/creds.json")
    assert settings.google_token_path == Path("/etc/token.json")
    assert settings.timezone == "UTC"
    assert settings.sync_tag == "custom-tag"


def test_from_env_raises_config_error_when_credentials_missing():
    with pytest.raises(ConfigError):
        Settings.from_env({})
