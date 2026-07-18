"""Google Calendar client used by the sync service.

The client only ever touches events it created itself: every event it
writes carries a private extended property (``managed_by``) set to a
configurable "sync tag", and :meth:`GoogleCalendarClient.list_managed_events`
filters on that property server-side via the Calendar API's
``privateExtendedProperty`` list filter. This means the sync can safely
run against a calendar that also has manually created events, without
ever reading, modifying or deleting them.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError

from .exceptions import GoogleCalendarError
from .models import CalendarEvent

logger = logging.getLogger(__name__)

DEFAULT_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

_PROPERTY_MANAGED_BY = "managed_by"
_PROPERTY_EXTERNAL_ID = "spielerplus_id"


def build_service(
    credentials_path: str | Path,
    token_path: str | Path,
    scopes: list[str] | None = None,
) -> Resource:
    """Build an authenticated Calendar API resource, running the OAuth
    installed-app flow (opens a browser) the first time and caching a
    refresh token at ``token_path`` afterwards."""
    scopes = scopes or DEFAULT_SCOPES
    token_path = Path(token_path)

    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), scopes)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return build("calendar", "v3", credentials=creds)


class GoogleCalendarClient:
    def __init__(
        self,
        service: Resource,
        calendar_id: str,
        sync_tag: str,
        timezone: str = "Europe/Berlin",
    ):
        self._service = service
        self._calendar_id = calendar_id
        self._sync_tag = sync_tag
        self._timezone = timezone

    @classmethod
    def from_credentials(
        cls,
        credentials_path: str | Path,
        token_path: str | Path,
        calendar_id: str,
        sync_tag: str = "spielerplus-sync",
        timezone: str = "Europe/Berlin",
        scopes: list[str] | None = None,
    ) -> "GoogleCalendarClient":
        service = build_service(credentials_path, token_path, scopes)
        return cls(service, calendar_id, sync_tag=sync_tag, timezone=timezone)

    def list_managed_events(self) -> dict[str, CalendarEvent]:
        """Return all non-cancelled events this tool previously created on
        the target calendar, keyed by :attr:`CalendarEvent.external_id`."""
        events: dict[str, CalendarEvent] = {}
        page_token: str | None = None
        while True:
            try:
                response = (
                    self._service.events()
                    .list(
                        calendarId=self._calendar_id,
                        privateExtendedProperty=f"{_PROPERTY_MANAGED_BY}={self._sync_tag}",
                        singleEvents=True,
                        pageToken=page_token,
                    )
                    .execute()
                )
            except HttpError as exc:
                raise GoogleCalendarError(f"failed to list events: {exc}") from exc

            for item in response.get("items", []):
                if item.get("status") == "cancelled":
                    continue
                event = self._from_api(item)
                events[event.external_id] = event

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return events

    def create_event(self, event: CalendarEvent) -> CalendarEvent:
        try:
            created = (
                self._service.events()
                .insert(calendarId=self._calendar_id, body=self._to_api(event))
                .execute()
            )
        except HttpError as exc:
            raise GoogleCalendarError(f"failed to create event {event.external_id!r}: {exc}") from exc
        return self._from_api(created)

    def update_event(self, event: CalendarEvent) -> CalendarEvent:
        if not event.google_id:
            raise ValueError("event.google_id is required to update an event")
        try:
            updated = (
                self._service.events()
                .update(calendarId=self._calendar_id, eventId=event.google_id, body=self._to_api(event))
                .execute()
            )
        except HttpError as exc:
            raise GoogleCalendarError(f"failed to update event {event.external_id!r}: {exc}") from exc
        return self._from_api(updated)

    def delete_event(self, google_id: str) -> None:
        try:
            self._service.events().delete(calendarId=self._calendar_id, eventId=google_id).execute()
        except HttpError as exc:
            if exc.resp.status in (404, 410):
                logger.debug("event %s already deleted", google_id)
                return
            raise GoogleCalendarError(f"failed to delete event {google_id!r}: {exc}") from exc

    def _to_api(self, event: CalendarEvent) -> dict[str, Any]:
        return {
            "summary": event.title,
            "description": event.description,
            "start": {"dateTime": event.start.isoformat(), "timeZone": self._timezone},
            "end": {"dateTime": event.end.isoformat(), "timeZone": self._timezone},
            "extendedProperties": {
                "private": {
                    _PROPERTY_MANAGED_BY: self._sync_tag,
                    _PROPERTY_EXTERNAL_ID: event.external_id,
                }
            },
        }

    @staticmethod
    def _from_api(item: dict[str, Any]) -> CalendarEvent:
        private = item.get("extendedProperties", {}).get("private", {})
        external_id = private.get(_PROPERTY_EXTERNAL_ID)
        if not external_id:
            raise GoogleCalendarError(
                f"event {item.get('id')!r} is missing the {_PROPERTY_EXTERNAL_ID!r} extended property"
            )
        return CalendarEvent(
            external_id=external_id,
            title=item.get("summary", ""),
            description=item.get("description", ""),
            start=_parse_api_datetime(item["start"]),
            end=_parse_api_datetime(item["end"]),
            google_id=item["id"],
            etag=item.get("etag"),
        )


def _parse_api_datetime(node: dict[str, str]) -> datetime:
    if "dateTime" in node:
        return datetime.fromisoformat(node["dateTime"])
    return datetime.fromisoformat(node["date"])
