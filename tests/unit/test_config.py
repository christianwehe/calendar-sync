from pathlib import Path

import pytest

from calendar_sync.config import ConfigError, JobConfig, ResolvedJob, Settings, load_jobs

REPO_ROOT = Path(__file__).resolve().parents[2]


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


def test_from_env_reads_jobs_file_path():
    settings = Settings.from_env(
        {
            "SPIELERPLUS_EMAIL": "me@example.com",
            "SPIELERPLUS_PASSWORD": "hunter2",
            "CALENDAR_SYNC_JOBS_FILE": "jobs.toml",
        }
    )
    assert settings.jobs_file == Path("jobs.toml")


def test_from_env_jobs_file_defaults_to_none():
    settings = Settings.from_env(
        {"SPIELERPLUS_EMAIL": "me@example.com", "SPIELERPLUS_PASSWORD": "hunter2"}
    )
    assert settings.jobs_file is None


def _settings(**overrides) -> Settings:
    base = dict(
        spielerplus_email="me@example.com",
        spielerplus_password="hunter2",
        google_calendar_id="default@group.calendar.google.com",
        google_credentials_path=Path("creds.json"),
        google_token_path=Path("token.json"),
        spielerplus_user_id="1001",
        timezone="Europe/Berlin",
        sync_tag="spielerplus-sync",
    )
    base.update(overrides)
    return Settings(**base)


def test_job_config_resolve_uses_its_own_values_when_set():
    job = JobConfig(
        name="u7",
        spielerplus_user_id="42",
        google_calendar_id="u7@group.calendar.google.com",
        google_token_path=Path("u7-token.json"),
        sync_tag="tag-u7",
        timezone="UTC",
        title_prefix="U7",
    )

    assert job.resolve(_settings()) == ResolvedJob(
        name="u7",
        spielerplus_user_id="42",
        google_calendar_id="u7@group.calendar.google.com",
        google_token_path=Path("u7-token.json"),
        sync_tag="tag-u7",
        timezone="UTC",
        title_prefix="U7",
    )


def test_job_config_resolve_title_prefix_defaults_to_none():
    job = JobConfig(name="u9", google_calendar_id="u9@group.calendar.google.com")
    resolved = job.resolve(_settings())

    assert resolved.title_prefix is None


def test_job_config_resolve_falls_back_to_settings_when_unset():
    job = JobConfig(name="u9", google_calendar_id="u9@group.calendar.google.com")
    resolved = job.resolve(_settings())

    assert resolved.spielerplus_user_id == "1001"
    assert resolved.google_token_path == Path("token.json")
    assert resolved.sync_tag == "spielerplus-sync"
    assert resolved.timezone == "Europe/Berlin"


def test_job_config_resolve_raises_when_no_calendar_id_anywhere():
    job = JobConfig(name="u9")
    with pytest.raises(ConfigError):
        job.resolve(_settings(google_calendar_id=""))


def test_load_jobs_parses_multiple_entries(tmp_path):
    jobs_file = tmp_path / "jobs.toml"
    jobs_file.write_text(
        """
[[job]]
name = "u7"
spielerplus_user_id = "111"
google_calendar_id = "u7@group.calendar.google.com"

[[job]]
name = "u9"
spielerplus_user_id = "222"
google_calendar_id = "u9@group.calendar.google.com"
sync_tag = "custom-tag"
google_token_path = "u9-token.json"
title_prefix = "U9"
"""
    )

    jobs = load_jobs(jobs_file)

    assert [j.name for j in jobs] == ["u7", "u9"]
    assert jobs[0].spielerplus_user_id == "111"
    assert jobs[0].title_prefix is None
    assert jobs[1].sync_tag == "custom-tag"
    assert jobs[1].google_token_path == Path("u9-token.json")
    assert jobs[1].title_prefix == "U9"


def test_load_jobs_requires_name_field(tmp_path):
    jobs_file = tmp_path / "jobs.toml"
    jobs_file.write_text('[[job]]\ngoogle_calendar_id = "x"\n')

    with pytest.raises(ConfigError):
        load_jobs(jobs_file)


def test_load_jobs_rejects_duplicate_names(tmp_path):
    jobs_file = tmp_path / "jobs.toml"
    jobs_file.write_text('[[job]]\nname = "u7"\n\n[[job]]\nname = "u7"\n')

    with pytest.raises(ConfigError):
        load_jobs(jobs_file)


def test_load_jobs_rejects_file_with_no_job_entries(tmp_path):
    jobs_file = tmp_path / "jobs.toml"
    jobs_file.write_text("")

    with pytest.raises(ConfigError):
        load_jobs(jobs_file)


def test_load_jobs_raises_on_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_jobs(tmp_path / "does-not-exist.toml")


def test_load_jobs_raises_on_invalid_toml(tmp_path):
    jobs_file = tmp_path / "jobs.toml"
    jobs_file.write_text("this is not [valid toml")

    with pytest.raises(ConfigError):
        load_jobs(jobs_file)


def test_shipped_example_jobs_file_is_valid():
    jobs = load_jobs(REPO_ROOT / "jobs.example.toml")
    assert {j.name for j in jobs} == {"u7", "u9", "herren-1", "thomas"}
    thomas = next(j for j in jobs if j.name == "thomas")
    assert thomas.title_prefix == "Thomas"
