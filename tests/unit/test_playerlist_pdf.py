from datetime import date

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject

from calendar_sync.playerlist import PlayerListError, PlayerRow, fill_player_list, normalize_age_class
from calendar_sync.playerlist.pdf import discover_layout

COLUMNS = (103, 244, 383)
ROWS_PER_PAGE = 4


def _widget(writer, page, name, x, y, button=False):
    field = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/T"): TextStringObject(name),
            NameObject("/FT"): NameObject("/Btn" if button else "/Tx"),
            NameObject("/Rect"): ArrayObject(
                [FloatObject(x), FloatObject(y), FloatObject(x + 100), FloatObject(y + 14)]
            ),
        }
    )
    if button:
        field[NameObject("/AP")] = DictionaryObject(
            {
                NameObject("/N"): DictionaryObject(
                    {NameObject("/Ja"): DictionaryObject(), NameObject("/Off"): DictionaryObject()}
                )
            }
        )
    ref = writer._add_object(field)
    page.setdefault(NameObject("/Annots"), ArrayObject()).append(ref)
    writer._root_object.setdefault(
        NameObject("/AcroForm"), DictionaryObject({NameObject("/Fields"): ArrayObject()})
    )["/Fields"].append(ref)


@pytest.fixture
def template(tmp_path):
    """A two-page stand-in for the association's form.

    Like the real one, page 1 names its cells sensibly while page 2 uses
    unrelated names — and one cell sits a few points off its row — so
    only the geometry tells which cell is which. Fields are added in
    scrambled order to rule out relying on document order.
    """
    writer = PdfWriter()
    page1 = writer.add_blank_page(595, 842)
    page2 = writer.add_blank_page(595, 842)

    _widget(writer, page1, "Name des Vereins", 162, 700)
    for index, y in enumerate((669, 651, 633), start=1):
        _widget(writer, page1, f"Kontrollkästchen{index}", 80, y, button=True)

    for row in reversed(range(1, ROWS_PER_PAGE + 1)):
        y = 600 - row * 21
        _widget(writer, page1, f"Geburtsdatum{row}", COLUMNS[2], y)
        _widget(writer, page1, f"Name{row}", COLUMNS[0], y)
        _widget(writer, page1, f"Vorname{row}", COLUMNS[1], y)

    for offset, row in enumerate(range(ROWS_PER_PAGE + 1, 2 * ROWS_PER_PAGE + 1)):
        y = 780 - offset * 21
        _widget(writer, page2, f"Text{50 + offset}", COLUMNS[2], y)
        _widget(writer, page2, str(row), COLUMNS[0], y - (3 if offset == 1 else 0))
        _widget(writer, page2, f"Text{10 + offset}", COLUMNS[1], y)
    _widget(writer, page2, "Datum", 59, 63)
    _widget(writer, page2, "Unterschrift", COLUMNS[1], 63)

    path = tmp_path / "template.pdf"
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def test_discover_layout_reads_the_table_in_reading_order(template):
    layout = discover_layout(PdfReader(template))

    assert [(r.last_name.name, r.first_name.name, r.birthday.name) for r in layout.rows] == [
        ("Name1", "Vorname1", "Geburtsdatum1"),
        ("Name2", "Vorname2", "Geburtsdatum2"),
        ("Name3", "Vorname3", "Geburtsdatum3"),
        ("Name4", "Vorname4", "Geburtsdatum4"),
        ("5", "Text10", "Text50"),
        ("6", "Text11", "Text51"),
        ("7", "Text12", "Text52"),
        ("8", "Text13", "Text53"),
    ]
    assert layout.rows[4].last_name.page_index == 1


def test_discover_layout_finds_the_other_fields(template):
    layout = discover_layout(PdfReader(template))

    assert layout.club.name == "Name des Vereins"
    assert layout.date.name == "Datum"
    assert [box.name for box in layout.age_classes] == [
        "Kontrollkästchen1",
        "Kontrollkästchen2",
        "Kontrollkästchen3",
    ]
    assert layout.age_class_on_state == "/Ja"


def test_discover_layout_rejects_a_pdf_without_a_table(tmp_path):
    writer = PdfWriter()
    page = writer.add_blank_page(595, 842)
    _widget(writer, page, "Irgendwas", 100, 100)

    with pytest.raises(PlayerListError, match="player table"):
        discover_layout(writer)


def _rows(count):
    return [
        PlayerRow(f"Nach{i}", f"Vor{i}", date(2019, 4, i) if i % 2 else None, str(i), "x")
        for i in range(1, count + 1)
    ]


def test_fill_player_list_writes_rows_across_pages(template, tmp_path):
    output = tmp_path / "out" / "list.pdf"

    fill_player_list(
        template,
        output,
        _rows(6),
        club="VfL Ulm",
        list_date=date(2026, 9, 27),
        age_class="f-junioren",
    )

    fields = {name: field.get("/V") for name, field in PdfReader(output).get_fields().items()}
    assert fields["Name1"] == "Nach1"
    assert fields["Vorname1"] == "Vor1"
    assert fields["Geburtsdatum1"] == "01.04.2019"
    assert fields["Geburtsdatum2"] is None
    assert fields["5"] == "Nach5"
    assert fields["Text11"] == "Vor6"
    assert fields["7"] is None
    assert fields["Name des Vereins"] == "VfL Ulm"
    assert fields["Datum"] == "27.09.2026"
    assert fields["Kontrollkästchen2"] == "/Ja"
    assert fields["Kontrollkästchen1"] is None
    assert fields["Unterschrift"] is None


def test_fill_player_list_refuses_more_players_than_rows(template, tmp_path):
    with pytest.raises(PlayerListError, match="room for 8 players"):
        fill_player_list(template, tmp_path / "list.pdf", _rows(9))


def test_fill_player_list_reports_an_unreadable_template(tmp_path):
    with pytest.raises(PlayerListError, match="could not read"):
        fill_player_list(tmp_path / "missing.pdf", tmp_path / "list.pdf", _rows(1))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("F", "f-junioren"), ("f-jugend", "f-junioren"), ("E-Junioren", "e-junioren"), ("Bambinis", "bambini")],
)
def test_normalize_age_class(raw, expected):
    assert normalize_age_class(raw) == expected


def test_normalize_age_class_rejects_unknown_values():
    with pytest.raises(PlayerListError, match="unknown age class"):
        normalize_age_class("D-Junioren")
