from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from calendar_sync import cli
from calendar_sync.spielerplus import SpielerPlusError
from calendar_sync.sync import SyncResult


def test_build_parser_requires_a_subcommand():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_build_parser_accepts_known_subcommands():
    parser = cli.build_parser()
    for command in ("sync", "list-events", "google-login", "list-jobs"):
        args = parser.parse_args([command])
        assert args.command == command
        assert callable(args.func)


def test_main_reports_missing_configuration_as_usage_error(monkeypatch, capsys):
    monkeypatch.delenv("SPIELERPLUS_EMAIL", raising=False)
    monkeypatch.delenv("SPIELERPLUS_PASSWORD", raising=False)
    with patch("calendar_sync.cli.load_dotenv"):
        exit_code = cli.main(["sync"])

    assert exit_code == 2
    assert "SPIELERPLUS_EMAIL" in capsys.readouterr().err


def test_main_loads_dotenv_relative_to_the_working_directory():
    # Regression test: python-dotenv's default load_dotenv() searches
    # upward from the installed package file's location, not the CWD.
    # That only found .env by accident here because of the editable dev
    # install; a systemd service (WorkingDirectory=<project dir>, non-
    # editable install) needs usecwd=True to find .env at all.
    with patch("calendar_sync.cli.load_dotenv") as fake_load_dotenv, patch.object(
        cli, "cmd_list_events", return_value=0
    ):
        cli.main(["list-events"])

    fake_load_dotenv.assert_called_once_with(usecwd=True)


def test_main_dispatches_to_the_matching_subcommand():
    with patch("calendar_sync.cli.load_dotenv"), patch.object(
        cli, "cmd_list_events", return_value=0
    ) as fake_cmd:
        exit_code = cli.main(["list-events"])

    assert exit_code == 0
    fake_cmd.assert_called_once()


TWO_JOBS_TOML = """
[[job]]
name = "u7"
spielerplus_user_id = "111"
google_calendar_id = "u7@group.calendar.google.com"

[[job]]
name = "u9"
spielerplus_user_id = "222"
google_calendar_id = "u9@group.calendar.google.com"
"""


def _write_jobs_file(tmp_path, text=TWO_JOBS_TOML):
    path = tmp_path / "jobs.toml"
    path.write_text(text)
    return path


def _multi_job_patches(fake_spielerplus, sync_result=None):
    sync_result = sync_result or SyncResult(created=["x"], updated=[], deleted=[], unchanged=[])
    return (
        patch("calendar_sync.cli.load_dotenv"),
        patch("calendar_sync.cli._build_spielerplus_client", return_value=fake_spielerplus),
        patch("calendar_sync.cli.build_service", return_value=MagicMock()),
        patch("calendar_sync.cli.GoogleCalendarClient"),
        patch("calendar_sync.cli.SyncService", **{"return_value.sync.return_value": sync_result}),
    )


def _env(monkeypatch):
    monkeypatch.setenv("SPIELERPLUS_EMAIL", "me@example.com")
    monkeypatch.setenv("SPIELERPLUS_PASSWORD", "hunter2")


def test_cmd_sync_runs_every_job_in_the_jobs_file(tmp_path, monkeypatch, capsys):
    _env(monkeypatch)
    jobs_file = _write_jobs_file(tmp_path)
    fake_spielerplus = MagicMock()

    patches = _multi_job_patches(fake_spielerplus)
    with patches[0], patches[1], patches[2], patches[3] as gc_cls, patches[4]:
        exit_code = cli.main(["--jobs-file", str(jobs_file), "sync"])

    assert exit_code == 0
    fake_spielerplus.switch_user.assert_any_call("111")
    fake_spielerplus.switch_user.assert_any_call("222")
    assert gc_cls.call_count == 2
    out = capsys.readouterr().out
    assert "[u7] created=1 updated=0 deleted=0 unchanged=0" in out
    assert "[u9] created=1 updated=0 deleted=0 unchanged=0" in out


def test_cmd_sync_passes_each_jobs_title_prefix_to_sync_service(tmp_path, monkeypatch, capsys):
    _env(monkeypatch)
    jobs_file = _write_jobs_file(
        tmp_path,
        text=(
            '[[job]]\nname = "u7"\nspielerplus_user_id = "111"\n'
            'google_calendar_id = "shared@group.calendar.google.com"\ntitle_prefix = "U7"\n'
        ),
    )
    fake_spielerplus = MagicMock()
    fake_event = SimpleNamespace(title="Training", subtitle="")

    patches = _multi_job_patches(fake_spielerplus)
    with patches[0], patches[1], patches[2], patches[3], patches[4] as sync_service_cls:
        cli.main(["--jobs-file", str(jobs_file), "sync"])

    _, kwargs = sync_service_cls.call_args
    assert kwargs["title_builder"](fake_event) == "[U7] Training"


def test_cmd_sync_with_job_flag_only_runs_that_job(tmp_path, monkeypatch, capsys):
    _env(monkeypatch)
    jobs_file = _write_jobs_file(tmp_path)
    fake_spielerplus = MagicMock()

    patches = _multi_job_patches(fake_spielerplus)
    with patches[0], patches[1], patches[2], patches[3] as gc_cls, patches[4]:
        exit_code = cli.main(["--jobs-file", str(jobs_file), "sync", "--job", "u9"])

    assert exit_code == 0
    fake_spielerplus.switch_user.assert_called_once_with("222")
    assert gc_cls.call_count == 1
    assert "[u9]" in capsys.readouterr().out


def test_cmd_sync_unknown_job_name_is_a_config_error(tmp_path, monkeypatch, capsys):
    _env(monkeypatch)
    jobs_file = _write_jobs_file(tmp_path)

    with patch("calendar_sync.cli.load_dotenv"):
        exit_code = cli.main(["--jobs-file", str(jobs_file), "sync", "--job", "does-not-exist"])

    assert exit_code == 2
    assert "does-not-exist" in capsys.readouterr().err


def test_cmd_sync_continues_after_one_job_fails(tmp_path, monkeypatch, capsys):
    _env(monkeypatch)
    jobs_file = _write_jobs_file(tmp_path)
    fake_spielerplus = MagicMock()

    def switch_user(user_id):
        if user_id == "111":
            raise SpielerPlusError("could not switch profile")

    fake_spielerplus.switch_user.side_effect = switch_user

    patches = _multi_job_patches(fake_spielerplus)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        exit_code = cli.main(["--jobs-file", str(jobs_file), "sync"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "[u7] FAILED" in captured.err
    assert "[u9] created=1" in captured.out


def test_cmd_sync_reuses_google_service_for_jobs_sharing_a_token_path(tmp_path, monkeypatch):
    _env(monkeypatch)
    jobs_file = _write_jobs_file(tmp_path)  # neither job overrides google_token_path
    fake_spielerplus = MagicMock()

    patches = _multi_job_patches(fake_spielerplus)
    with patches[0], patches[1], patches[2] as build_service_mock, patches[3], patches[4]:
        cli.main(["--jobs-file", str(jobs_file), "sync"])

    assert build_service_mock.call_count == 1


def test_cmd_list_events_requires_job_flag_when_multiple_jobs_defined(tmp_path, monkeypatch, capsys):
    _env(monkeypatch)
    jobs_file = _write_jobs_file(tmp_path)
    fake_spielerplus = MagicMock(get_events=MagicMock(return_value=[]))

    with (
        patch("calendar_sync.cli.load_dotenv"),
        patch("calendar_sync.cli._build_spielerplus_client", return_value=fake_spielerplus),
    ):
        exit_code = cli.main(["--jobs-file", str(jobs_file), "list-events"])

    assert exit_code == 2
    assert "--job" in capsys.readouterr().err


def test_cmd_list_events_uses_the_single_job_without_a_flag(tmp_path, monkeypatch, capsys):
    _env(monkeypatch)
    jobs_file = _write_jobs_file(
        tmp_path,
        text='[[job]]\nname = "u7"\nspielerplus_user_id = "111"\ngoogle_calendar_id = "u7@group.calendar.google.com"\n',
    )
    fake_spielerplus = MagicMock(get_events=MagicMock(return_value=[]))

    with (
        patch("calendar_sync.cli.load_dotenv"),
        patch("calendar_sync.cli._build_spielerplus_client", return_value=fake_spielerplus),
    ):
        exit_code = cli.main(["--jobs-file", str(jobs_file), "list-events"])

    assert exit_code == 0
    fake_spielerplus.switch_user.assert_called_once_with("111")


def test_cmd_google_login_caches_a_token_per_distinct_token_path(tmp_path, monkeypatch, capsys):
    _env(monkeypatch)
    jobs_file = _write_jobs_file(
        tmp_path,
        text=(
            TWO_JOBS_TOML
            + '\n[[job]]\nname = "herren-1"\ngoogle_calendar_id = "h1@group.calendar.google.com"\n'
            'google_token_path = "herren-1-token.json"\n'
        ),
    )

    with (
        patch("calendar_sync.cli.load_dotenv"),
        patch("calendar_sync.cli.build_service", return_value=MagicMock()) as build_service_mock,
    ):
        exit_code = cli.main(["--jobs-file", str(jobs_file), "google-login"])

    assert exit_code == 0
    # u7 and u9 share the default token path -> only 2 distinct paths total.
    assert build_service_mock.call_count == 2
    out = capsys.readouterr().out
    assert "herren-1-token.json" in out


def test_cmd_list_jobs_without_jobs_file_reports_single_job_mode(monkeypatch, capsys):
    _env(monkeypatch)
    with patch("calendar_sync.cli.load_dotenv"):
        exit_code = cli.main(["list-jobs"])

    assert exit_code == 0
    assert "single-job mode" in capsys.readouterr().out


def test_cmd_list_jobs_prints_resolved_jobs(tmp_path, monkeypatch, capsys):
    _env(monkeypatch)
    jobs_file = _write_jobs_file(tmp_path)

    with patch("calendar_sync.cli.load_dotenv"):
        exit_code = cli.main(["--jobs-file", str(jobs_file), "list-jobs"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "u7" in out and "u9" in out
    assert "u7@group.calendar.google.com" in out
