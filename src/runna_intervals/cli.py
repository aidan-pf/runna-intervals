"""CLI entry point for runna-intervals.

Usage:
    runna-intervals config               # Set up API credentials
    runna-intervals sync                 # Sync upcoming workouts from Runna ICS feed
    runna-intervals sync --dry-run       # Preview without uploading
    runna-intervals list-events --start 2024-01-01 --end 2024-01-31
"""

from datetime import date, timedelta
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from runna_intervals.config import _LOCAL_ENV, Settings
from runna_intervals.intervals_client import IntervalsAPIError, IntervalsClient

app = typer.Typer(
    name="runna-intervals",
    help="Sync your Runna training plan to Intervals.icu planned workouts.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()
err_console = Console(stderr=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]
    except Exception as exc:
        err_console.print(f"[red]Configuration error:[/red] {exc}")
        err_console.print(
            "Run [bold]runna-intervals config[/bold] to set up your Intervals.icu API key."
        )
        raise typer.Exit(1) from None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def config(
    show: Annotated[
        bool,
        typer.Option("--show", help="Show current configuration."),
    ] = False,
) -> None:
    """Set up Intervals.icu API credentials.

    Credentials are saved to [cyan].env[/cyan] in the current directory.
    """
    if show:
        if _LOCAL_ENV.exists():
            text = _LOCAL_ENV.read_text()
            console.print(Panel(text, title=str(_LOCAL_ENV), border_style="blue"))
        else:
            console.print(
                f"[yellow]No config found at {_LOCAL_ENV}. "
                "Run [bold]runna-intervals config[/bold] to create it.[/yellow]"
            )
        return

    console.print(
        Panel(
            "Enter your [bold]Intervals.icu[/bold] API credentials.\n\n"
            "Find your API key at:\n"
            "  [cyan]https://intervals.icu[/cyan] → [bold]Settings[/bold] → "
            "[bold]Developer Settings[/bold] → Generate API Key\n\n"
            "Your athlete ID appears in your profile URL:\n"
            "  [cyan]https://intervals.icu/i[bold]12345[/bold][/cyan]"
            "  ←  athlete ID is [bold]i12345[/bold]",
            title="Intervals.icu Setup",
            border_style="blue",
        )
    )

    api_key = typer.prompt("API key").strip()
    athlete_id = typer.prompt("Athlete ID").strip()
    ics_url = typer.prompt(
        "Runna ICS calendar URL (Runna app → Profile → Connected Apps & Devices → Connect Calendar → Other Calendar)"
    ).strip()
    easy_pace_raw = typer.prompt(
        "Easy pace fallback in sec/mi (used for 'conversational pace' steps — "
        "e.g. 520 = 8:40/mi, 480 = 8:00/mi)",
        default="520",
    ).strip()
    try:
        easy_pace_sec_mi = int(easy_pace_raw)
    except ValueError:
        err_console.print(
            f"[yellow]Invalid easy pace '{easy_pace_raw}', using default 520.[/yellow]"
        )
        easy_pace_sec_mi = 520

    lines = [
        f"RUNNA_INTERVALS_INTERVALS_API_KEY={api_key}\n",
        f"RUNNA_INTERVALS_INTERVALS_ATHLETE_ID={athlete_id}\n",
        f"RUNNA_INTERVALS_RUNNA_ICS_URL={ics_url}\n",
        f"RUNNA_INTERVALS_EASY_PACE_SEC_MI={easy_pace_sec_mi}\n",
    ]
    _LOCAL_ENV.write_text("".join(lines))
    console.print(f"[green]✓ Credentials saved to {_LOCAL_ENV}[/green]")


@app.command()
def sync(
    start: Annotated[
        str | None,
        typer.Option(
            "--start",
            help="Only sync workouts from this date (YYYY-MM-DD). Defaults to today.",
        ),
    ] = None,
    end: Annotated[
        str | None,
        typer.Option("--end", help="Only sync workouts up to this date (YYYY-MM-DD)."),
    ] = None,
    all_dates: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Include past workouts. By default only today onwards are synced.",
        ),
    ] = False,
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-l", help="Maximum number of workouts to process."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Preview without uploading."),
    ] = False,
    ics_url: Annotated[
        str | None,
        typer.Option("--ics-url", help="Override the Runna ICS URL for this run."),
    ] = None,
    show_desc: Annotated[
        bool,
        typer.Option(
            "--show-desc", help="Print the converted description for each workout."
        ),
    ] = False,
    use_miles: Annotated[
        bool,
        typer.Option(
            "--miles/--km", help="Format descriptions in miles (default: km)."
        ),
    ] = False,
    easy_pace: Annotated[
        int | None,
        typer.Option(
            "--easy-pace",
            help="Easy-pace fallback in sec/mi for steps with no explicit pace "
            "(e.g. 480 = 8:00/mi). Overrides RUNNA_INTERVALS_EASY_PACE_SEC_MI in .env.",
        ),
    ] = None,
) -> None:
    """Fetch your Runna calendar and sync workouts to Intervals.icu.

    Reads your Runna ICS feed, converts each workout to Intervals.icu format,
    and uploads them as planned events. Existing events are updated (upserted)
    based on the Runna workout UID, so re-running is safe.

    By default only workouts from today onwards are synced. Use [bold]--all[/bold]
    to include past workouts, or [bold]--start[/bold]/[bold]--end[/bold] for a
    specific window. Use [bold]--limit[/bold] to test with a small batch first.

    [dim]Examples:[/dim]
        runna-intervals sync                          # upcoming workouts
        runna-intervals sync --dry-run --limit 2      # preview next 2 workouts
        runna-intervals sync --start 2024-04-01 --end 2024-04-30
        runna-intervals sync --all                    # sync entire plan
        runna-intervals sync --miles                  # use miles/min-per-mile
        runna-intervals sync --easy-pace 480          # override easy pace to 8:00/mi
    """
    # Resolve the ICS URL without requiring the Intervals.icu API key yet.
    # (API key is only needed when actually uploading, not for dry-run.)
    url = ics_url
    if not url:
        try:
            url = Settings().runna_ics_url  # type: ignore[call-arg]
        except Exception:
            pass  # Settings missing — will fail below with a clear message
    if not url:
        err_console.print(
            "[red]No Runna ICS URL configured.[/red]\n"
            "Run [bold]runna-intervals config[/bold] and enter your calendar URL, "
            "or pass [bold]--ics-url <url>[/bold].\n\n"
            "Find the URL in the Runna app: Profile → Integrations → Calendar Sync."
        )
        raise typer.Exit(1)

    # Default start to today unless --all or an explicit --start was given.
    effective_start = start
    if not effective_start and not all_dates:
        effective_start = date.today().isoformat()

    from runna_intervals.runna.ics_parser import fetch_ics, parse_ics_to_events

    # Resolve easy pace: CLI flag > .env setting > module default
    _easy_pace = easy_pace
    if _easy_pace is None:
        try:
            _easy_pace = Settings().easy_pace_sec_mi  # type: ignore[call-arg]
        except Exception:
            pass  # API key missing is fine here; default baked into parse_ics_to_events

    console.print("[dim]Fetching Runna calendar…[/dim]")
    try:
        ics_text = fetch_ics(url)
    except Exception as exc:
        err_console.print(f"[red]Failed to fetch ICS feed:[/red] {exc}")
        raise typer.Exit(1) from None

    skipped: list[tuple[str, str]] = []
    events = parse_ics_to_events(
        ics_text,
        start_date=effective_start,
        end_date=end,
        use_miles=use_miles,
        easy_pace_sec_mi=_easy_pace,
        skipped=skipped,
    )
    for skipped_date, skipped_name in skipped:
        err_console.print(
            f"[yellow]⚠ Skipped {skipped_date} '{skipped_name}' — "
            "could not parse workout description.[/yellow]"
        )

    if limit is not None:
        events = events[:limit]

    if not events:
        date_range = ""
        if start or end:
            date_range = f" between {start or '…'} and {end or '…'}"
        console.print(f"[yellow]No upcoming workouts found{date_range}.[/yellow]")
        return

    table = Table(title=f"Runna → Intervals.icu ({len(events)} workout(s))")
    table.add_column("Date", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Duration", justify="right")

    for ev in events:
        mins, secs = divmod(ev.moving_time, 60)
        table.add_row(ev.start_date_local[:10], ev.name, f"{mins}m {secs:02d}s")

    console.print(table)

    if show_desc:
        for ev in events:
            console.print(
                Panel(
                    f"[dim]{ev.description}[/dim]",
                    title=f"{ev.start_date_local[:10]} — {ev.name}",
                    border_style="dim",
                )
            )

    if dry_run:
        console.print("[yellow]Dry run — not uploading.[/yellow]")
        return

    settings = _get_settings()
    with IntervalsClient(
        api_key=settings.intervals_api_key,
        athlete_id=settings.intervals_athlete_id,
        base_url=settings.intervals_base_url,
    ) as client:
        try:
            results = client.upload_events(events)
        except IntervalsAPIError as exc:
            err_console.print(f"[red]Upload failed:[/red] {exc}")
            raise typer.Exit(1) from None

    console.print(
        f"[green]✓ Synced {len(results)} workout(s) to Intervals.icu![/green]"
    )


@app.command(name="list-events")
def list_events(
    start: Annotated[str, typer.Option("--start", help="Start date (YYYY-MM-DD).")],
    end: Annotated[str, typer.Option("--end", help="End date (YYYY-MM-DD).")],
) -> None:
    """List planned workout events on Intervals.icu for a date range."""
    settings = _get_settings()
    with IntervalsClient(
        api_key=settings.intervals_api_key,
        athlete_id=settings.intervals_athlete_id,
        base_url=settings.intervals_base_url,
    ) as client:
        try:
            events = client.get_events(start, end)
        except IntervalsAPIError as exc:
            err_console.print(f"[red]Failed to fetch events:[/red] {exc}")
            raise typer.Exit(1) from None

    if not events:
        console.print(f"[yellow]No events found between {start} and {end}.[/yellow]")
        return

    table = Table(title=f"Intervals.icu Events: {start} → {end}")
    table.add_column("Date", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Type")
    table.add_column("Category")
    table.add_column("ID", style="dim")

    for ev in events:
        table.add_row(
            (ev.get("start_date_local") or "")[:10],
            ev.get("name") or "",
            ev.get("type") or "",
            ev.get("category") or "",
            str(ev.get("id") or ""),
        )

    console.print(table)


@app.command()
def delete(
    start: Annotated[
        str | None,
        typer.Option(
            "--start", help="Delete Runna events from this date (YYYY-MM-DD)."
        ),
    ] = None,
    end: Annotated[
        str | None,
        typer.Option("--end", help="Delete Runna events up to this date (YYYY-MM-DD)."),
    ] = None,
    future: Annotated[
        bool,
        typer.Option(
            "--future",
            help="Delete all Runna events from today onwards (2-year window).",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", "-n", help="Preview what would be deleted without deleting."
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the confirmation prompt."),
    ] = False,
) -> None:
    """Delete Runna workouts from your Intervals.icu calendar.

    Only events uploaded by runna-intervals are touched — they are identified by
    their [cyan]runna-[/cyan] external_id prefix. Manual workouts and activities
    recorded on your watch are never affected.

    You must supply at least one of [bold]--start[/bold], [bold]--end[/bold], or
    [bold]--future[/bold].

    [dim]Examples:[/dim]
        runna-intervals delete --start 2024-04-01 --end 2024-04-30
        runna-intervals delete --start 2024-06-01
        runna-intervals delete --future --dry-run
        runna-intervals delete --future --yes
    """
    if not future and start is None and end is None:
        err_console.print(
            "[red]Error:[/red] Specify at least one of --start, --end, or --future."
        )
        raise typer.Exit(1)

    # Resolve effective date range
    if future:
        effective_start = start or date.today().isoformat()
        effective_end = end or (date.today() + timedelta(days=730)).isoformat()
    else:
        effective_start = start or "2000-01-01"
        effective_end = end or (date.today() + timedelta(days=730)).isoformat()

    settings = _get_settings()
    with IntervalsClient(
        api_key=settings.intervals_api_key,
        athlete_id=settings.intervals_athlete_id,
        base_url=settings.intervals_base_url,
    ) as client:
        console.print(
            f"[dim]Fetching events between {effective_start} and {effective_end}…[/dim]"
        )
        try:
            all_events = client.get_events(effective_start, effective_end)
        except IntervalsAPIError as exc:
            err_console.print(f"[red]Failed to fetch events:[/red] {exc}")
            raise typer.Exit(1) from None

        # Keep only events uploaded by runna-intervals
        runna_events = [
            ev
            for ev in all_events
            if (ev.get("external_id") or "").startswith("runna-")
        ]

        if not runna_events:
            console.print(
                f"[yellow]No Runna events found between "
                f"{effective_start} and {effective_end}.[/yellow]"
            )
            return

        table = Table(title=f"Runna Events to Delete ({len(runna_events)})")
        table.add_column("Date", style="cyan", no_wrap=True)
        table.add_column("Name", style="bold")
        table.add_column("ID", style="dim")

        for ev in runna_events:
            table.add_row(
                (ev.get("start_date_local") or "")[:10],
                ev.get("name") or "",
                str(ev.get("id") or ""),
            )

        console.print(table)

        if dry_run:
            console.print("[yellow]Dry run — not deleting.[/yellow]")
            return

        if not yes:
            typer.confirm(
                f"Permanently delete {len(runna_events)} Runna event(s)?",
                abort=True,
            )

        deleted = 0
        failed = 0
        for ev in runna_events:
            event_id = ev.get("id")
            if not event_id:
                continue
            try:
                client.delete_event(int(event_id))
                deleted += 1
            except IntervalsAPIError as exc:
                date_str = (ev.get("start_date_local") or "")[:10]
                name_str = ev.get("name") or ""
                err_console.print(
                    f"[red]Failed to delete event {event_id} "
                    f"({date_str} {name_str}):[/red] {exc}"
                )
                failed += 1

    console.print(
        f"[green]✓ Deleted {deleted} Runna event(s) from Intervals.icu.[/green]"
    )
    if failed:
        err_console.print(f"[red]{failed} event(s) could not be deleted.[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
