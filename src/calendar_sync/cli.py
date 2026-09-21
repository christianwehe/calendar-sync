"""Command line entry point: ``calendar-sync <command>``."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from googleapiclient.discovery import Resource

from .config import ConfigError, ResolvedJob, Settings, load_jobs
from .google_calendar import GoogleCalendarClient, GoogleCalendarError, build_service
from .playerlist import (
    AGE_CLASSES,
    PlayerListError,
    build_rows,
    fill_player_list,
    implausible_age,
    normalize_age_class,
    select_players,
)
from .spielerplus import SpielerPlusClient, SpielerPlusError, SpielerPlusEvent
from .sync import SyncResult, SyncService, prefixed_title_builder

TEMPLATE_SOURCE_URL = "https://www.wuerttfv.de/app/uploads/2020/09/KiFu-Spielerliste-2026.pdf"

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


def _jobs_file(settings: Settings, args: argparse.Namespace) -> Path | None:
    return args.jobs_file or settings.jobs_file


def _resolve_jobs(
    settings: Settings, jobs_file: Path | None, job_name: str | None
) -> list[ResolvedJob] | None:
    """Returns ``None`` for legacy single-job mode (no jobs file configured)."""
    if jobs_file is None:
        return None

    jobs = [job.resolve(settings) for job in load_jobs(jobs_file)]
    if job_name is not None:
        jobs = [job for job in jobs if job.name == job_name]
        if not jobs:
            raise ConfigError(f"no job named {job_name!r} in {jobs_file}")
    return jobs


def _single_job(settings: Settings, args: argparse.Namespace, command: str) -> ResolvedJob | None:
    """Resolve the one job a single-profile command should act on.

    Returns ``None`` in legacy single-job mode (no jobs file), where the
    settings themselves describe the only profile.
    """
    jobs_file = _jobs_file(settings, args)
    if jobs_file is None:
        return None

    jobs = _resolve_jobs(settings, jobs_file, args.job)
    if args.job is None and len(jobs) > 1:
        raise ConfigError(
            f"{jobs_file} defines multiple jobs; pass --job NAME to pick which profile to "
            f"run {command} for"
        )
    return jobs[0]


def _google_client_for_job(
    settings: Settings, job: ResolvedJob, service_cache: dict[Path, Resource]
) -> GoogleCalendarClient:
    service = service_cache.get(job.google_token_path)
    if service is None:
        service = build_service(settings.google_credentials_path, job.google_token_path)
        service_cache[job.google_token_path] = service
    return GoogleCalendarClient(
        service, job.google_calendar_id, sync_tag=job.sync_tag, timezone=job.timezone
    )


def _print_sync_result(name: str | None, result: SyncResult) -> None:
    prefix = f"[{name}] " if name else ""
    print(
        f"{prefix}created={len(result.created)} updated={len(result.updated)} "
        f"deleted={len(result.deleted)} unchanged={len(result.unchanged)}"
    )


def cmd_sync(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    jobs = _resolve_jobs(settings, _jobs_file(settings, args), args.job)
    spielerplus = _build_spielerplus_client(settings)

    if jobs is None:
        google_calendar = _build_google_client(settings)
        result = SyncService(spielerplus, google_calendar).sync()
        _print_sync_result(None, result)
        return 0

    service_cache: dict[Path, Resource] = {}
    exit_code = 0
    for job in jobs:
        try:
            if job.spielerplus_user_id:
                spielerplus.switch_user(job.spielerplus_user_id)
            google_calendar = _google_client_for_job(settings, job, service_cache)
            title_builder = prefixed_title_builder(job.title_prefix)
            result = SyncService(spielerplus, google_calendar, title_builder=title_builder).sync()
            _print_sync_result(job.name, result)
        except (SpielerPlusError, GoogleCalendarError) as exc:
            print(f"[{job.name}] FAILED: {exc}", file=sys.stderr)
            exit_code = 1
    return exit_code


def cmd_list_events(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    job = _single_job(settings, args, "list-events")
    spielerplus = _build_spielerplus_client(settings)

    if job is not None and job.spielerplus_user_id:
        spielerplus.switch_user(job.spielerplus_user_id)

    for event in spielerplus.get_events():
        print(
            f"{event.start:%Y-%m-%d %H:%M}-{event.end:%H:%M}  "
            f"{event.title} [{event.event_type}] attendance={event.attendance.value}"
        )
    return 0


def _pick_event(events: list[SpielerPlusEvent], wanted: str | None) -> SpielerPlusEvent:
    """Find the event to build a player list for.

    ``wanted`` is an event id as printed by ``list-events``, either
    qualified (``tournament-2061203``) or bare (``2061203``). Without
    one, the next upcoming tournament is used.
    """
    if wanted is not None:
        matches = [
            event for event in events if wanted in (event.uid, event.id)
        ]
        if not matches:
            raise ConfigError(
                f"no upcoming event {wanted!r} for this profile; "
                f"run list-events to see what there is"
            )
        return matches[0]

    now = datetime.now()
    upcoming = sorted(
        (e for e in events if e.event_type == "tournament" and e.start >= now),
        key=lambda event: event.start,
    )
    if not upcoming:
        raise ConfigError(
            "no upcoming tournament for this profile; pass --event to pick another event"
        )
    return upcoming[0]


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def cmd_player_list(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    job = _single_job(settings, args, "player-list")

    club = args.club or (job.club_name if job else None) or settings.club_name
    raw_age_class = args.age_class or (job.age_class if job else None) or settings.age_class
    age_class = normalize_age_class(raw_age_class) if raw_age_class else None
    template = args.template or settings.form_template
    if not args.dry_run and not template.exists():
        raise ConfigError(
            f"player list template not found at {template}; download it from "
            f"{TEMPLATE_SOURCE_URL} or point --template at your copy"
        )

    spielerplus = _build_spielerplus_client(settings)
    if job is not None and job.spielerplus_user_id:
        spielerplus.switch_user(job.spielerplus_user_id)

    event = _pick_event(spielerplus.get_events(), args.event)
    print(f"Event: {event.title} {event.subtitle} on {event.start:%d.%m.%Y %H:%M} ({event.uid})")

    participation = spielerplus.get_participation(event)
    # One profile request per member, so say what is happening first.
    print("Fetching squad and birthdays from SpielerPlus...")
    members = spielerplus.get_members(with_birthdays=True)
    rows = build_rows(select_players(members, participation))
    if not rows:
        raise ConfigError("no players left for this event — every squad member has declined")

    event_date = event.start.date()
    for row in rows:
        age = implausible_age(row, event_date)
        if age is not None:
            _warn(
                f"{row.source_name}: birthday {row.birthday_text} makes the player {age} years "
                f"old — SpielerPlus stores one birthday per account, so this is most likely "
                f"the parent's"
            )
    missing_birthday = [row.source_name for row in rows if row.birthday is None]
    if missing_birthday:
        _warn(
            f"no birthday set in SpielerPlus for {len(missing_birthday)} player(s), "
            f"left blank: {', '.join(missing_birthday)}"
        )
    missing_last_name = [row.source_name for row in rows if not row.last_name]
    if missing_last_name:
        _warn(f"no surname could be derived for: {', '.join(missing_last_name)}")

    if args.dry_run:
        for index, row in enumerate(rows, start=1):
            print(f"{index:>3}. {row.last_name:<20} {row.first_name:<20} {row.birthday_text}")
        print(f"{len(rows)} player(s); not writing a PDF (--dry-run)")
        return 0

    output = args.output or Path(f"Spielerliste-{event.start:%Y-%m-%d}.pdf")
    fill_player_list(
        template,
        output,
        rows,
        club=club,
        list_date=args.date or event_date,
        age_class=age_class,
    )
    print(f"Wrote {len(rows)} player(s) to {output}")
    if club is None:
        _warn("no club name configured (--club / club_name in the jobs file); left blank")
    if age_class is None:
        _warn("no age class configured (--age-class / age_class in the jobs file); left unticked")
    return 0


def cmd_google_login(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    jobs_file = _jobs_file(settings, args)

    if jobs_file is None:
        _build_google_client(settings)
        print(f"Google Calendar credentials cached at {settings.google_token_path}")
        return 0

    jobs = _resolve_jobs(settings, jobs_file, args.job)
    for token_path in sorted({job.google_token_path for job in jobs}, key=str):
        build_service(settings.google_credentials_path, token_path)
        print(f"Google Calendar credentials cached at {token_path}")
    return 0


def cmd_list_jobs(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    jobs_file = _jobs_file(settings, args)

    if jobs_file is None:
        print("No jobs file configured (set CALENDAR_SYNC_JOBS_FILE or pass --jobs-file); running in single-job mode.")
        return 0

    for job in _resolve_jobs(settings, jobs_file, None):
        print(
            f"{job.name}: spielerplus_user_id={job.spielerplus_user_id!r} "
            f"google_calendar_id={job.google_calendar_id!r} sync_tag={job.sync_tag!r} "
            f"timezone={job.timezone!r} google_token_path={job.google_token_path} "
            f"title_prefix={job.title_prefix!r}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="calendar-sync", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    parser.add_argument(
        "--jobs-file",
        type=Path,
        default=None,
        help=(
            "TOML file defining multiple sync jobs, one SpielerPlus profile -> Google "
            "Calendar mapping each (see jobs.example.toml). Overrides CALENDAR_SYNC_JOBS_FILE."
        ),
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="sync SpielerPlus events into Google Calendar")
    sync_parser.add_argument("--job", help="only run this job (default: every job in the jobs file)")
    sync_parser.set_defaults(func=cmd_sync)

    list_parser = subparsers.add_parser("list-events", help="print SpielerPlus events without syncing")
    list_parser.add_argument("--job", help="list events for this job's SpielerPlus profile")
    list_parser.set_defaults(func=cmd_list_events)

    player_list_parser = subparsers.add_parser(
        "player-list",
        help="fill the association's player list PDF with a tournament's squad",
    )
    player_list_parser.add_argument("--job", help="build the list for this job's SpielerPlus profile")
    player_list_parser.add_argument(
        "--event",
        help=(
            "event to build the list for, as printed by list-events (e.g. "
            "tournament-2061203). Default: the next upcoming tournament."
        ),
    )
    player_list_parser.add_argument("--output", type=Path, help="where to write the filled PDF")
    player_list_parser.add_argument(
        "--template",
        type=Path,
        help=f"the association's blank form (default: CALENDAR_SYNC_FORM_TEMPLATE, from {TEMPLATE_SOURCE_URL})",
    )
    player_list_parser.add_argument("--club", help="club name to fill in (\"Name des Vereins\")")
    player_list_parser.add_argument(
        "--age-class", help=f"age class to tick: {', '.join(AGE_CLASSES)}"
    )
    player_list_parser.add_argument(
        "--date",
        type=lambda raw: datetime.strptime(raw, "%d.%m.%Y").date(),
        help="date for the form's signature line, DD.MM.YYYY (default: the event's date)",
    )
    player_list_parser.add_argument(
        "--dry-run", action="store_true", help="print the players instead of writing a PDF"
    )
    player_list_parser.set_defaults(func=cmd_player_list)

    login_parser = subparsers.add_parser(
        "google-login", help="run the Google OAuth flow once and cache a token for later syncs"
    )
    login_parser.add_argument(
        "--job", help="only cache a token for this job (default: every distinct token in the jobs file)"
    )
    login_parser.set_defaults(func=cmd_google_login)

    list_jobs_parser = subparsers.add_parser(
        "list-jobs", help="print the resolved jobs from the configured jobs file"
    )
    list_jobs_parser.set_defaults(func=cmd_list_jobs)

    return parser


def main(argv: list[str] | None = None) -> int:
    # find_dotenv(usecwd=True): search for .env starting from the current working
    # directory, not from wherever this installed package file happens
    # to live (python-dotenv's default). This is what makes a systemd
    # service with WorkingDirectory=<project dir> find .env correctly
    # regardless of whether the package was installed editable or not.
    load_dotenv(find_dotenv(usecwd=True))
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        return args.func(args)
    except (ConfigError, PlayerListError) as exc:
        print(f"{parser.prog}: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
