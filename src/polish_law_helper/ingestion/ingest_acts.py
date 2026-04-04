"""Ingest legal acts from ELI API into the database."""

import asyncio
import uuid
from datetime import datetime, timezone

from rich.console import Console
from rich.progress import Progress
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from polish_law_helper.config import settings
from polish_law_helper.db.engine import async_session
from polish_law_helper.db.models import Act, Chunk, IngestionLog
from polish_law_helper.embeddings.ollama_client import embedder
from polish_law_helper.ingestion.chunker import ChunkData, chunk_act, chunk_plain_text
from polish_law_helper.ingestion.eli_client import ELIClient
from polish_law_helper.ingestion.html_parser import extract_plain_text, parse_act_html

console = Console()


async def ingest_act(
    session: AsyncSession,
    eli_client: ELIClient,
    eli_id: str,
    force: bool = False,
) -> bool:
    """Ingest a single act. Returns True if act was ingested/updated."""
    publisher, year, position = ELIClient.parse_eli_id(eli_id)

    console.print(f"  Fetching metadata for {eli_id}...")
    try:
        metadata = await eli_client.get_act_metadata(publisher, year, position)
    except Exception as e:
        console.print(f"  [red]Failed to fetch metadata: {e}[/red]")
        return False

    title = metadata.get("title", f"Act {eli_id}")

    # --- Try to get content: HTML first, then PDF fallback ---
    html: str | None = None
    raw_text: str | None = None
    content_hash: str | None = None
    chunks: list[ChunkData] = []
    source_method = "unknown"

    # Step 1: Try HTML
    console.print(f"  Fetching HTML for: {title[:80]}...")
    try:
        html = await eli_client.get_act_html(publisher, year, position)
    except Exception:
        html = None

    if html:
        content_hash = ELIClient.html_hash(html)

        # Check if already ingested with same hash
        existing = await session.execute(select(Act).where(Act.eli_id == eli_id))
        existing_act = existing.scalar_one_or_none()

        if existing_act and existing_act.raw_html_hash == content_hash and not force:
            console.print(f"  [dim]Already up to date, skipping.[/dim]")
            return False

        # Try structured parsing
        console.print(f"  Parsing HTML (structured mode)...")
        units = parse_act_html(html)
        if units:
            chunks = chunk_act(title, units)
            source_method = "structured-html"

        # Fallback: extract plain text from HTML
        if not chunks:
            console.print(f"  [yellow]Structured parsing returned 0 units, trying plain text extraction from HTML...[/yellow]")
            raw_text = extract_plain_text(html)
            if raw_text and len(raw_text) > 100:
                chunks = chunk_plain_text(title, raw_text)
                source_method = "fallback-html"
    else:
        console.print(f"  [yellow]HTML unavailable, trying PDF...[/yellow]")

    # Step 2: If no HTML or no chunks from HTML, try PDF
    if not chunks:
        console.print(f"  Fetching and extracting text from PDF...")
        pdf_text = await eli_client.get_act_pdf_text(publisher, year, position)
        if pdf_text and len(pdf_text) > 100:
            content_hash = ELIClient.html_hash(pdf_text)

            # Check if already ingested with same hash
            if not html:
                existing = await session.execute(select(Act).where(Act.eli_id == eli_id))
                existing_act = existing.scalar_one_or_none()

                if existing_act and existing_act.raw_html_hash == content_hash and not force:
                    console.print(f"  [dim]Already up to date (PDF), skipping.[/dim]")
                    return False

            chunks = chunk_plain_text(title, pdf_text)
            source_method = "pdf"
        else:
            console.print(f"  [red]PDF unavailable or empty[/red]")

    # Step 3: If neither worked, skip
    if not chunks:
        console.print(f"  [yellow]Failed to extract content (neither HTML nor PDF)[/yellow]")
        return False

    console.print(f"  Extracted {len(chunks)} chunks (method: {source_method})")

    # Ensure existing_act is resolved for the upsert below
    if "existing_act" not in locals():
        existing = await session.execute(select(Act).where(Act.eli_id == eli_id))
        existing_act = existing.scalar_one_or_none()

    # Generate embeddings in batches
    console.print(f"  Generating embeddings...")
    all_embeddings = []
    batch_size = settings.embedding_batch_size
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        texts = [c.text_for_embedding for c in batch]
        batch_embeddings = await embedder.embed_batch(texts)
        all_embeddings.extend(batch_embeddings)

    # Upsert act
    if existing_act:
        act = existing_act
        act.title = title
        act.raw_html_hash = content_hash
        act.fetched_at = datetime.now(timezone.utc)
        act.updated_at = datetime.now(timezone.utc)
        # Delete old chunks
        await session.execute(delete(Chunk).where(Chunk.act_id == act.id))
    else:
        act = Act(
            eli_id=eli_id,
            eli_address=metadata.get("ELI"),
            title=title,
            act_type=metadata.get("type"),
            status=metadata.get("status"),
            in_force=metadata.get("inForce"),
            announcement_date=_parse_date(metadata.get("announcementDate")),
            entry_into_force=_parse_date(metadata.get("entryIntoForce")),
            publisher=publisher,
            year=int(year),
            position=int(position),
            keywords=metadata.get("keywords", []),
            raw_html_hash=content_hash,
            fetched_at=datetime.now(timezone.utc),
        )
        session.add(act)
        await session.flush()  # Get act.id

    # Insert chunks
    for chunk_data, embedding in zip(chunks, all_embeddings):
        chunk = Chunk(
            act_id=act.id,
            part_num=chunk_data.part_num,
            part_title=chunk_data.part_title,
            title_num=chunk_data.title_num,
            title_name=chunk_data.title_name,
            section_num=chunk_data.section_num,
            section_title=chunk_data.section_title,
            chapter_num=chunk_data.chapter_num,
            chapter_title=chunk_data.chapter_title,
            article_num=chunk_data.article_num,
            paragraph_num=chunk_data.paragraph_num,
            point_num=chunk_data.point_num,
            text_content=chunk_data.text_content,
            text_for_embedding=chunk_data.text_for_embedding,
            embedding=embedding,
            char_count=chunk_data.char_count,
        )
        session.add(chunk)

    # Log ingestion
    log = IngestionLog(
        source="eli",
        identifier=eli_id,
        status="success",
        message=f"Ingested {len(chunks)} chunks via {source_method}",
        completed_at=datetime.now(timezone.utc),
    )
    session.add(log)

    await session.commit()
    console.print(f"  [green]Ingested {len(chunks)} chunks for {title[:60]} (method: {source_method})[/green]")
    return True


def _parse_date(date_str: str | None):
    """Parse date string from ELI API."""
    if not date_str:
        return None
    try:
        from datetime import date

        return date.fromisoformat(date_str[:10])
    except (ValueError, TypeError):
        return None


async def ingest_acts(
    eli_ids: list[str] | None = None,
    force: bool = False,
) -> None:
    """Ingest multiple acts."""
    from polish_law_helper.ingestion.eli_client import PRIORITY_ACTS

    if eli_ids is None:
        eli_ids = PRIORITY_ACTS

    eli_client = ELIClient()

    try:
        async with async_session() as session:
            console.print(f"[bold]Ingesting {len(eli_ids)} acts...[/bold]")
            success = 0
            for i, eli_id in enumerate(eli_ids, 1):
                console.print(f"\n[{i}/{len(eli_ids)}] {eli_id}")
                try:
                    if await ingest_act(session, eli_client, eli_id, force=force):
                        success += 1
                except Exception as e:
                    console.print(f"  [red]Error: {e}[/red]")
                    await session.rollback()
                    try:
                        log = IngestionLog(
                            source="eli",
                            identifier=eli_id,
                            status="error",
                            message=str(e)[:500],
                            completed_at=datetime.now(timezone.utc),
                        )
                        session.add(log)
                        await session.commit()
                    except Exception:
                        await session.rollback()

                # Rate limiting
                if i < len(eli_ids):
                    await asyncio.sleep(settings.request_delay)

            console.print(f"\n[bold green]Done! {success}/{len(eli_ids)} acts ingested.[/bold green]")
    finally:
        await eli_client.close()
        await embedder.close()


async def ingest_all_laws(
    force: bool = False,
    publisher: str = "DU",
    in_force_only: bool = True,
) -> None:
    """Ingest all Polish laws with HTML text from ELI API.

    Paginates through the full ELI search index, skipping acts without HTML
    and acts already up-to-date. Expect 8-15k HTML-available acts.
    This will take several hours on CPU.
    """
    eli_client = ELIClient()

    try:
        # Get total count first
        first = await eli_client.search_acts(
            in_force=in_force_only, publisher=publisher, limit=1, offset=0
        )
        total = first.get("totalCount", "?")
        console.print(
            f"[bold]Full ingestion: {total} total acts in {publisher} "
            f"({'in-force only' if in_force_only else 'all statuses'}). "
            f"Only HTML-available acts will be indexed.[/bold]\n"
        )

        offset = 0
        page_size = 500
        ingested = 0
        skipped = 0
        no_html = 0
        errors = 0
        page_num = 0

        async with async_session() as session:
            while True:
                page_num += 1
                page = await eli_client.search_acts(
                    in_force=in_force_only,
                    publisher=publisher,
                    limit=page_size,
                    offset=offset,
                )
                items = page.get("items", [])
                if not items:
                    break

                console.print(
                    f"[bold]Page {page_num} — offset {offset}/{total} "
                    f"({ingested} ingested, {skipped} skipped, {errors} errors so far)[/bold]"
                )

                for item in items:
                    eli_id = item.get("ELI")
                    if not eli_id:
                        continue

                    if not item.get("textHTML"):
                        no_html += 1
                        continue

                    console.print(f"  {eli_id} — {item.get('title', '')[:70]}")
                    try:
                        did_ingest = await ingest_act(session, eli_client, eli_id, force=force)
                        if did_ingest:
                            ingested += 1
                        else:
                            skipped += 1
                    except Exception as e:
                        console.print(f"  [red]Error: {e}[/red]")
                        errors += 1
                        log = IngestionLog(
                            source="eli",
                            identifier=eli_id,
                            status="error",
                            message=str(e),
                            completed_at=datetime.now(timezone.utc),
                        )
                        session.add(log)
                        await session.commit()

                    await asyncio.sleep(settings.request_delay)

                offset += len(items)
                if len(items) < page_size:
                    break

                await asyncio.sleep(settings.request_delay)

        console.print(
            f"\n[bold green]Done! Ingested: {ingested}, "
            f"Skipped (up-to-date): {skipped}, "
            f"No HTML: {no_html}, Errors: {errors}[/bold green]"
        )
    finally:
        await eli_client.close()
        await embedder.close()
