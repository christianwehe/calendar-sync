from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

PLAYER_ROLES = frozenset({"Spieler", "Player"})
"""Role labels SpielerPlus gives a team member who plays (as opposed to
coaching or managing). Members can hold several roles at once — a parent
who coaches and whose child plays carries both ``Trainer`` and ``Spieler``."""


class Attendance(Enum):
    """A member's participation status for an event."""

    ACCEPTED = "accepted"
    DECLINED = "declined"
    UNSURE = "unsure"
    UNKNOWN = "unknown"  # no response has been given yet
    NOT_NOMINATED = "not_nominated"  # left out of the squad for this event


@dataclass(frozen=True)
class Team:
    id: str
    name: str


@dataclass(frozen=True)
class TeamMember:
    """A member of the currently active team, as listed on "Mitglieder"."""

    id: str
    """SpielerPlus user id — the same id used by :meth:`SpielerPlusClient.switch_user`
    and the key the per-event participation list is joined on."""

    name: str
    """The member's display name, exactly as SpielerPlus renders it."""

    roles: tuple[str, ...]
    """Role labels shown next to the member, e.g. ``("Trainer", "Spieler")``."""

    birthday: date | None = None
    """Only populated when the member's profile page was fetched as well,
    and only when a birthday is set there at all.

    Note this is the birthday of whoever *owns the account*, which for
    youth teams is usually a parent rather than the child who plays."""

    @property
    def is_player(self) -> bool:
        return bool(PLAYER_ROLES.intersection(self.roles))


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
