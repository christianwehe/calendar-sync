from unittest.mock import patch

import pytest

from calendar_sync import cli


def test_build_parser_requires_a_subcommand():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_build_parser_accepts_known_subcommands():
    parser = cli.build_parser()
    for command in ("sync", "list-events", "google-login"):
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


def test_main_dispatches_to_the_matching_subcommand():
    with patch("calendar_sync.cli.load_dotenv"), patch.object(
        cli, "cmd_list_events", return_value=0
    ) as fake_cmd:
        exit_code = cli.main(["list-events"])

    assert exit_code == 0
    fake_cmd.assert_called_once()
