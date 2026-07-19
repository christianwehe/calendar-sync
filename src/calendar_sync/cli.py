"""Command line entry point: ``calendar-sync <command>``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
from googleapiclient.discovery import Resource

from .config import ConfigError, ResolvedJob, Settings, load_jobs
from .google_calendar import GoogleCalendarClient, GoogleCalendarError, build_service
from .spielerplus import SpielerPlusClient, SpielerPlusError
from .sync import SyncResult, SyncService, prefixed_title_builder

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
    jobs_file = _jobs_file(settings, args)
    spielerplus = _build_spielerplus_client(settings)

    if jobs_file is not None:
        jobs = _resolve_jobs(settings, jobs_file, args.job)
        if args.job is None and len(jobs) > 1:
            raise ConfigError(
                f"{jobs_file} defines multiple jobs; pass --job NAME to pick which profile to list events for"
            )
        job = jobs[0]
        if job.spielerplus_user_id:
            spielerplus.switch_user(job.spielerplus_user_id)

    for event in spielerplus.get_events():
        print(
            f"{event.start:%Y-%m-%d %H:%M}-{event.end:%H:%M}  "
            f"{event.title} [{event.event_type}] attendance={event.attendance.value}"
        )
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
    # usecwd=True: search for .env starting from the current working
    # directory, not from wherever this installed package file happens
    # to live (python-dotenv's default). This is what makes a systemd
    # service with WorkingDirectory=<project dir> find .env correctly
    # regardless of whether the package was installed editable or not.
    load_dotenv(usecwd=True)
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
