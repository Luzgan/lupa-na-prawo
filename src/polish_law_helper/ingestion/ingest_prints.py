"""Ingest Sejm parliamentary prints (druki sejmowe) into the database."""

import asyncio
import hashlib
import uuid
from datetime import date, datetime, timedelta, timezone

from rich.console import Console
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from polish_law_helper.config import settings
from polish_law_helper.db.engine import async_session
from polish_law_helper.db.models import IngestionLog, PrintChunk, SejmPrint
from polish_law_helper.embeddings.ollama_client import embedder
from polish_law_helper.ingestion.print_chunker import PrintChunkData, chunk_print_text
from polish_law_helper.ingestion.print_parser import extract_print_text
from polish_law_helper.ingestion.sejm_client import SejmClient

console = Console()


def _parse_date(date_str: str | None) -> date | None:
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str[:10])
    except (ValueError, TypeError):
        return None


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def ingest_print(
    session: AsyncSession,
    sejm_client: SejmClient,
    print_data: dict,
    force: bool = False,
) -> bool:
    """Ingest a single parliamentary print. Returns True if ingested/updated."""
    print_number = str(print_data.get("number", ""))
    if not print_number:
        return False

    title = print_data.get("title", f"Print {print_number}")
    term = settings.sejm_term

    console.print(f"  Extracting text for print {print_number}...")

    # Extract text from HTML attachment
    text = await extract_print_text(sejm_client, term, print_number)
    if not text:
        console.print(f"  [dim]No text extracted, skipping print {print_number}[/dim]")
        return False

    text_hash_val = _text_hash(text)

    # Check if already ingested with same hash
    existing = await session.execute(
        select(SejmPrint).where(
            SejmPrint.term == term,
            SejmPrint.print_number == print_number,
        )
    )
    existing_print = existing.scalar_one_or_none()

    if existing_print and existing_print.text_hash == text_hash_val and not force:
        console.print(f"  [dim]Print {print_number} already up to date, skipping.[/dim]")
        return False

    # Chunk the text
    chunks = chunk_print_text(title, text)
    console.print(f"  Parsed {len(chunks)} chunks from print {print_number}")

    if not chunks:
        console.print(f"  [yellow]No chunks created for print {print_number}[/yellow]")
        return False

    # Generate embeddings in batches
    console.print(f"  Generating embeddings...")
    all_embeddings: list[list[float]] = []
    batch_size = settings.embedding_batch_size
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        texts = [c.text_for_embedding for c in batch]
        batch_embeddings = await embedder.embed_batch(texts)
        all_embeddings.extend(batch_embeddings)

    # Determine process number from print data
    process_number = None
    process_numbers = print_data.get("processPrint", [])
    if process_numbers and isinstance(process_numbers, list):
        process_number = str(process_numbers[0])

    # Build attachment URL (first attachment if available)
    attachment_url = None
    attachments = print_data.get("attachments", [])
    if attachments:
        first_att = attachments[0] if isinstance(attachments[0], str) else attachments[0].get("name", "")
        if first_att:
            attachment_url = f"{sejm_client.base_url}/term{term}/prints/{print_number}/{first_att}"

    # Upsert SejmPrint
    if existing_print:
        existing_print.title = title
        existing_print.document_date = _parse_date(print_data.get("documentDate"))
        existing_print.process_number = process_number
        existing_print.attachment_url = attachment_url
        existing_print.text_content = text
        existing_print.text_hash = text_hash_val
        existing_print.raw_json = print_data
        existing_print.updated_at = datetime.now(timezone.utc)

        # Delete old chunks
        await session.execute(
            delete(PrintChunk).where(PrintChunk.print_id == existing_print.id)
        )
        sejm_print_id = existing_print.id
    else:
        sejm_print = SejmPrint(
            term=term,
            print_number=print_number,
            title=title,
            document_date=_parse_date(print_data.get("documentDate")),
            process_number=process_number,
            attachment_url=attachment_url,
            text_content=text,
            text_hash=text_hash_val,
            raw_json=print_data,
        )
        session.add(sejm_print)
        await session.flush()  # Get sejm_print.id
        sejm_print_id = sejm_print.id

    # Insert chunks with embeddings
    for chunk_data, embedding in zip(chunks, all_embeddings):
        chunk = PrintChunk(
            print_id=sejm_print_id,
            chunk_index=chunk_data.chunk_index,
            text_content=chunk_data.text_content,
            text_for_embedding=chunk_data.text_for_embedding,
            embedding=embedding,
            char_count=chunk_data.char_count,
        )
        session.add(chunk)

    # Log ingestion
    log = IngestionLog(
        source="sejm_print",
        identifier=f"term{term}/print/{print_number}",
        status="success",
        message=f"Ingested {len(chunks)} chunks",
        completed_at=datetime.now(timezone.utc),
    )
    session.add(log)

    await session.commit()
    console.print(
        f"  [green]Ingested {len(chunks)} chunks for print {print_number}: {title[:60]}[/green]"
    )
    return True


async def ingest_prints(
    since_days: int = 30,
    force: bool = False,
) -> None:
    """Ingest recent Sejm parliamentary prints.

    Fetches prints from the Sejm API, extracts text from HTML attachments,
    chunks the text, generates embeddings, and stores everything.
    """
    sejm_client = SejmClient()

    try:
        async with async_session() as session:
            console.print("[bold]Fetching parliamentary prints...[/bold]")

            offset = 0
            success = 0
            skipped = 0
            errors = 0
            cutoff_date = date.today() - timedelta(days=since_days)

            while True:
                try:
                    batch = await sejm_client.get_prints(limit=50, offset=offset)
                except Exception as e:
                    console.print(f"[red]Failed to fetch prints: {e}[/red]")
                    break

                if not batch:
                    break

                for print_data in batch:
                    doc_date = _parse_date(print_data.get("documentDate"))
                    if doc_date and doc_date < cutoff_date and not force:
                        continue

                    print_number = str(print_data.get("number", ""))
                    if not print_number:
                        continue

                    console.print(
                        f"\n[{success + skipped + errors + 1}] Print {print_number}: "
                        f"{print_data.get('title', '')[:70]}"
                    )

                    try:
                        if await ingest_print(session, sejm_client, print_data, force=force):
                            success += 1
                        else:
                            skipped += 1
                    except Exception as e:
                        console.print(f"  [red]Error: {e}[/red]")
                        errors += 1
                        log = IngestionLog(
                            source="sejm_print",
                            identifier=f"term{settings.sejm_term}/print/{print_number}",
                            status="error",
                            message=str(e),
                            completed_at=datetime.now(timezone.utc),
                        )
                        session.add(log)
                        await session.commit()

                    # Rate limiting
                    await asyncio.sleep(settings.request_delay)

                offset += len(batch)
                if len(batch) < 50:
                    break  # last page

            console.print(
                f"\n[bold green]Done! Ingested: {success}, "
                f"Skipped: {skipped}, Errors: {errors}[/bold green]"
            )
    finally:
        await sejm_client.close()
        await embedder.close()
