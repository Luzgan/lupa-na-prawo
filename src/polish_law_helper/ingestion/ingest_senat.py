"""Ingest Senat parliamentary data into the database."""

import asyncio
import uuid
from datetime import date, datetime, timezone

from rich.console import Console
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from polish_law_helper.config import settings
from polish_law_helper.db.engine import async_session
from polish_law_helper.db.models import SenatProcess
from polish_law_helper.ingestion.senat_client import SenatClient

console = Console()


def _parse_date(date_str: str | None) -> date | None:
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str[:10])
    except (ValueError, TypeError):
        return None


async def ingest_senat_prints(
    session: AsyncSession,
    senat_client: SenatClient,
) -> int:
    """Ingest Senat prints as SenatProcess records. Returns count of new/updated."""
    console.print("[bold]Fetching Senat prints...[/bold]")
    count = 0
    offset = 0

    while True:
        try:
            batch = await senat_client.get_prints(limit=50, offset=offset)
        except Exception as e:
            console.print(f"[red]Failed to fetch Senat prints at offset {offset}: {e}[/red]")
            break

        if not batch:
            break

        # Handle case where API returns a dict with items key instead of a list
        if isinstance(batch, dict):
            batch = batch.get("items", batch.get("prints", []))

        if not isinstance(batch, list):
            console.print("[yellow]Unexpected Senat prints response format[/yellow]")
            break

        for item in batch:
            # The Senat API may use different field names — handle gracefully
            number = str(
                item.get("number", "")
                or item.get("printNumber", "")
                or item.get("nr", "")
            )
            if not number:
                continue

            title = (
                item.get("title", "")
                or item.get("name", "")
                or f"Senat Print {number}"
            )

            # Try to extract Sejm process reference
            sejm_ref = (
                item.get("sejmProcessNumber")
                or item.get("sejmPrint")
                or item.get("processNumber")
            )
            if sejm_ref is not None:
                sejm_ref = str(sejm_ref)

            # Try to extract decision info
            decision = item.get("decision") or item.get("result")
            decision_date = _parse_date(
                item.get("decisionDate") or item.get("resultDate")
            )

            values = {
                "id": uuid.uuid4(),
                "term": settings.senat_term,
                "print_number": number,
                "title": title,
                "sejm_process_number": sejm_ref,
                "decision": decision,
                "decision_date": decision_date,
                "raw_json": item,
                "updated_at": datetime.now(timezone.utc),
            }

            stmt = pg_insert(SenatProcess).values(**values)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_senat_process",
                set_={
                    k: v
                    for k, v in values.items()
                    if k not in ("id", "term", "print_number")
                },
            )
            await session.execute(stmt)
            count += 1

            await asyncio.sleep(0.3)

        offset += len(batch)
        if len(batch) < 50:
            break  # last page

    await session.commit()
    console.print(f"[green]Ingested/updated {count} Senat processes.[/green]")
    return count


async def ingest_senat() -> None:
    """Full Senat ingestion pipeline."""
    senat_client = SenatClient()

    try:
        async with async_session() as session:
            await ingest_senat_prints(session, senat_client)
    finally:
        await senat_client.close()
