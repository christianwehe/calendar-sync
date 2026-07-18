"""Command line entry point: ``calendar-sync <command>``."""

from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv

from .config import ConfigError, Settings
from .google_calendar import GoogleCalendarClient
from .spielerplus import SpielerPlusClient
from .sync import SyncService

logger = logging.getLogger(__name__)


def _build_spielerplus_client(settings: Settings) -> SpielerPlusClient:
    client = SpielerPlusClient()
    client.login(settings.spielerplus_email, settings.spielerplus_password)
    if settings.spielerplus_user_id:
        client.switch_user(settings.spielerplus_user_id)
    return client


def _build_google_client(settings: Settings) -> GoogleCalendarClient:
    return GoogleCalendarClient.from_credentials(
        credentials_path=settings.google_credentials_path,
        token_path=settings.google_token_path,
        calendar_id=settings.google_calendar_id,
        sync_tag=settings.sync_tag,
        timezone=settings.timezone,
    )


def cmd_sync(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    spielerplus = _build_spielerplus_client(settings)
    google_calendar = _build_google_client(settings)

    result = SyncService(spielerplus, google_calendar).sync()
    print(
        f"created={len(result.created)} updated={len(result.updated)} "
        f"deleted={len(result.deleted)} unchanged={len(result.unchanged)}"
    )
    return 0


def cmd_list_events(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    spielerplus = _build_spielerplus_client(settings)

    for event in spielerplus.get_events():
        print(
            f"{event.start:%Y-%m-%d %H:%M}-{event.end:%H:%M}  "
            f"{event.title} [{event.event_type}] attendance={event.attendance.value}"
        )
    return 0


def cmd_google_login(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    _build_google_client(settings)
    print(f"Google Calendar credentials cached at {settings.google_token_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="calendar-sync", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")

    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="sync SpielerPlus events into Google Calendar")
    sync_parser.set_defaults(func=cmd_sync)

    list_parser = subparsers.add_parser("list-events", help="print SpielerPlus events without syncing")
    list_parser.set_defaults(func=cmd_list_events)

    login_parser = subparsers.add_parser(
        "google-login", help="run the Google OAuth flow once and cache a token for later syncs"
    )
    login_parser.set_defaults(func=cmd_google_login)

    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"{parser.prog}: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
