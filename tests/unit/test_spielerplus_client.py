from datetime import date, datetime
from urllib.parse import parse_qs

import pytest
import responses

from calendar_sync.spielerplus.client import (
    EVENT_PARTICIPATION_URL,
    EVENTS_URL,
    LOGIN_URL,
    MEMBERS_URL,
    MORE_EVENTS_URL,
    PARTICIPATION_URL,
    PROFILE_URL,
    SWITCH_USER_URL,
    SpielerPlusClient,
)
from calendar_sync.spielerplus.exceptions import (
    AuthenticationError,
    ParseError,
    SpielerPlusError,
)
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


def _event_html(event_id: str, day_month: str = "01.08.") -> str:
    return f"""
    <div class="last-week">Kalenderwoche 31</div>
    <div class="list event">
      <div class="panel" id="event-training-{event_id}">
        <div class="panel-heading-text"><span class="panel-title">Training</span></div>
        <div class="panel-heading-info"><span class="panel-subtitle">{day_month}</span></div>
        <table class="event-time-table">
          <tr><td class="event-time-value">-:-</td></tr>
          <tr><td class="event-time-value">19:00</td></tr>
        </table>
      </div>
    </div>
    """


def _more_events_body(*event_ids: str) -> dict:
    return {"html": "".join(_event_html(i) for i in event_ids), "count": len(event_ids)}


@responses.activate
def test_get_events_returns_parsed_events(load_fixture):
    responses.add(responses.GET, EVENTS_URL, body=load_fixture("events_page.html"))
    responses.add(responses.GET, EVENTS_URL, body=load_fixture("events_page.html"))
    responses.add(responses.POST, MORE_EVENTS_URL, json=_more_events_body())

    client = SpielerPlusClient()
    client.login("me@example.com", "hunter2")
    events = client.get_events()

    assert [e.id for e in events] == ["111", "222", "333"]


@responses.activate
def test_get_events_loads_further_batches_until_an_empty_one(load_fixture):
    # The events page only renders the first few events; the rest come
    # from the "Mehr Termine laden" AJAX endpoint, offset by the number
    # of events already loaded.
    responses.add(responses.GET, EVENTS_URL, body=load_fixture("events_page.html"))
    responses.add(responses.GET, EVENTS_URL, body=load_fixture("events_page.html"))
    responses.add(responses.POST, MORE_EVENTS_URL, json=_more_events_body("444", "555"))
    responses.add(responses.POST, MORE_EVENTS_URL, json=_more_events_body("666"))
    responses.add(responses.POST, MORE_EVENTS_URL, json=_more_events_body())

    client = SpielerPlusClient()
    client.login("me@example.com", "hunter2")
    events = client.get_events()

    assert [e.id for e in events] == ["111", "222", "333", "444", "555", "666"]
    offsets = [
        parse_qs(call.request.body)["offset"]
        for call in responses.calls
        if call.request.url == MORE_EVENTS_URL
    ]
    assert offsets == [["3"], ["5"], ["6"]]


@responses.activate
def test_get_events_drops_duplicates_across_batches(load_fixture):
    responses.add(responses.GET, EVENTS_URL, body=load_fixture("events_page.html"))
    responses.add(responses.GET, EVENTS_URL, body=load_fixture("events_page.html"))
    # "111" is also a training in the fixture page, so it has the same uid
    responses.add(responses.POST, MORE_EVENTS_URL, json=_more_events_body("111", "444"))
    responses.add(responses.POST, MORE_EVENTS_URL, json=_more_events_body())

    client = SpielerPlusClient()
    client.login("me@example.com", "hunter2")
    events = client.get_events()

    assert [e.id for e in events] == ["111", "222", "333", "444"]


@responses.activate
def test_get_events_raises_on_malformed_batch_response(load_fixture):
    responses.add(responses.GET, EVENTS_URL, body=load_fixture("events_page.html"))
    responses.add(responses.GET, EVENTS_URL, body=load_fixture("events_page.html"))
    responses.add(responses.POST, MORE_EVENTS_URL, body=load_fixture("login_page.html"))

    client = SpielerPlusClient()
    client.login("me@example.com", "hunter2")
    with pytest.raises(ParseError):
        client.get_events()


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


def _logged_in_client(load_fixture) -> SpielerPlusClient:
    responses.add(responses.GET, EVENTS_URL, body=load_fixture("events_page.html"))
    client = SpielerPlusClient()
    client.login("me@example.com", "hunter2")
    return client


@responses.activate
def test_get_members_without_birthdays_fetches_only_the_member_list(load_fixture):
    client = _logged_in_client(load_fixture)
    responses.add(responses.GET, MEMBERS_URL, body=load_fixture("members_page.html"))

    members = client.get_members()

    assert [m.id for m in members] == ["1001", "1002", "1003", "1004"]
    assert all(m.birthday is None for m in members)
    assert not any(call.request.url.startswith(PROFILE_URL) for call in responses.calls)


@responses.activate
def test_get_members_with_birthdays_fetches_each_profile(load_fixture):
    client = _logged_in_client(load_fixture)
    responses.add(responses.GET, MEMBERS_URL, body=load_fixture("members_page.html"))
    responses.add(responses.GET, PROFILE_URL, body=load_fixture("profile_page.html"))

    members = client.get_members(with_birthdays=True)

    assert [m.birthday for m in members] == [date(1992, 4, 9)] * 4
    profile_calls = [c for c in responses.calls if c.request.url.startswith(PROFILE_URL)]
    assert [parse_qs(c.request.url.split("?", 1)[1])["id"] for c in profile_calls] == [
        ["1001"], ["1002"], ["1003"], ["1004"]
    ]


@responses.activate
def test_get_participation_posts_the_event_and_parses_the_modal(load_fixture):
    client = _logged_in_client(load_fixture)
    responses.add(
        responses.POST,
        EVENT_PARTICIPATION_URL,
        json={"html": load_fixture("participation_modal.html")},
    )
    event = SpielerPlusEvent(
        id="2061203",
        event_type="tournament",
        title="Turnier",
        subtitle="",
        start=datetime(2026, 9, 27, 12, 0),
        end=datetime(2026, 9, 27, 14, 0),
        end_is_estimated=True,
        attendance=Attendance.UNKNOWN,
    )

    participation = client.get_participation(event)

    assert participation["1002"] == Attendance.ACCEPTED
    body = parse_qs(responses.calls[-1].request.body)
    assert body == {"eventid": ["2061203"], "eventtype": ["tournament"]}


@responses.activate
def test_get_participation_rejects_a_non_json_response(load_fixture):
    client = _logged_in_client(load_fixture)
    responses.add(responses.POST, EVENT_PARTICIPATION_URL, body="<html>oops</html>")
    event = SpielerPlusEvent(
        id="1", event_type="tournament", title="", subtitle="",
        start=datetime(2026, 9, 27), end=datetime(2026, 9, 27),
        end_is_estimated=True, attendance=Attendance.UNKNOWN,
    )

    with pytest.raises(ParseError, match="participation"):
        client.get_participation(event)


def test_new_methods_require_login():
    client = SpielerPlusClient()
    with pytest.raises(AuthenticationError):
        client.get_members()
    with pytest.raises(AuthenticationError):
        client.get_birthday("1")
