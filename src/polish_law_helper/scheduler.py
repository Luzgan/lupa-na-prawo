"""Nightly sync scheduler — keeps the database up to date."""

import asyncio
from datetime import date, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from rich.console import Console

from polish_law_helper.config import settings

_console = Console()

scheduler = AsyncIOScheduler()


async def nightly_sync_acts() -> None:
    """Fetch acts changed since yesterday and ingest them."""
    try:
        from polish_law_helper.db.engine import async_session
        from polish_law_helper.embeddings.ollama_client import embedder
        from polish_law_helper.ingestion.eli_client import ELIClient
        from polish_law_helper.ingestion.ingest_acts import ingest_act

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        _console.print(f"[bold blue]Nightly sync: checking for act changes since {yesterday}...[/bold blue]")

        eli_client = ELIClient()
        try:
            changes = await eli_client.get_changes_since(yesterday)
            _console.print(f"  Found {len(changes)} changed act(s).")

            if not changes:
                return

            async with async_session() as session:
                ingested = 0
                for item in changes:
                    # Extract ELI ID from change metadata
                    eli_id = item.get("ELI") or item.get("eli_id")
                    if not eli_id:
                        # Try to build from publisher/year/position fields
                        publisher = item.get("publisher")
                        year = item.get("year")
                        position = item.get("position") or item.get("pos")
                        if publisher and year and position:
                            eli_id = f"{publisher}/{year}/{position}"
                    if not eli_id:
                        continue

                    try:
                        _console.print(f"  Syncing {eli_id}...")
                        if await ingest_act(session, eli_client, eli_id):
                            ingested += 1
                    except Exception as e:
                        _console.print(f"  [red]Failed to ingest {eli_id}: {e}[/red]")

                    await asyncio.sleep(settings.request_delay)

            _console.print(f"[bold green]Nightly act sync complete: {ingested} act(s) ingested/updated.[/bold green]")

            # After ingesting new acts, try to link unlinked processes
            if ingested > 0:
                await _run_linker()
        finally:
            await eli_client.close()
            await embedder.close()

    except Exception as exc:
        _console.print(f"[bold red]Nightly act sync failed: {exc}[/bold red]")


async def nightly_sync_sejm() -> None:
    """Refresh Sejm data for the past week."""
    try:
        from polish_law_helper.ingestion.ingest_sejm import ingest_sejm

        _console.print("[bold blue]Nightly sync: refreshing Sejm data (last 7 days)...[/bold blue]")
        await ingest_sejm(since_days=7)
        _console.print("[bold green]Nightly Sejm sync complete.[/bold green]")
    except Exception as exc:
        _console.print(f"[bold red]Nightly Sejm sync failed: {exc}[/bold red]")


async def _run_linker() -> None:
    """Run all linkers: process→act and voting→process."""
    try:
        from polish_law_helper.ingestion.linker import (
            link_processes_to_acts,
            link_votings_to_processes,
        )

        _console.print("[bold blue]Running process → act linker...[/bold blue]")
        linked_acts = await link_processes_to_acts()
        _console.print(f"  Linked {linked_acts} process(es) to acts.")

        _console.print("[bold blue]Running voting → process linker...[/bold blue]")
        linked_votings = await link_votings_to_processes()
        _console.print(f"  Linked {linked_votings} voting(s) to processes.")
    except Exception as exc:
        _console.print(f"[bold red]Linker failed: {exc}[/bold red]")


async def nightly_sync_senat() -> None:
    """Refresh Senat data."""
    try:
        from polish_law_helper.ingestion.ingest_senat import ingest_senat

        _console.print("[bold blue]Nightly sync: refreshing Senat data...[/bold blue]")
        await ingest_senat()
        _console.print("[bold green]Nightly Senat sync complete.[/bold green]")
    except Exception as exc:
        _console.print(f"[bold red]Nightly Senat sync failed: {exc}[/bold red]")


def setup_scheduler() -> AsyncIOScheduler:
    """Configure and return the scheduler with nightly jobs."""
    trigger = CronTrigger(hour=settings.cron_hour)

    scheduler.add_job(nightly_sync_acts, trigger, id="nightly_sync_acts", replace_existing=True)
    scheduler.add_job(nightly_sync_sejm, trigger, id="nightly_sync_sejm", replace_existing=True)
    scheduler.add_job(nightly_sync_senat, trigger, id="nightly_sync_senat", replace_existing=True)

    return scheduler


async def run_nightly_sync() -> None:
    """Run all sync jobs immediately (for CLI / manual use)."""
    _console.print("[bold]Running manual nightly sync...[/bold]\n")
    await nightly_sync_acts()
    await nightly_sync_sejm()
    await nightly_sync_senat()
    await _run_linker()
    _console.print("\n[bold green]Manual sync finished.[/bold green]")
