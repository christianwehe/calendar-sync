# calendar-sync

Sync your event schedule from [SpielerPlus](https://www.spielerplus.de/) into
Google Calendar. One-way: SpielerPlus is the source of truth, Google
Calendar gets created, updated and deleted to match it.

The project is split into three independent layers:

- **`calendar_sync.spielerplus`** — a client for SpielerPlus. It has no
  public API, so this scrapes the same server-rendered pages a browser
  would (login, optional profile/team switching, the "Events" page). The
  login/parsing flow is ported from the reverse-engineered Rust reference
  at [janic0/autospieler](https://github.com/janic0/autospieler/blob/main/src/main.rs).
- **`calendar_sync.google_calendar`** — a thin wrapper around the Google
  Calendar API v3 (OAuth installed-app flow + `google-api-python-client`).
- **`calendar_sync.sync`** — the sync logic on top, decoupled from both
  clients via `typing.Protocol`, so it can be tested with in-memory fakes.

Because SpielerPlus has no public API, the HTML selectors this project
relies on may need adjusting if SpielerPlus changes their markup — see
[Caveats](#caveats--known-limitations) below.

## How matching works

Every event created in Google Calendar carries a private [extended
property](https://developers.google.com/calendar/api/guides/extended-properties)
recording a `spielerplus-sync` tag and the SpielerPlus event's stable id
(`{event_type}-{event_id}`, e.g. `training-48213`). On every run:

1. Fetch all current events from SpielerPlus.
2. List only the Google Calendar events carrying our tag (via the API's
   `privateExtendedProperty` filter) — events you created manually are
   never touched.
3. Create events present in SpielerPlus but missing in Google Calendar.
4. Update events whose title/description/start/end changed.
5. Delete previously-synced Google Calendar events that no longer exist
   in SpielerPlus.

## Installation

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Configuration

Copy `.env.example` to `.env` and fill it in:

```bash
cp .env.example .env
```

| Variable | Required | Description |
| --- | --- | --- |
| `SPIELERPLUS_EMAIL` | yes | SpielerPlus login email |
| `SPIELERPLUS_PASSWORD` | yes | SpielerPlus login password |
| `SPIELERPLUS_USER_ID` | no | Profile/team id to switch to after login — only needed for shared logins that manage several profiles (see below) |
| `GOOGLE_CALENDAR_ID` | no (default `primary`) | Target Google Calendar id |
| `GOOGLE_CREDENTIALS_PATH` | no (default `google_credentials.json`) | OAuth client secret file downloaded from Google Cloud Console |
| `GOOGLE_TOKEN_PATH` | no (default `google_token.json`) | Where the OAuth refresh token is cached after the first login |
| `CALENDAR_SYNC_TIMEZONE` | no (default `Europe/Berlin`) | IANA timezone used for created events |
| `CALENDAR_SYNC_TAG` | no (default `spielerplus-sync`) | Tag used to identify events this tool manages |

### Setting up Google Calendar API access

1. In [Google Cloud Console](https://console.cloud.google.com/), create a
   project and enable the **Google Calendar API**.
2. Create an **OAuth client ID** of type "Desktop app" and download the
   JSON as `google_credentials.json` (path configurable via
   `GOOGLE_CREDENTIALS_PATH`).
3. Run `calendar-sync google-login` once — it opens a browser for
   consent and caches a refresh token at `GOOGLE_TOKEN_PATH`. Subsequent
   `sync` runs (including on a headless server) reuse that token.

### Multiple SpielerPlus profiles

Some SpielerPlus logins manage several profiles under one account (e.g.
a shared family login covering multiple children/teams — SpielerPlus
calls this "switching users"). If that applies to you, set
`SPIELERPLUS_USER_ID` to the profile to sync. To sync several profiles,
run `calendar-sync sync` once per profile with different `.env` files
(and, ideally, different `GOOGLE_CALENDAR_ID` / `CALENDAR_SYNC_TAG`
values so their events don't collide).

## Usage

```bash
# One-time: cache a Google OAuth token
calendar-sync google-login

# Print what SpielerPlus currently has, without touching Google Calendar
calendar-sync list-events

# Sync SpielerPlus -> Google Calendar
calendar-sync sync
```

Run on a schedule (cron, systemd timer, CI) to keep the calendar in sync
periodically. `calendar-sync sync` is idempotent — running it repeatedly
without changes on SpielerPlus is a no-op.

## Development

```bash
pip install -e ".[dev]"
pytest                                        # run the test suite
pytest --cov=calendar_sync --cov-report=term-missing  # with coverage
```

Tests are fully offline:

- `calendar_sync.spielerplus.parser` is pure HTML-in/data-out and is
  tested against static fixtures in `tests/fixtures/`.
- `calendar_sync.spielerplus.client` is tested with the
  [`responses`](https://github.com/getsentry/responses) library mocking
  `spielerplus.de` HTTP calls.
- `calendar_sync.google_calendar.client` is tested against a mocked
  `googleapiclient` discovery `Resource`, asserting the request bodies
  and filters sent to the Calendar API without any real network calls.
- `calendar_sync.sync.service` is tested against in-memory fakes of both
  clients (see `tests/unit/test_sync_service.py`), independent of both
  SpielerPlus and Google.

## Project layout

```
src/calendar_sync/
  spielerplus/       # SpielerPlus client (HTTP session, HTML parser, models)
  google_calendar/    # Google Calendar API client (auth, CRUD, models)
  sync/                # SyncService tying the two together
  config.py            # Settings loaded from environment / .env
  cli.py                # `calendar-sync` command line entry point
tests/
  fixtures/             # Static SpielerPlus HTML samples used by parser tests
  unit/
```

## Caveats & known limitations

- **Reverse-engineered HTML scraping.** SpielerPlus exposes no public
  API. The CSS selectors and login flow in
  `calendar_sync.spielerplus.parser`/`client` were ported from the Rust
  reference implementation and adapted, then validated end-to-end
  against a live SpielerPlus account (login, team list, and event
  parsing for `training`/`tournament` events all confirmed working). If
  SpielerPlus changes their markup, parsing will raise `ParseError` —
  check `parser.py` first when that happens.
- **Cloudflare in front of SpielerPlus.** `spielerplus.de` returns a
  bare `403` for requests carrying the default `python-requests`
  User-Agent, before login is even attempted. `SpielerPlusClient` sends
  a browser-like User-Agent by default to get past this (see
  `_DEFAULT_HEADERS` in `client.py`); this is unrelated to your
  credentials.
- **Event date resolution.** SpielerPlus displays event dates without a
  year. The year is inferred by assuming the event list only contains
  upcoming events (`resolve_event_date` in `parser.py`); see its
  docstring for the exact heuristic.
- **Estimated end times.** If SpielerPlus doesn't publish an end time,
  it's estimated as `start + 2h` and the event is flagged
  `end_is_estimated=True` (surfaced in the synced event's description).
- **One-way sync only.** Changes made directly in Google Calendar are
  never written back to SpielerPlus, and attendance/participation is not
  synced in either direction (though `SpielerPlusClient.set_attendance`
  exists and is unit-tested, in case you want to build on it).
