"""Turning a SpielerPlus squad into the rows of an association player list.

The association's form wants one line per player: surname, first name,
birth date. SpielerPlus models a team as *accounts*, which in youth
football usually belong to a parent rather than to the child who plays,
so both the names and the birth dates need care — see :func:`split_name`
and :func:`implausible_age`.
"""

from __future__ import annotations

import re
from datetime import date

from ..spielerplus import Attendance, TeamMember
from .models import PlayerRow

DEFAULT_EXCLUDED = frozenset({Attendance.DECLINED})
"""Statuses that keep a player off the list. Everyone else makes it on,
including members who simply have not answered yet."""

MAX_PLAUSIBLE_PLAYER_AGE = 18
"""Above this, a birthday almost certainly belongs to the parent who owns
the account rather than to the child who plays."""

_PARENTHESISED_NAME = re.compile(r"^(?P<before>[^(]*)\((?P<inside>[^)]*)\)(?P<after>.*)$")


def split_name(display_name: str) -> tuple[str, str]:
    """Split a SpielerPlus display name into ``(first_name, last_name)``.

    Teams whose accounts are registered by parents commonly name them
    ``"Parent (Child) Surname"`` — the child who actually plays is the
    part in parentheses, and that is the name the association's list
    wants. So ``"Ahmet (Çınar) Utlu"`` becomes ``("Çınar", "Utlu")``.

    Without parentheses the name is taken at face value and split on the
    last space: ``"Joel Adebare"`` becomes ``("Joel", "Adebare")``.
    Either part can come back empty for accounts carrying only a single
    word (``"Sam"``) or no surname after the parentheses
    (``"Wladimir (Adrian)"``); the corresponding cell is then left blank
    rather than guessed at.
    """
    display_name = " ".join(display_name.split())

    match = _PARENTHESISED_NAME.match(display_name)
    if match is not None:
        return match.group("inside").strip(), match.group("after").strip()

    first, _, last = display_name.rpartition(" ")
    return (first.strip(), last.strip()) if first else (last.strip(), "")


def select_players(
    members: list[TeamMember],
    participation: dict[str, Attendance],
    excluded: frozenset[Attendance] = DEFAULT_EXCLUDED,
) -> list[TeamMember]:
    """Pick the squad members who belong on the list for one event.

    Only members holding the *player* role are considered — coaches and
    managers are left off even when they responded. A member missing
    from ``participation`` counts as not having answered, which keeps
    them on the list.
    """
    return [
        member
        for member in members
        if member.is_player
        and participation.get(member.id, Attendance.UNKNOWN) not in excluded
    ]


def _sort_key(row: PlayerRow) -> tuple:
    # Rows without a surname sort last: they need filling in by hand, and
    # an empty first column mid-list reads like a mistake on the form.
    return (not row.last_name, row.last_name.casefold(), row.first_name.casefold())


def build_rows(members: list[TeamMember]) -> list[PlayerRow]:
    """Build the player list's rows, sorted by surname."""
    rows = []
    for member in members:
        first_name, last_name = split_name(member.name)
        rows.append(
            PlayerRow(
                last_name=last_name,
                first_name=first_name,
                birthday=member.birthday,
                member_id=member.id,
                source_name=member.name,
            )
        )
    return sorted(rows, key=_sort_key)


def age_on(birthday: date, reference: date) -> int:
    """Age in completed years at ``reference``."""
    had_birthday = (reference.month, reference.day) >= (birthday.month, birthday.day)
    return reference.year - birthday.year - (0 if had_birthday else 1)


def implausible_age(
    row: PlayerRow, reference: date, max_age: int = MAX_PLAUSIBLE_PLAYER_AGE
) -> int | None:
    """Return the row's age at ``reference`` if it is too old to be the
    player's own, else ``None``.

    SpielerPlus stores one birthday per *account*, so a parent-owned
    account contributes the parent's birthday. There is no way to tell
    the two apart from the data, but an age well beyond youth football
    is a reliable enough signal to warn about.
    """
    if row.birthday is None:
        return None
    age = age_on(row.birthday, reference)
    return age if age > max_age else None
