from __future__ import annotations

from dataclasses import dataclass
from datetime import date


class PlayerListError(Exception):
    """Raised when a player list cannot be built or written."""


@dataclass(frozen=True)
class PlayerRow:
    """One line of the association's player list."""

    last_name: str
    first_name: str
    birthday: date | None
    member_id: str
    """SpielerPlus user id the row was built from, for error messages."""

    source_name: str
    """The raw SpielerPlus display name the names were split out of."""

    @property
    def birthday_text(self) -> str:
        return f"{self.birthday:%d.%m.%Y}" if self.birthday else ""
