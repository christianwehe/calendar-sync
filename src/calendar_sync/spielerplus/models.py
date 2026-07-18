from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Attendance(Enum):
    """A member's participation status for an event."""

    ACCEPTED = "accepted"
    DECLINED = "declined"
    UNSURE = "unsure"
    UNKNOWN = "unknown"  # no response has been given yet


@dataclass(frozen=True)
class Team:
    id: str
    name: str


@dataclass(frozen=True)
class SpielerPlusEvent:
    """A single event as scraped from the SpielerPlus "Events" page."""

    id: str
    """SpielerPlus' internal event id (stable across requests)."""

    event_type: str
    """Raw SpielerPlus event-type slug, e.g. ``training`` or ``match``."""

    title: str
    subtitle: str
    start: datetime
    end: datetime
    end_is_estimated: bool
    """True if SpielerPlus did not publish an end time and it was derived
    as ``start + 2h``."""

    attendance: Attendance

    @property
    def uid(self) -> str:
        """A stable identifier used to match this event to a Google Calendar
        event across sync runs."""
        return f"{self.event_type}-{self.id}"
