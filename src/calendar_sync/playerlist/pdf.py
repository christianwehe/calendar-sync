"""Filling the association's player-list PDF form.

The form is a plain AcroForm, but only its first page names fields
usefully (``Name1``, ``Vorname1``, ``Geburtsdatum1``, …); the second
page's continuation rows are called ``26``…``56`` and ``Text5``…``Text67``
with a gap in the numbering. Rather than hard-coding that mapping — which
would silently fill the wrong cells the year the association re-exports
the form — the table is recovered from where the fields sit on the page:
three columns of equally many fields, read left to right, top to bottom.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pypdf import PdfWriter

from .models import PlayerListError, PlayerRow

logger = logging.getLogger(__name__)

ROW_TOLERANCE = 8.0
"""Vertical slack, in points, within which fields count as one table row.
The template's cells are not perfectly aligned — a few sit up to 3pt off
their neighbours — while consecutive rows are ~21pt apart."""

COLUMN_TOLERANCE = 12.0
"""Horizontal slack, in points, within which fields count as one column."""

MIN_TABLE_ROWS = 5
"""Fewer aligned rows than this means the table was not found at all."""

AGE_CLASSES = ("e-junioren", "f-junioren", "bambini")
"""The form's age-class checkboxes, in the order they are printed — which
is the order they are matched in, top to bottom."""

_AGE_CLASS_ALIASES = {
    "e": "e-junioren",
    "e-jugend": "e-junioren",
    "f": "f-junioren",
    "f-jugend": "f-junioren",
    "bambinis": "bambini",
}


def normalize_age_class(value: str) -> str:
    """Accept the usual spellings of an age class, e.g. ``"F"``,
    ``"f-jugend"`` or ``"F-Junioren"`` for :data:`AGE_CLASSES`' ``"f-junioren"``."""
    key = value.strip().casefold().replace(" ", "-")
    key = _AGE_CLASS_ALIASES.get(key, key)
    if key not in AGE_CLASSES:
        raise PlayerListError(
            f"unknown age class {value!r}; expected one of {', '.join(AGE_CLASSES)}"
        )
    return key


@dataclass(frozen=True)
class FieldRef:
    """A form field, identified by name and by the page it is filled on
    (field names can repeat across pages)."""

    page_index: int
    name: str


@dataclass(frozen=True)
class RowFields:
    last_name: FieldRef
    first_name: FieldRef
    birthday: FieldRef


@dataclass(frozen=True)
class FormLayout:
    rows: tuple[RowFields, ...]
    club: FieldRef | None
    date: FieldRef | None
    age_classes: tuple[FieldRef, ...]
    age_class_on_state: str


@dataclass(frozen=True)
class _Widget:
    page_index: int
    name: str
    is_button: bool
    x: float
    y: float
    on_state: str | None


def _widgets(pdf) -> list[_Widget]:
    """Collect every form widget with its position on the page."""
    widgets: list[_Widget] = []
    for page_index, page in enumerate(pdf.pages):
        for annotation in page.get("/Annots") or []:
            obj = annotation.get_object()
            if obj.get("/Subtype") != "/Widget":
                continue

            # A widget either carries its own field name, or is a kid of
            # the field that does.
            name, field_type, node = obj.get("/T"), obj.get("/FT"), obj
            while name is None and node.get("/Parent") is not None:
                node = node["/Parent"].get_object()
                name = node.get("/T")
                field_type = field_type or node.get("/FT")
            if name is None:
                continue

            rect = obj.get("/Rect")
            if rect is None:
                continue

            on_state = None
            if field_type == "/Btn":
                appearances = obj.get("/AP")
                normal = appearances.get("/N", {}).get_object() if appearances else {}
                on_state = next((state for state in normal if state != "/Off"), None)

            widgets.append(
                _Widget(
                    page_index=page_index,
                    name=str(name),
                    is_button=field_type == "/Btn",
                    x=float(rect[0]),
                    y=float(rect[1]),
                    on_state=str(on_state) if on_state else None,
                )
            )
    return widgets


def _cluster(values: list[float], tolerance: float) -> list[list[float]]:
    """Group nearby values, e.g. the x positions making up one column."""
    clusters: list[list[float]] = []
    for value in sorted(values):
        if clusters and value - clusters[-1][-1] <= tolerance:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return clusters


def _column_positions(text_widgets: list[_Widget]) -> tuple[float, float, float]:
    """Find the x positions of the table's three columns.

    The table dwarfs everything else on the form, so its columns are
    simply the three x positions shared by the most fields.
    """
    clusters = _cluster([widget.x for widget in text_widgets], COLUMN_TOLERANCE)
    biggest = sorted(clusters, key=len, reverse=True)[:3]
    if len(biggest) < 3 or min(len(cluster) for cluster in biggest) < MIN_TABLE_ROWS:
        raise PlayerListError(
            "could not find a three-column player table in the template; "
            "is this the association's player list form?"
        )
    return tuple(sorted(sum(cluster) / len(cluster) for cluster in biggest))


def _find_field(text_widgets: list[_Widget], needle: str) -> FieldRef | None:
    for widget in text_widgets:
        if needle in widget.name.casefold():
            return FieldRef(widget.page_index, widget.name)
    return None


def discover_layout(pdf) -> FormLayout:
    """Work out which form field belongs to which cell of the form."""
    widgets = _widgets(pdf)
    text_widgets = [widget for widget in widgets if not widget.is_button]
    columns = _column_positions(text_widgets)

    in_table = set()
    rows: list[tuple[int, float, RowFields]] = []
    by_page: dict[int, list[_Widget]] = defaultdict(list)
    for widget in text_widgets:
        by_page[widget.page_index].append(widget)

    for page_index, page_widgets in by_page.items():
        lines: dict[float, list[_Widget]] = defaultdict(list)
        for line in _cluster([widget.y for widget in page_widgets], ROW_TOLERANCE):
            key = line[0]
            for widget in page_widgets:
                if line[0] <= widget.y <= line[-1]:
                    lines[key].append(widget)

        for key, line_widgets in lines.items():
            cells = []
            for column_x in columns:
                matches = [w for w in line_widgets if abs(w.x - column_x) <= COLUMN_TOLERANCE]
                cells.append(matches[0] if len(matches) == 1 else None)
            if any(cell is None for cell in cells):
                continue  # not a table row: the club name, the signature line, …
            rows.append(
                (
                    page_index,
                    key,
                    RowFields(*(FieldRef(cell.page_index, cell.name) for cell in cells)),
                )
            )
            in_table.update(cell.name for cell in cells)

    # Top of the page is the highest y, and pages run front to back.
    rows.sort(key=lambda row: (row[0], -row[1]))

    outside_table = [widget for widget in text_widgets if widget.name not in in_table]
    buttons = sorted(
        (widget for widget in widgets if widget.is_button and widget.on_state),
        key=lambda widget: (widget.page_index, -widget.y),
    )

    return FormLayout(
        rows=tuple(row[2] for row in rows),
        club=_find_field(outside_table, "verein"),
        date=_find_field(outside_table, "datum"),
        age_classes=tuple(FieldRef(button.page_index, button.name) for button in buttons),
        age_class_on_state=buttons[0].on_state if buttons else "/Ja",
    )


def fill_player_list(
    template: Path,
    output: Path,
    rows: list[PlayerRow],
    *,
    club: str | None = None,
    list_date: date | None = None,
    age_class: str | None = None,
) -> None:
    """Write ``rows`` into a copy of the association's player list form.

    ``age_class`` is one of :data:`AGE_CLASSES` and ticks the matching
    checkbox; the form's remaining fields (the signature line) are left
    empty to be filled in by hand.
    """
    try:
        writer = PdfWriter(clone_from=str(template))
    except (OSError, ValueError) as exc:
        raise PlayerListError(f"could not read the template {template}: {exc}") from exc

    layout = discover_layout(writer)
    if len(rows) > len(layout.rows):
        raise PlayerListError(
            f"the template has room for {len(layout.rows)} players, but the list has {len(rows)}"
        )

    values: dict[int, dict[str, str]] = defaultdict(dict)

    def put(field: FieldRef | None, value: str) -> None:
        if field is not None and value:
            values[field.page_index][field.name] = value

    for row, fields in zip(rows, layout.rows):
        put(fields.last_name, row.last_name)
        put(fields.first_name, row.first_name)
        put(fields.birthday, row.birthday_text)

    put(layout.club, club or "")
    put(layout.date, f"{list_date:%d.%m.%Y}" if list_date else "")

    if age_class is not None:
        try:
            box = layout.age_classes[AGE_CLASSES.index(age_class)]
        except ValueError:
            raise PlayerListError(
                f"unknown age class {age_class!r}; expected one of {', '.join(AGE_CLASSES)}"
            ) from None
        except IndexError:
            raise PlayerListError(
                f"the template has {len(layout.age_classes)} age-class checkboxes, "
                f"too few to tick {age_class!r}"
            ) from None
        values[box.page_index][box.name] = layout.age_class_on_state

    # Without this, viewers that do not generate field appearances
    # themselves (notably some browsers' built-in PDF readers) show the
    # form as empty even though the values are in the file.
    writer.set_need_appearances_writer(True)
    for page_index, page_values in values.items():
        writer.update_page_form_field_values(
            writer.pages[page_index], page_values, auto_regenerate=False
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as handle:
        writer.write(handle)
