from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError

from calendar_sync.google_calendar.client import GoogleCalendarClient, build_service
from calendar_sync.google_calendar.exceptions import GoogleCalendarError
from calendar_sync.google_calendar.models import CalendarEvent

CALENDAR_ID = "team@group.calendar.google.com"
SYNC_TAG = "spielerplus-sync"


class _FakeResp:
    def __init__(self, status: int):
        self.status = status
        self.reason = "error"

    def get(self, key, default=""):
        return default


def _http_error(status: int) -> HttpError:
    return HttpError(resp=_FakeResp(status), content=b'{"error": {"message": "boom"}}')


def _api_event(google_id: str, external_id: str, summary="Training", status="confirmed") -> dict:
    return {
        "id": google_id,
        "status": status,
        "summary": summary,
        "description": "desc",
        "start": {"dateTime": "2026-07-18T19:00:00+02:00"},
        "end": {"dateTime": "2026-07-18T21:00:00+02:00"},
        "extendedProperties": {"private": {"managed_by": SYNC_TAG, "spielerplus_id": external_id}},
    }


def _make_client(service: MagicMock) -> GoogleCalendarClient:
    return GoogleCalendarClient(service, CALENDAR_ID, sync_tag=SYNC_TAG, timezone="Europe/Berlin")


def _sample_event(external_id="training-111", google_id=None) -> CalendarEvent:
    return CalendarEvent(
        external_id=external_id,
        title="Training",
        description="desc",
        start=datetime(2026, 7, 18, 19, 0),
        end=datetime(2026, 7, 18, 21, 0),
        google_id=google_id,
    )


def test_list_managed_events_filters_by_sync_tag_and_keys_by_external_id():
    service = MagicMock()
    service.events.return_value.list.return_value.execute.return_value = {
        "items": [_api_event("g1", "training-111"), _api_event("g2", "match-222")]
    }

    client = _make_client(service)
    events = client.list_managed_events()

    assert set(events) == {"training-111", "match-222"}
    assert events["training-111"].google_id == "g1"

    _, kwargs = service.events.return_value.list.call_args
    assert kwargs["calendarId"] == CALENDAR_ID
    assert kwargs["privateExtendedProperty"] == f"managed_by={SYNC_TAG}"


def test_list_managed_events_skips_cancelled_events():
    service = MagicMock()
    service.events.return_value.list.return_value.execute.return_value = {
        "items": [_api_event("g1", "training-111", status="cancelled")]
    }

    client = _make_client(service)
    assert client.list_managed_events() == {}


def test_list_managed_events_paginates():
    service = MagicMock()
    service.events.return_value.list.return_value.execute.side_effect = [
        {"items": [_api_event("g1", "training-111")], "nextPageToken": "page-2"},
        {"items": [_api_event("g2", "match-222")]},
    ]

    client = _make_client(service)
    events = client.list_managed_events()

    assert set(events) == {"training-111", "match-222"}
    assert service.events.return_value.list.return_value.execute.call_count == 2


def test_list_managed_events_wraps_http_errors():
    service = MagicMock()
    service.events.return_value.list.return_value.execute.side_effect = _http_error(500)

    client = _make_client(service)
    with pytest.raises(GoogleCalendarError):
        client.list_managed_events()


def test_list_managed_events_raises_on_missing_external_id():
    service = MagicMock()
    bad_event = _api_event("g1", "training-111")
    del bad_event["extendedProperties"]["private"]["spielerplus_id"]
    service.events.return_value.list.return_value.execute.return_value = {"items": [bad_event]}

    client = _make_client(service)
    with pytest.raises(GoogleCalendarError):
        client.list_managed_events()


def test_create_event_sends_expected_body():
    service = MagicMock()
    service.events.return_value.insert.return_value.execute.return_value = _api_event(
        "new-id", "training-111"
    )

    client = _make_client(service)
    result = client.create_event(_sample_event())

    assert result.google_id == "new-id"
    _, kwargs = service.events.return_value.insert.call_args
    assert kwargs["calendarId"] == CALENDAR_ID
    body = kwargs["body"]
    assert body["summary"] == "Training"
    assert body["start"] == {"dateTime": "2026-07-18T19:00:00", "timeZone": "Europe/Berlin"}
    assert body["extendedProperties"]["private"] == {
        "managed_by": SYNC_TAG,
        "spielerplus_id": "training-111",
    }


def test_update_event_requires_google_id():
    client = _make_client(MagicMock())
    with pytest.raises(ValueError):
        client.update_event(_sample_event(google_id=None))


def test_update_event_sends_expected_request():
    service = MagicMock()
    service.events.return_value.update.return_value.execute.return_value = _api_event(
        "g1", "training-111"
    )

    client = _make_client(service)
    client.update_event(_sample_event(google_id="g1"))

    _, kwargs = service.events.return_value.update.call_args
    assert kwargs["calendarId"] == CALENDAR_ID
    assert kwargs["eventId"] == "g1"


def test_delete_event_calls_api():
    service = MagicMock()
    client = _make_client(service)

    client.delete_event("g1")

    service.events.return_value.delete.assert_called_once_with(calendarId=CALENDAR_ID, eventId="g1")


@pytest.mark.parametrize("status", [404, 410])
def test_delete_event_swallows_already_gone_errors(status):
    service = MagicMock()
    service.events.return_value.delete.return_value.execute.side_effect = _http_error(status)

    client = _make_client(service)
    client.delete_event("g1")  # must not raise


def test_delete_event_raises_on_other_errors():
    service = MagicMock()
    service.events.return_value.delete.return_value.execute.side_effect = _http_error(500)

    client = _make_client(service)
    with pytest.raises(GoogleCalendarError):
        client.delete_event("g1")


def test_build_service_reuses_valid_cached_token(tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text("{}")

    fake_creds = MagicMock(valid=True)

    with (
        patch("calendar_sync.google_calendar.client.Credentials") as creds_cls,
        patch("calendar_sync.google_calendar.client.build") as build_fn,
        patch("calendar_sync.google_calendar.client.InstalledAppFlow") as flow_cls,
    ):
        creds_cls.from_authorized_user_file.return_value = fake_creds
        build_fn.return_value = "the-service"

        result = build_service("creds.json", token_path)

        assert result == "the-service"
        flow_cls.from_client_secrets_file.assert_not_called()
        build_fn.assert_called_once_with("calendar", "v3", credentials=fake_creds)


def test_build_service_runs_oauth_flow_when_no_cached_token(tmp_path):
    token_path = tmp_path / "token.json"

    fake_creds = MagicMock(valid=True)
    fake_creds.to_json.return_value = "{}"

    with (
        patch("calendar_sync.google_calendar.client.build") as build_fn,
        patch("calendar_sync.google_calendar.client.InstalledAppFlow") as flow_cls,
    ):
        flow_cls.from_client_secrets_file.return_value.run_local_server.return_value = fake_creds
        build_fn.return_value = "the-service"

        result = build_service("creds.json", token_path)

        assert result == "the-service"
        assert token_path.exists()
        flow_cls.from_client_secrets_file.assert_called_once_with(
            "creds.json", ["https://www.googleapis.com/auth/calendar.events"]
        )
