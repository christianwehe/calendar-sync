from datetime import datetime
from urllib.parse import parse_qs

import pytest
import responses

from calendar_sync.spielerplus.client import (
    EVENTS_URL,
    LOGIN_URL,
    PARTICIPATION_URL,
    SWITCH_USER_URL,
    SpielerPlusClient,
)
from calendar_sync.spielerplus.exceptions import AuthenticationError, SpielerPlusError
from calendar_sync.spielerplus.models import Attendance, SpielerPlusEvent, Team


def test_client_overrides_default_requests_user_agent():
    # spielerplus.de sits behind Cloudflare, which returns a bare 403 for
    # the default "python-requests/x.y" User-Agent before any of our
    # request handling runs. A `requests.Session()` pre-populates that
    # header itself, so the fix must *overwrite* it, not merely fill in
    # a missing value (see SpielerPlusClient.__init__).
    client = SpielerPlusClient()
    user_agent = client._session.headers["User-Agent"]
    assert not user_agent.startswith("python-requests")
    assert "Mozilla" in user_agent


def test_client_does_not_clobber_a_caller_provided_session_object():
    import requests

    session = requests.Session()
    client = SpielerPlusClient(session=session)
    assert client._session is session
    assert not session.headers["User-Agent"].startswith("python-requests")


@responses.activate
def test_login_skips_form_when_already_authenticated(load_fixture):
    responses.add(responses.GET, EVENTS_URL, body=load_fixture("events_page.html"))

    client = SpielerPlusClient()
    client.login("me@example.com", "hunter2")

    assert client._logged_in is True
    assert len(responses.calls) == 1


@responses.activate
def test_login_submits_credentials_and_parses_teams(load_fixture):
    responses.add(responses.GET, EVENTS_URL, body=load_fixture("login_page.html"))
    responses.add(responses.POST, LOGIN_URL, body=load_fixture("team_selection_page.html"))

    client = SpielerPlusClient()
    client.login("me@example.com", "hunter2")

    assert client._logged_in is True
    assert client.teams == [Team(id="1001", name="U11 Junioren"), Team(id="1002", name="Herren I")]

    login_request = responses.calls[1].request
    form = parse_qs(login_request.body)
    assert form["_csrf"] == ["test-csrf-token-abc123"]
    assert form["LoginForm[email]"] == ["me@example.com"]
    assert form["LoginForm[password]"] == ["hunter2"]


@responses.activate
def test_login_raises_when_credentials_are_rejected(load_fixture):
    responses.add(responses.GET, EVENTS_URL, body=load_fixture("login_page.html"))
    responses.add(responses.POST, LOGIN_URL, body=load_fixture("login_page.html"))

    client = SpielerPlusClient()
    with pytest.raises(AuthenticationError):
        client.login("me@example.com", "wrong-password")


def test_get_events_requires_login_first():
    client = SpielerPlusClient()
    with pytest.raises(AuthenticationError):
        client.get_events()


def test_switch_user_requires_login_first():
    client = SpielerPlusClient()
    with pytest.raises(AuthenticationError):
        client.switch_user("1001")


def test_set_attendance_requires_login_first():
    client = SpielerPlusClient()
    event = SpielerPlusEvent(
        id="111",
        event_type="training",
        title="Training",
        subtitle="",
        start=datetime(2026, 7, 18, 19, 0),
        end=datetime(2026, 7, 18, 21, 0),
        end_is_estimated=True,
        attendance=Attendance.UNKNOWN,
    )
    with pytest.raises(AuthenticationError):
        client.set_attendance(event, Attendance.ACCEPTED, user_id="42")


@responses.activate
def test_get_events_returns_parsed_events(load_fixture):
    responses.add(responses.GET, EVENTS_URL, body=load_fixture("events_page.html"))
    responses.add(responses.GET, EVENTS_URL, body=load_fixture("events_page.html"))

    client = SpielerPlusClient()
    client.login("me@example.com", "hunter2")
    events = client.get_events()

    assert [e.id for e in events] == ["111", "222", "333"]


@responses.activate
def test_switch_user_requests_expected_url(load_fixture):
    responses.add(responses.GET, EVENTS_URL, body=load_fixture("events_page.html"))
    responses.add(responses.GET, SWITCH_USER_URL, status=200)

    client = SpielerPlusClient()
    client.login("me@example.com", "hunter2")
    client.switch_user("1001")

    switch_request = responses.calls[-1].request
    assert switch_request.url.startswith(SWITCH_USER_URL)
    assert "id=1001" in switch_request.url


@responses.activate
def test_set_attendance_posts_expected_form_fields(load_fixture):
    responses.add(responses.GET, EVENTS_URL, body=load_fixture("events_page.html"))
    responses.add(responses.POST, PARTICIPATION_URL, status=200)

    client = SpielerPlusClient()
    client.login("me@example.com", "hunter2")

    event = SpielerPlusEvent(
        id="111",
        event_type="training",
        title="Training",
        subtitle="",
        start=datetime(2026, 7, 18, 19, 0),
        end=datetime(2026, 7, 18, 21, 0),
        end_is_estimated=True,
        attendance=Attendance.UNKNOWN,
    )
    client.set_attendance(event, Attendance.ACCEPTED, user_id="42", reason="see you there")

    form = parse_qs(responses.calls[-1].request.body)
    assert form["Participation[participation]"] == ["1"]
    assert form["Participation[reason]"] == ["see you there"]
    assert form["Participation[type]"] == ["training"]
    assert form["Participation[typeid]"] == ["111"]
    assert form["Participation[user_id]"] == ["42"]


@responses.activate
def test_set_attendance_raises_on_unexpected_status(load_fixture):
    responses.add(responses.GET, EVENTS_URL, body=load_fixture("events_page.html"))
    responses.add(responses.POST, PARTICIPATION_URL, status=202)

    client = SpielerPlusClient()
    client.login("me@example.com", "hunter2")

    event = SpielerPlusEvent(
        id="111",
        event_type="training",
        title="Training",
        subtitle="",
        start=datetime(2026, 7, 18, 19, 0),
        end=datetime(2026, 7, 18, 21, 0),
        end_is_estimated=True,
        attendance=Attendance.UNKNOWN,
    )
    with pytest.raises(SpielerPlusError):
        client.set_attendance(event, Attendance.ACCEPTED, user_id="42")
