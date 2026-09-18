"""Session-based client for SpielerPlus (spielerplus.de).

SpielerPlus does not offer a public API. This client drives the same
server-rendered web flow a browser would: log in with a CSRF-protected
form, optionally switch between profiles/teams sharing one login, and
scrape the "Events" page. See ``parser.py`` for the HTML parsing itself.
"""

from __future__ import annotations

import logging

import requests

from . import parser
from .exceptions import AuthenticationError, ParseError, SpielerPlusError
from .models import Attendance, SpielerPlusEvent, Team

logger = logging.getLogger(__name__)

BASE_URL = "https://www.spielerplus.de"
EVENTS_URL = f"{BASE_URL}/events"
MORE_EVENTS_URL = f"{BASE_URL}/events/ajaxgetevents"
LOGIN_URL = f"{BASE_URL}/site/login"
SWITCH_USER_URL = f"{BASE_URL}/site/switch-user"
PARTICIPATION_URL = f"{BASE_URL}/events/ajax-participation-form"

_PARTICIPATION_VALUE = {
    Attendance.ACCEPTED: "1",
    Attendance.UNSURE: "2",
    Attendance.DECLINED: "0",
}

# Upper bound on "load more" requests per get_events() call, as a guard
# against looping forever should the endpoint ever stop reporting an
# empty batch. SpielerPlus serves 5 events per batch.
_MAX_EVENT_BATCHES = 100

# spielerplus.de sits behind Cloudflare, which outright blocks requests
# carrying the default `python-requests/x.y` User-Agent (fingerprinted as
# a bot) with a 403 before any of our code runs. A realistic browser
# User-Agent is enough to pass; sent unconditionally since there is no
# legitimate reason to prefer identifying as a script here.
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}


class SpielerPlusClient:
    """A logged-in session against spielerplus.de.

    Not thread-safe; each instance owns one ``requests.Session`` (and
    therefore one authenticated identity / active profile).
    """

    def __init__(self, session: requests.Session | None = None, timeout: float = 30.0):
        self._session = session or requests.Session()
        # requests.Session() pre-populates headers with its own defaults
        # (notably a "python-requests/x.y" User-Agent), so these must be
        # applied with update(), not setdefault().
        self._session.headers.update(_DEFAULT_HEADERS)
        self._timeout = timeout
        self._logged_in = False
        self.teams: list[Team] = []

    def login(self, email: str, password: str) -> None:
        """Authenticate the session. Safe to call once per client instance."""
        response = self._get(EVENTS_URL)
        if not parser.requires_login(response.text):
            self._logged_in = True
            return

        csrf_token = parser.extract_csrf_token(response.text)
        response = self._post(
            LOGIN_URL,
            data={
                "_csrf": csrf_token,
                "LoginForm[email]": email,
                "LoginForm[password]": password,
            },
        )

        if parser.requires_login(response.text):
            raise AuthenticationError("login rejected: still on the login page")
        if parser.is_team_selection_page(response.text):
            self.teams = parser.parse_teams(response.text)
        elif not parser.is_events_page(response.text):
            raise SpielerPlusError(
                f"unexpected page after login: {parser.page_title(response.text)!r}"
            )

        self._logged_in = True

    def switch_user(self, user_id: str) -> None:
        """Switch the active profile/team for logins that manage several
        (e.g. a shared family login with one SpielerPlus "user" per
        child/team). No-op for accounts with a single profile."""
        self._require_login()
        self._get(SWITCH_USER_URL, params={"id": user_id})

    def get_events(self) -> list[SpielerPlusEvent]:
        """Fetch and parse all upcoming events of the current user.

        The "Events" page itself only renders the next few events; the
        rest are loaded in batches via the same AJAX endpoint behind the
        page's "Mehr Termine laden" button, until an empty batch
        signals the end of the list.
        """
        self._require_login()
        response = self._get(EVENTS_URL)
        if not parser.is_events_page(response.text):
            raise ParseError(
                f"expected the events page, got {parser.page_title(response.text)!r}"
            )
        events = parser.parse_events(response.text)

        offset = len(events)
        for _ in range(_MAX_EVENT_BATCHES):
            batch, count = self._get_more_events(offset)
            if count == 0:
                break
            events.extend(batch)
            offset += count
        else:
            logger.warning(
                "stopped loading events after %d batches; the list may be incomplete",
                _MAX_EVENT_BATCHES,
            )

        # Batches are offset-based, so an event added or removed between
        # requests can shift one into two batches.
        unique = {event.uid: event for event in events}
        return list(unique.values())

    def _get_more_events(self, offset: int) -> tuple[list[SpielerPlusEvent], int]:
        """Fetch the batch of events following the first ``offset`` ones.

        Returns the parsed events and the server-reported batch size,
        which is what the next offset has to advance by.
        """
        response = self._post(MORE_EVENTS_URL, data={"offset": offset})
        try:
            payload = response.json()
            html, count = payload["html"], int(payload["count"])
        except (ValueError, KeyError, TypeError) as exc:
            raise ParseError(f"unexpected response when loading more events: {exc}") from exc
        return parser.parse_events(html), count

    def set_attendance(
        self,
        event: SpielerPlusEvent,
        attendance: Attendance,
        user_id: str,
        reason: str = "-",
    ) -> None:
        """Submit an attendance response for ``event``.

        ``user_id`` identifies the profile the response is submitted for,
        matching the argument accepted by :meth:`switch_user`.
        """
        self._require_login()
        if attendance not in _PARTICIPATION_VALUE:
            raise ValueError(f"cannot submit attendance {attendance!r}")

        response = self._post(
            PARTICIPATION_URL,
            data={
                "Participation[participation]": _PARTICIPATION_VALUE[attendance],
                "Participation[reason]": reason,
                "Participation[type]": event.event_type,
                "Participation[typeid]": event.id,
                "Participation[user_id]": user_id,
            },
        )
        if response.status_code != 200:
            raise SpielerPlusError(
                f"attendance update failed with status {response.status_code}"
            )

    def _require_login(self) -> None:
        if not self._logged_in:
            raise AuthenticationError("call login() before using the client")

    def _get(self, url: str, **kwargs) -> requests.Response:
        logger.debug("GET %s", url)
        response = self._session.get(url, timeout=self._timeout, **kwargs)
        response.raise_for_status()
        return response

    def _post(self, url: str, **kwargs) -> requests.Response:
        logger.debug("POST %s", url)
        response = self._session.post(url, timeout=self._timeout, **kwargs)
        response.raise_for_status()
        return response
