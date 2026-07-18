from datetime import date, datetime

import pytest

from calendar_sync.spielerplus import Attendance, ParseError
from calendar_sync.spielerplus import parser

REFERENCE_DATE = datetime(2026, 7, 18, 12, 0)


def test_requires_login_true_for_login_page(load_fixture):
    assert parser.requires_login(load_fixture("login_page.html")) is True


def test_requires_login_false_for_events_page(load_fixture):
    assert parser.requires_login(load_fixture("events_page.html")) is False


def test_is_team_selection_page(load_fixture):
    assert parser.is_team_selection_page(load_fixture("team_selection_page.html")) is True
    assert parser.is_team_selection_page(load_fixture("events_page.html")) is False


def test_is_events_page(load_fixture):
    assert parser.is_events_page(load_fixture("events_page.html")) is True
    assert parser.is_events_page(load_fixture("login_page.html")) is False


def test_extract_csrf_token(load_fixture):
    assert parser.extract_csrf_token(load_fixture("login_page.html")) == "test-csrf-token-abc123"


def test_extract_csrf_token_missing_raises():
    with pytest.raises(ParseError):
        parser.extract_csrf_token("<html><head><title>Einloggen</title></head><body></body></html>")


def test_parse_teams(load_fixture):
    teams = parser.parse_teams(load_fixture("team_selection_page.html"))
    assert teams == [
        parser.Team(id="1001", name="U11 Junioren"),
        parser.Team(id="1002", name="Herren I"),
    ]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("-:-", None),
        ("", None),
        ("19:00", "19:00"),
        (" 9:05 ", "09:05"),
        ("7:00 PM", "19:00"),
        ("12:00 PM", "12:00"),
        ("12:00 AM", "00:00"),
        ("12:30 AM", "00:30"),
        ("11:59 PM", "23:59"),
        ("not-a-time", None),
    ],
)
def test_parse_time_string(raw, expected):
    assert parser.parse_time_string(raw) == expected


def test_resolve_event_date_same_year():
    # 20 July, seen while "today" is 18 July 2026 -> stays in 2026.
    assert parser.resolve_event_date(20, 7, date(2026, 7, 18)) == date(2026, 7, 20)


def test_resolve_event_date_rolls_over_to_next_year():
    # 5 January is far in the past relative to 18 July 2026 -> must be
    # the *next* occurrence, i.e. January 2027.
    assert parser.resolve_event_date(5, 1, date(2026, 7, 18)) == date(2027, 1, 5)


def test_resolve_event_date_within_grace_window_stays_current_year():
    # A handful of days in the past (e.g. timezone skew around midnight)
    # should not roll over.
    assert parser.resolve_event_date(10, 7, date(2026, 7, 18)) == date(2026, 7, 10)


def test_parse_events_returns_all_events(load_fixture):
    events = parser.parse_events(load_fixture("events_page.html"), reference_date=REFERENCE_DATE)
    assert [e.id for e in events] == ["111", "222", "333"]


def test_parse_training_event_uses_second_time_value_and_estimates_end(load_fixture):
    events = parser.parse_events(load_fixture("events_page.html"), reference_date=REFERENCE_DATE)
    training = events[0]

    assert training.event_type == "training"
    assert training.title == "Training"
    assert training.subtitle == ""
    assert training.start == datetime(2026, 7, 18, 19, 0)
    assert training.end == datetime(2026, 7, 18, 21, 0)
    assert training.end_is_estimated is True
    assert training.attendance == Attendance.ACCEPTED


def test_parse_match_event_uses_explicit_start_and_end(load_fixture):
    events = parser.parse_events(load_fixture("events_page.html"), reference_date=REFERENCE_DATE)
    match = events[1]

    assert match.event_type == "match"
    assert match.title == "Punktspiel"
    assert match.subtitle == "Heimspiel"
    assert match.start == datetime(2026, 7, 20, 17, 0)
    assert match.end == datetime(2026, 7, 20, 19, 30)
    assert match.end_is_estimated is False
    assert match.attendance == Attendance.UNSURE


def test_parse_event_with_am_pm_times_and_year_rollover_and_unknown_attendance(load_fixture):
    events = parser.parse_events(load_fixture("events_page.html"), reference_date=REFERENCE_DATE)
    party = events[2]

    assert party.event_type == "other"
    assert party.start == datetime(2027, 1, 5, 19, 0)
    assert party.end == datetime(2027, 1, 5, 21, 0)
    assert party.end_is_estimated is False
    assert party.attendance == Attendance.UNKNOWN


def test_parse_events_defaults_reference_date_to_now(load_fixture, monkeypatch):
    fixed_now = datetime(2026, 7, 18, 8, 0)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(parser, "datetime", _FixedDateTime)

    events = parser.parse_events(load_fixture("events_page.html"))
    assert events[0].start == datetime(2026, 7, 18, 19, 0)


def test_event_uid_is_stable_identifier(load_fixture):
    events = parser.parse_events(load_fixture("events_page.html"), reference_date=REFERENCE_DATE)
    assert events[0].uid == "training-111"
    assert events[1].uid == "match-222"
