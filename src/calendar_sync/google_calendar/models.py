from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CalendarEvent:
    """A calendar event, in the shape the sync layer works with.

    ``google_id`` and ``etag`` are ``None`` for events that only exist on
    the SpielerPlus side and have not been created in Google Calendar yet.
    ``external_id`` is the stable :attr:`SpielerPlusEvent.uid` used to
    match events across sync runs.
    """

    external_id: str
    title: str
    description: str
    start: datetime
    end: datetime
    google_id: str | None = None
    etag: str | None = None
