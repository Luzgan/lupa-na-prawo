"""Ingest Sejm parliamentary data into the database."""

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone

from rich.console import Console
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from polish_law_helper.config import settings
from polish_law_helper.db.engine import async_session
from polish_law_helper.db.models import LegislativeProcess, Voting
from polish_law_helper.ingestion.sejm_client import SejmClient

console = Console()


def _compute_result(voting: dict) -> str | None:
    """Derive voting result from yes/no counts and majority threshold."""
    kind = voting.get("kind", "")
    if kind == "ON_LIST":
        return None  # List-based voting (e.g. choosing candidates), no simple pass/fail

    yes = voting.get("yes", 0) or 0
    majority = voting.get("majorityVotes", 0) or 0

    if majority <= 0:
        return None

    return "przyjęto" if yes >= majority else "odrzucono"


def _parse_date(date_str: str | None) -> date | None:
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str[:10])
    except (ValueError, TypeError):
        return None


async def ingest_processes(
    session: AsyncSession,
    sejm_client: SejmClient,
) -> int:
    """Ingest all legislative processes. Returns count of new/updated."""
    console.print("[bold]Fetching legislative processes...[/bold]")
    count = 0
    offset = 0
    errors = 0

    while True:
        batch = await sejm_client.get_processes(limit=50, offset=offset)
        if not batch:
            break

        for proc in batch:
            number = str(proc.get("number", ""))
            if not number:
                continue

            try:
                details = await sejm_client.get_process(number)
            except Exception as e:
                errors += 1
                continue

            values = {
                "id": uuid.uuid4(),
                "term": settings.sejm_term,
                "process_number": number,
                "title": details.get("title", f"Process {number}"),
                "document_type": details.get("documentType"),
                "document_date": _parse_date(details.get("documentDate")),
                "process_start": _parse_date(details.get("processStartDate")),
                "closure_date": _parse_date(details.get("changeDate")),
                "urgency_status": details.get("urgencyStatus"),
                "raw_json": details,
                "change_date": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }

            stmt = pg_insert(LegislativeProcess).values(**values)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_process_term_number",
                set_={k: v for k, v in values.items() if k not in ("id", "term", "process_number")},
            )
            await session.execute(stmt)
            count += 1

            if count % 50 == 0:
                console.print(f"  Fetched {count} processes...")

            await asyncio.sleep(0.3)

        offset += len(batch)
        await session.commit()
        if len(batch) < 50:
            break

    await session.commit()
    console.print(f"[green]Ingested/updated {count} processes.[/green]")
    return count


async def ingest_votings(
    session: AsyncSession,
    sejm_client: SejmClient,
    since_days: int = 30,
) -> int:
    """Ingest recent votings. Returns count of new/updated.

    The Sejm API lists sittings at /votings, then individual votings
    are fetched per sitting at /votings/{sitting}.
    """
    date_from = (date.today() - timedelta(days=since_days)).isoformat()
    console.print(f"[bold]Fetching votings since {date_from}...[/bold]")

    count = 0

    # Step 1: Get all sitting summaries (no dateFrom — API blocks filtered requests)
    try:
        sittings = await sejm_client.get_votings(limit=500)
    except Exception as e:
        console.print(f"[red]Failed to fetch sittings list: {e}[/red]")
        return 0

    if not isinstance(sittings, list):
        console.print(f"[yellow]Unexpected sittings response format[/yellow]")
        return 0

    # Filter client-side by date and extract unique sitting numbers
    sitting_nums = sorted({
        s.get("proceeding")
        for s in sittings
        if s.get("proceeding") and s.get("date", "") >= date_from
    })
    console.print(f"  Found {len(sitting_nums)} sittings with votings")

    # Step 2: Fetch individual votings per sitting
    for sitting_num in sitting_nums:
        try:
            votings = await sejm_client.get_votings(sitting=sitting_num)
        except Exception as e:
            console.print(f"  [yellow]Failed to fetch votings for sitting {sitting_num}: {e}[/yellow]")
            continue

        if not isinstance(votings, list):
            continue

        for voting in votings:
            sitting = voting.get("sitting")
            voting_num = voting.get("votingNumber") or voting.get("number")
            if not sitting or not voting_num:
                continue

            values = {
                "id": uuid.uuid4(),
                "term": settings.sejm_term,
                "sitting": int(sitting),
                "voting_number": int(voting_num),
                "title": voting.get("title", voting.get("topic", f"Voting {voting_num}")),
                "date": _parse_date(voting.get("date", date.today().isoformat())),
                "result": _compute_result(voting),
                "yes_count": voting.get("yes"),
                "no_count": voting.get("no"),
                "abstain_count": voting.get("abstain"),
                "raw_json": voting,
            }

            stmt = pg_insert(Voting).values(**values)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_voting",
                set_={k: v for k, v in values.items() if k not in ("id", "term", "sitting", "voting_number")},
            )
            await session.execute(stmt)
            count += 1

        await session.commit()
        console.print(f"  Sitting {sitting_num}: {len(votings)} votings")
        await asyncio.sleep(0.3)

    console.print(f"[green]Ingested/updated {count} votings.[/green]")
    return count


async def ingest_sejm(since_days: int = 30) -> None:
    """Full Sejm ingestion pipeline."""
    sejm_client = SejmClient()

    try:
        async with async_session() as session:
            await ingest_processes(session, sejm_client)  # fetches all pages
            await ingest_votings(session, sejm_client, since_days=since_days)
    finally:
        await sejm_client.close()
