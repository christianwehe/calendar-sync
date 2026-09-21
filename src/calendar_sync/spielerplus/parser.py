"""HTML parsing helpers for the SpielerPlus "Events" page.

SpielerPlus has no public API, so this client scrapes server-rendered
HTML. The selectors and login/team-switch flow below are ported from the
reverse-engineered Rust reference implementation at
https://github.com/janic0/autospieler/blob/main/src/main.rs, translated
to BeautifulSoup and adapted to be independently testable.

All functions here are pure (HTML string in, data out) so they can be
unit tested against static fixtures without any network access.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

from .exceptions import ParseError
from .models import Attendance, SpielerPlusEvent, Team, TeamMember

logger = logging.getLogger(__name__)

_LOGIN_TITLES = {"Einloggen", "Login"}
_TEAM_SELECT_TITLES = {"Team auswählen", "Select team"}
_EVENTS_TITLES = {"Termine", "Events"}

_ATTENDANCE_BY_TITLE = {
    "Zugesagt": Attendance.ACCEPTED,
    "Confirmed": Attendance.ACCEPTED,
    "Unsicher": Attendance.UNSURE,
    "Unsure": Attendance.UNSURE,
    "Absagen / Abwesend": Attendance.DECLINED,
    "Declined / Absent": Attendance.DECLINED,
}

# The per-event participation list groups members under collapsible
# sections keyed by SpielerPlus' own numeric participation code (the same
# values `set_attendance` posts, plus two the user cannot pick). The code
# is read off the section's collapse target rather than its heading,
# which keeps this independent of the account's UI language.
_ATTENDANCE_BY_CODE = {
    "1": Attendance.ACCEPTED,
    "2": Attendance.UNSURE,
    "0": Attendance.DECLINED,
    "3": Attendance.NOT_NOMINATED,
    "99": Attendance.UNKNOWN,
}

_COLLAPSE_TARGET_RE = re.compile(r"#(\d+)-parti-collapse")

_DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.?")
_FULL_DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*(AM|PM)?", re.IGNORECASE)

DEFAULT_EVENT_DURATION = timedelta(hours=2)


def page_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one("title")
    if title is None:
        raise ParseError("page has no <title> element")
    return title.get_text(strip=True)


def requires_login(html: str) -> bool:
    return page_title(html) in _LOGIN_TITLES


def is_team_selection_page(html: str) -> bool:
    return page_title(html) in _TEAM_SELECT_TITLES


def is_events_page(html: str) -> bool:
    return page_title(html) in _EVENTS_TITLES


def extract_csrf_token(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    field = soup.select_one("form#login-form input[name='_csrf']")
    if field is None or not field.get("value"):
        raise ParseError("missing CSRF token on login page")
    return field["value"]


def parse_teams(html: str) -> list[Team]:
    """Parse the (informational) team list shown after login for accounts
    that manage several profiles/teams under one login."""
    soup = BeautifulSoup(html, "html.parser")
    teams: list[Team] = []
    for item in soup.select(".select-team-item"):
        name_el = item.select_one(".select-team-item-meta h4")
        link_el = item.select_one("a")
        if name_el is None or link_el is None or not link_el.get("href"):
            raise ParseError("malformed .select-team-item entry")
        href = link_el["href"]
        if "=" not in href:
            raise ParseError(f"team link href has no id parameter: {href!r}")
        team_id = href.rsplit("=", 1)[-1]
        teams.append(Team(id=team_id, name=name_el.get_text(strip=True)))
    return teams


def parse_time_string(raw: str) -> str | None:
    """Normalize a SpielerPlus time value to 24h ``HH:MM``.

    Returns ``None`` for the SpielerPlus placeholder ``"-:-"`` (no time
    set) or for unparseable input.
    """
    raw = raw.strip()
    if raw == "-:-" or not raw:
        return None

    match = _TIME_RE.fullmatch(raw)
    if match is None:
        return None

    hours, minutes, meridiem = match.groups()
    hours_int = int(hours)

    if meridiem is None:
        # already 24h, just normalize zero-padding
        return f"{hours_int:02d}:{minutes}"

    meridiem = meridiem.upper()
    if hours_int == 12:
        hours_int = 0 if meridiem == "AM" else 12
    elif meridiem == "PM":
        hours_int += 12

    return f"{hours_int:02d}:{minutes}"


def resolve_event_date(day: int, month: int, reference_date: date, grace_days: int = 45) -> date:
    """Resolve a year-less ``day.month`` date shown by SpielerPlus.

    SpielerPlus lists events without a year. We assume the event list only
    ever contains events at or after ``reference_date`` (with a small
    grace window into the past to tolerate timezone/midnight skew): pick
    ``reference_date.year``, and roll over to next year if that would put
    the event further than ``grace_days`` in the past.
    """
    candidate = date(reference_date.year, month, day)
    if candidate < reference_date - timedelta(days=grace_days):
        candidate = date(reference_date.year + 1, month, day)
    return candidate


def _parse_attendance(widget: Tag) -> Attendance:
    selected = widget.select_one(".selected")
    if selected is None:
        return Attendance.UNKNOWN
    title = (selected.get("title") or "").strip()
    return _ATTENDANCE_BY_TITLE.get(title, Attendance.UNKNOWN)


def _parse_event(event_el: Tag, reference_date: datetime) -> SpielerPlusEvent:
    panel = event_el.select_one(".panel")
    if panel is None:
        raise ParseError("event is missing .panel")

    panel_id = panel.get("id")
    if not panel_id:
        raise ParseError("event .panel has no id")
    parts = panel_id.split("-")
    if len(parts) < 3:
        raise ParseError(f"unexpected panel id format: {panel_id!r}")
    event_type, event_id = parts[1], parts[2].strip()

    heading_text = event_el.select_one(".panel-heading-text")
    if heading_text is None:
        raise ParseError("event is missing .panel-heading-text")
    title_el = heading_text.select_one(".panel-title")
    if title_el is None:
        raise ParseError("event is missing .panel-title")
    title = title_el.get_text(strip=True)
    subtitle_el = heading_text.select_one(".panel-subtitle")
    subtitle = subtitle_el.get_text(strip=True) if subtitle_el else ""

    heading_info = event_el.select_one(".panel-heading-info")
    if heading_info is None:
        raise ParseError("event is missing .panel-heading-info")
    date_el = heading_info.select_one(".panel-subtitle")
    if date_el is None:
        raise ParseError("event is missing date in .panel-heading-info .panel-subtitle")
    date_match = _DATE_RE.search(date_el.get_text(strip=True))
    if date_match is None:
        raise ParseError(f"could not parse event date: {date_el.get_text(strip=True)!r}")
    day, month = int(date_match.group(1)), int(date_match.group(2))
    event_date = resolve_event_date(day, month, reference_date.date())

    # SpielerPlus renders up to three time slots per event: a meetup
    # time ("Treffpunkt", index 0 — often earlier than the event itself
    # and sometimes unset, shown as "-:-"), the actual start time
    # (index 1), and the end time (index 2, also optional). We want the
    # actual start, so index 1 takes priority; index 0 is only used as a
    # fallback for the rare event that only has a single time slot at
    # all (no separate meetup/start distinction).
    time_values = [el.get_text(strip=True) for el in panel.select(".event-time-value")]

    start_str = None
    if len(time_values) > 1:
        start_str = parse_time_string(time_values[1])
    if start_str is None and len(time_values) > 0:
        start_str = parse_time_string(time_values[0])
    if start_str is None:
        raise ParseError(f"could not determine start time for event {event_id}")
    start = datetime.combine(event_date, datetime.strptime(start_str, "%H:%M").time())

    end_str = parse_time_string(time_values[2]) if len(time_values) > 2 else None
    end_is_estimated = end_str is None
    if end_str is not None:
        end = datetime.combine(event_date, datetime.strptime(end_str, "%H:%M").time())
    else:
        end = start + DEFAULT_EVENT_DURATION

    widget = event_el.select_one(".participation-widget-buttons")
    attendance = _parse_attendance(widget) if widget is not None else Attendance.UNKNOWN

    return SpielerPlusEvent(
        id=event_id,
        event_type=event_type,
        title=title,
        subtitle=subtitle,
        start=start,
        end=end,
        end_is_estimated=end_is_estimated,
        attendance=attendance,
    )


def parse_events(html: str, reference_date: datetime | None = None) -> list[SpielerPlusEvent]:
    """Parse all events from a SpielerPlus "Events" page.

    ``reference_date`` defaults to ``datetime.now()`` and is used to
    resolve the year of SpielerPlus' year-less event dates; pass an
    explicit value in tests to keep them deterministic.
    """
    if reference_date is None:
        reference_date = datetime.now()

    soup = BeautifulSoup(html, "html.parser")
    return [_parse_event(event_el, reference_date) for event_el in soup.select(".event")]


def _user_id_from_href(href: str) -> str | None:
    """Pull the user id out of a ``/user/view?id=123`` link."""
    ids = parse_qs(urlparse(href).query).get("id")
    return ids[0] if ids else None


def parse_members(html: str) -> list[TeamMember]:
    """Parse the team's "Mitglieder" page into members with their roles."""
    soup = BeautifulSoup(html, "html.parser")
    members: list[TeamMember] = []
    for item in soup.select(".team-list-item"):
        link = item.select_one("a.list-item-link[href]")
        if link is None:
            raise ParseError("member entry has no profile link")
        member_id = _user_id_from_href(link["href"])
        if member_id is None:
            raise ParseError(f"member link has no id parameter: {link['href']!r}")

        label = item.select_one(".list-label")
        if label is None:
            raise ParseError(f"member {member_id} has no .list-label")

        roles = tuple(role.get_text(strip=True) for role in item.select(".user-role-item"))
        members.append(TeamMember(id=member_id, name=label.get_text(strip=True), roles=roles))
    return members


def parse_participation(html: str) -> dict[str, Attendance]:
    """Parse the per-event participation modal into ``user id -> status``.

    The modal lists the whole squad grouped by response, so members who
    have not answered are included too (as ``Attendance.UNKNOWN``).
    Guest players, who have no SpielerPlus account, are skipped.
    """
    soup = BeautifulSoup(html, "html.parser")
    participation: dict[str, Attendance] = {}

    for section in soup.select(".participation-list"):
        header = section.select_one(".participation-list-header")
        target = (header.get("data-target") or "") if header is not None else ""
        code_match = _COLLAPSE_TARGET_RE.fullmatch(target)
        if code_match is None:
            raise ParseError(f"participation section has no usable collapse target: {target!r}")

        attendance = _ATTENDANCE_BY_CODE.get(code_match.group(1))
        if attendance is None:
            # An unrecognised group is more likely a new SpielerPlus
            # feature than corrupt markup. Leaving its members out means
            # they fall back to "no response given", which keeps them on
            # the player list rather than silently dropping them.
            logger.warning(
                "unknown participation group %r; its members are treated as unanswered",
                code_match.group(1),
            )
            continue

        for user_el in section.select(".participation-list-user"):
            photo = user_el.select_one("a.participation-list-user-photo[href]")
            if photo is None:
                continue  # guest player, or the "add a guest" placeholder row
            user_id = _user_id_from_href(photo["href"])
            if user_id is not None:
                participation[user_id] = attendance

    return participation


def parse_profile_fields(html: str) -> dict[str, str]:
    """Parse the label/value pairs from a member's profile page.

    Values SpielerPlus has nothing to show for are rendered as ``"-"``
    and are left out here.
    """
    soup = BeautifulSoup(html, "html.parser")
    fields: dict[str, str] = {}
    for label_el in soup.select("small.light b"):
        container = label_el.find_parent("div")
        value_el = container.select_one("p.dark") if container is not None else None
        if value_el is None:
            continue
        value = value_el.get_text(strip=True)
        if value and value != "-":
            fields[label_el.get_text(strip=True)] = value
    return fields


def parse_birthday(html: str) -> date | None:
    """Parse the birthday off a member's profile page, if one is set.

    Careful: this is the birthday of the account holder. In youth teams
    the account usually belongs to a parent, so this is often *not* the
    birthday of the child who plays.
    """
    for label in ("Geburtstag", "Birthday", "Date of birth"):
        raw = parse_profile_fields(html).get(label)
        if raw is None:
            continue
        match = _FULL_DATE_RE.search(raw)
        if match is None:
            return None
        day, month, year = (int(part) for part in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            logger.warning("profile has an invalid birthday %r", raw)
            return None
    return None
