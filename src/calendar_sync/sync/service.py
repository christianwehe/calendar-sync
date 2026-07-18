"""One-way sync of SpielerPlus events into Google Calendar."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..google_calendar.models import CalendarEvent
from ..spielerplus.models import SpielerPlusEvent


class SpielerPlusSource(Protocol):
    def get_events(self) -> list[SpielerPlusEvent]: ...


class GoogleCalendarTarget(Protocol):
    def list_managed_events(self) -> dict[str, CalendarEvent]: ...
    def create_event(self, event: CalendarEvent) -> CalendarEvent: ...
    def update_event(self, event: CalendarEvent) -> CalendarEvent: ...
    def delete_event(self, google_id: str) -> None: ...


TitleBuilder = Callable[[SpielerPlusEvent], str]
DescriptionBuilder = Callable[[SpielerPlusEvent], str]


def default_title(event: SpielerPlusEvent) -> str:
    if event.subtitle:
        return f"{event.title} – {event.subtitle}"
    return event.title


def default_description(event: SpielerPlusEvent) -> str:
    lines = [
        f"Synced from SpielerPlus ({event.event_type} #{event.id}).",
        f"Attendance in SpielerPlus: {event.attendance.value}",
    ]
    if event.end_is_estimated:
        lines.append("End time was not published by SpielerPlus; estimated as start + 2h.")
    return "\n".join(lines)


@dataclass
class SyncResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.created) + len(self.updated) + len(self.deleted) + len(self.unchanged)


class SyncService:
    """Copies events from SpielerPlus into Google Calendar (one-way).

    Matching between the two sides uses :attr:`SpielerPlusEvent.uid`,
    stored as a private extended property on each Google Calendar event
    (see :class:`calendar_sync.google_calendar.GoogleCalendarClient`).
    Events that disappear from SpielerPlus are deleted from Google
    Calendar. Nothing is ever written back to SpielerPlus.

    Both dependencies are accepted as :class:`typing.Protocol`-typed
    values so tests can pass lightweight fakes instead of real clients.
    """

    def __init__(
        self,
        spielerplus: SpielerPlusSource,
        google_calendar: GoogleCalendarTarget,
        title_builder: TitleBuilder = default_title,
        description_builder: DescriptionBuilder = default_description,
    ):
        self._spielerplus = spielerplus
        self._google_calendar = google_calendar
        self._title_builder = title_builder
        self._description_builder = description_builder

    def sync(self) -> SyncResult:
        sp_events = self._spielerplus.get_events()
        existing = self._google_calendar.list_managed_events()

        result = SyncResult()
        seen: set[str] = set()

        for sp_event in sp_events:
            desired = self._to_calendar_event(sp_event)
            seen.add(desired.external_id)
            current = existing.get(desired.external_id)

            if current is None:
                self._google_calendar.create_event(desired)
                result.created.append(desired.external_id)
            elif _differs(current, desired):
                self._google_calendar.update_event(
                    CalendarEvent(
                        external_id=desired.external_id,
                        title=desired.title,
                        description=desired.description,
                        start=desired.start,
                        end=desired.end,
                        google_id=current.google_id,
                    )
                )
                result.updated.append(desired.external_id)
            else:
                result.unchanged.append(desired.external_id)

        for external_id, current in existing.items():
            if external_id not in seen:
                self._google_calendar.delete_event(current.google_id)
                result.deleted.append(external_id)

        return result

    def _to_calendar_event(self, event: SpielerPlusEvent) -> CalendarEvent:
        return CalendarEvent(
            external_id=event.uid,
            title=self._title_builder(event),
            description=self._description_builder(event),
            start=event.start,
            end=event.end,
        )


def _differs(current: CalendarEvent, desired: CalendarEvent) -> bool:
    return (
        current.title != desired.title
        or current.description != desired.description
        or current.start != desired.start
        or current.end != desired.end
    )
