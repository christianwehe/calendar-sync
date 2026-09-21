from datetime import date

import pytest

from calendar_sync.playerlist import PlayerRow, build_rows, implausible_age, select_players, split_name
from calendar_sync.playerlist.roster import age_on
from calendar_sync.spielerplus import Attendance, TeamMember


@pytest.mark.parametrize(
    ("display_name", "expected"),
    [
        # Parent-owned accounts: the child is the part in parentheses.
        ("Ahmet (Çınar) Utlu", ("Çınar", "Utlu")),
        ("Kissmat (Samar Shakir) Blasini", ("Samar Shakir", "Blasini")),
        ("Bianca (Amro) Al Faloji", ("Amro", "Al Faloji")),
        # Nothing after the parentheses: the surname stays blank rather
        # than borrowing the parent's first name.
        ("Wladimir (Adrian)", ("Adrian", "")),
        # Plain names split on the last space.
        ("Joel Adebare", ("Joel", "Adebare")),
        ("Anna Maria Beispiel", ("Anna Maria", "Beispiel")),
        ("Sam", ("Sam", "")),
        ("  Joel   Adebare ", ("Joel", "Adebare")),
    ],
)
def test_split_name(display_name, expected):
    assert split_name(display_name) == expected


def _member(member_id, name="A B", roles=("Spieler",), birthday=None):
    return TeamMember(id=member_id, name=name, roles=roles, birthday=birthday)


def test_select_players_drops_declined_players_and_non_players():
    members = [
        _member("1"),
        _member("2"),
        _member("3"),
        _member("4"),
        _member("5"),
        _member("6", roles=("Trainer",)),
        _member("7", roles=("Trainer", "Spieler")),
        _member("8", roles=("Inaktiv",)),
    ]
    participation = {
        "1": Attendance.ACCEPTED,
        "2": Attendance.UNSURE,
        "3": Attendance.DECLINED,
        "4": Attendance.NOT_NOMINATED,
        # "5" is missing: no response given yet.
        "6": Attendance.ACCEPTED,
        "7": Attendance.UNKNOWN,
        "8": Attendance.ACCEPTED,
    }

    selected = select_players(members, participation)

    assert [m.id for m in selected] == ["1", "2", "4", "5", "7"]


def test_build_rows_splits_names_and_sorts_by_surname_with_blanks_last():
    members = [
        _member("1", "Wladimir (Adrian)"),
        _member("2", "Parent (Zoe) Zimmer", birthday=date(2019, 4, 13)),
        _member("3", "Joel Adebare"),
    ]

    rows = build_rows(members)

    assert [(r.last_name, r.first_name) for r in rows] == [
        ("Adebare", "Joel"),
        ("Zimmer", "Zoe"),
        ("", "Adrian"),
    ]
    assert rows[1].birthday_text == "13.04.2019"
    assert rows[0].birthday_text == ""
    assert rows[2].source_name == "Wladimir (Adrian)"


def test_age_on_counts_completed_years():
    assert age_on(date(2019, 9, 27), date(2026, 9, 27)) == 7
    assert age_on(date(2019, 9, 28), date(2026, 9, 27)) == 6


def _row(birthday):
    return PlayerRow("B", "A", birthday, "1", "A B")


def test_implausible_age_flags_birthdays_that_belong_to_an_adult():
    assert implausible_age(_row(date(1992, 4, 9)), date(2026, 9, 27)) == 34


def test_implausible_age_accepts_a_child_or_a_missing_birthday():
    assert implausible_age(_row(date(2019, 4, 13)), date(2026, 9, 27)) is None
    assert implausible_age(_row(None), date(2026, 9, 27)) is None
