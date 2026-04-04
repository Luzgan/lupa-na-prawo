"""Data integrity checker — verifies correctness and completeness of database contents."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select, text

from polish_law_helper.config import settings
from polish_law_helper.db.engine import async_session
from polish_law_helper.db.models import (
    Act,
    Chunk,
    IngestionLog,
    LegislativeProcess,
    PrintChunk,
    SejmPrint,
    Voting,
)
from polish_law_helper.ingestion.eli_client import PRIORITY_ACTS


@dataclass
class IntegrityReport:
    """Result of a data integrity check."""

    checks_passed: int = 0
    checks_failed: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.checks_failed == 0


async def run_integrity_check(fix: bool = False) -> IntegrityReport:
    """Run a data integrity check.

    If fix=True, attempts to repair found issues
    (re-fetch missing data, fill in missing embeddings, etc.).
    If fix=False, only reports problems.
    """
    report = IntegrityReport()
    console = Console()

    async with async_session() as session:
        # ------------------------------------------------------------------
        # 1. Priority legal acts
        # ------------------------------------------------------------------
        try:
            console.print("[bold]1/8 Checking priority legal acts...[/]")
            existing = await session.execute(
                select(Act.eli_id).where(Act.eli_id.in_(PRIORITY_ACTS))
            )
            existing_set = {r[0] for r in existing.all()}
            missing = [eid for eid in PRIORITY_ACTS if eid not in existing_set]

            if missing:
                report.checks_failed += 1
                report.errors.append(
                    f"Missing {len(missing)} priority act(s): {', '.join(missing)}"
                )
                if fix:
                    from polish_law_helper.ingestion.ingest_acts import ingest_acts

                    console.print(
                        f"  [bold yellow]Fetching {len(missing)} missing act(s) — "
                        f"this may take a few minutes...[/]"
                    )
                    try:
                        await ingest_acts(eli_ids=missing, force=False)
                        report.actions_taken.append(
                            f"Fetched {len(missing)} missing act(s)"
                        )
                    except Exception as e:
                        console.print(f"  [red]Failed to fetch acts: {e}[/]")
                        report.actions_taken.append(
                            f"Failed to fetch missing acts: {e}"
                        )
            else:
                report.checks_passed += 1
                console.print(
                    f"  [green]OK All {len(PRIORITY_ACTS)} priority acts present[/]"
                )
        except Exception as e:
            report.checks_failed += 1
            report.errors.append(f"Error checking priority acts: {e}")

        # ------------------------------------------------------------------
        # 2. Acts without chunks
        # ------------------------------------------------------------------
        try:
            console.print("[bold]2/8 Checking acts without chunks...[/]")
            acts_without_chunks_q = (
                select(Act.id, Act.eli_id, Act.title)
                .outerjoin(Chunk, Chunk.act_id == Act.id)
                .group_by(Act.id)
                .having(func.count(Chunk.id) == 0)
            )
            result = await session.execute(acts_without_chunks_q)
            acts_without_chunks = result.all()

            if acts_without_chunks:
                report.checks_failed += 1
                titles = [f"{r.eli_id}" for r in acts_without_chunks]
                report.errors.append(
                    f"{len(acts_without_chunks)} act(s) without chunks: {', '.join(titles[:10])}"
                    + (" ..." if len(titles) > 10 else "")
                )
                if fix:
                    from polish_law_helper.ingestion.ingest_acts import ingest_acts

                    eli_ids = [r.eli_id for r in acts_without_chunks]
                    await ingest_acts(eli_ids=eli_ids, force=True)
                    report.actions_taken.append(
                        f"Re-ingested {len(eli_ids)} act(s) without chunks"
                    )
            else:
                report.checks_passed += 1
                console.print("  [green]OK All acts have chunks[/]")
        except Exception as e:
            report.checks_failed += 1
            report.errors.append(f"Error checking acts without chunks: {e}")

        # ------------------------------------------------------------------
        # 3. Chunks without embeddings
        # ------------------------------------------------------------------
        try:
            console.print("[bold]3/8 Checking chunks without embeddings...[/]")
            null_embed_q = (
                select(
                    Act.eli_id,
                    func.count(Chunk.id).label("cnt"),
                )
                .join(Act, Chunk.act_id == Act.id)
                .where(Chunk.embedding.is_(None))
                .group_by(Act.eli_id)
            )
            result = await session.execute(null_embed_q)
            null_embed_rows = result.all()

            total_null = sum(r.cnt for r in null_embed_rows)
            if total_null > 0:
                report.checks_failed += 1
                detail_parts = [f"{r.eli_id}: {r.cnt}" for r in null_embed_rows[:10]]
                report.errors.append(
                    f"{total_null} chunk(s) without embeddings ("
                    + ", ".join(detail_parts)
                    + ("..." if len(null_embed_rows) > 10 else "")
                    + ")"
                )
                if fix:
                    await _fix_null_embeddings(session, console)
                    report.actions_taken.append(
                        f"Generated embeddings for {total_null} chunk(s)"
                    )
            else:
                report.checks_passed += 1
                console.print("  [green]OK All chunks have embeddings[/]")
        except Exception as e:
            report.checks_failed += 1
            report.errors.append(f"Error checking chunk embeddings: {e}")

        # ------------------------------------------------------------------
        # 4. Act freshness vs upstream (last 7 days)
        # ------------------------------------------------------------------
        try:
            console.print("[bold]4/8 Checking act freshness...[/]")
            from polish_law_helper.ingestion.eli_client import ELIClient

            eli_client = ELIClient()
            try:
                since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime(
                    "%Y-%m-%d"
                )
                changes = await eli_client.get_changes_since(since)

                stale_acts: list[str] = []
                if isinstance(changes, list):
                    for change in changes:
                        eli_id = change.get("ELI")
                        if not eli_id:
                            continue
                        # Check whether we have this act
                        act_row = await session.execute(
                            select(Act.eli_id, Act.raw_html_hash).where(
                                Act.eli_id == eli_id
                            )
                        )
                        act = act_row.one_or_none()
                        if act is not None:
                            # We have it -- cannot easily compare hashes without
                            # re-fetching HTML, so mark as potentially stale
                            stale_acts.append(eli_id)

                if stale_acts:
                    report.warnings.append(
                        f"{len(stale_acts)} act(s) changed upstream in the last 7 days: "
                        + ", ".join(stale_acts[:10])
                        + (" ..." if len(stale_acts) > 10 else "")
                    )
                    if fix:
                        from polish_law_helper.ingestion.ingest_acts import ingest_acts

                        await ingest_acts(eli_ids=stale_acts, force=False)
                        report.actions_taken.append(
                            f"Updated {len(stale_acts)} act(s) with upstream changes"
                        )
                else:
                    report.checks_passed += 1
                    console.print(
                        "  [green]OK No upstream changes in the last 7 days[/]"
                    )
            finally:
                await eli_client.close()
        except Exception as e:
            report.warnings.append(f"Failed to check act freshness: {e}")

        # ------------------------------------------------------------------
        # 5. Legislative processes -- freshness
        # ------------------------------------------------------------------
        try:
            console.print("[bold]5/8 Checking legislative processes...[/]")
            proc_count = (
                await session.scalar(
                    select(func.count()).select_from(LegislativeProcess)
                )
                or 0
            )

            if proc_count == 0:
                report.checks_failed += 1
                report.errors.append("No legislative processes in database")
                if fix:
                    from polish_law_helper.tasks import run_ingest_sejm

                    await run_ingest_sejm(since_days=2000)
                    report.actions_taken.append("Initiated legislative process ingestion")
            else:
                newest_change = await session.scalar(
                    select(func.max(LegislativeProcess.change_date))
                )
                if newest_change and (
                    datetime.now(timezone.utc) - newest_change > timedelta(days=7)
                ):
                    report.warnings.append(
                        f"Latest legislative process from {newest_change.strftime('%d.%m.%Y')} "
                        f"— data may be stale"
                    )
                else:
                    report.checks_passed += 1
                    console.print(
                        f"  [green]OK {proc_count} legislative processes, data up to date[/]"
                    )

            # Check votings — compare count against API
            voting_count = (
                await session.scalar(select(func.count()).select_from(Voting)) or 0
            )

            # Fetch expected count from API
            expected_votings = 0
            missing_sittings = []
            try:
                from polish_law_helper.ingestion.sejm_client import SejmClient
                sejm_client = SejmClient()
                try:
                    sittings_summary = await sejm_client.get_votings()
                    if isinstance(sittings_summary, list):
                        expected_votings = sum(
                            s.get("votingsNum", 0) for s in sittings_summary
                        )
                        # Check which sittings we're missing
                        for s in sittings_summary:
                            sitting_num = s.get("proceeding")
                            expected = s.get("votingsNum", 0)
                            if not sitting_num:
                                continue
                            actual = await session.scalar(
                                select(func.count()).select_from(Voting)
                                .where(Voting.sitting == sitting_num)
                            ) or 0
                            if actual < expected:
                                missing_sittings.append(sitting_num)
                finally:
                    await sejm_client.close()
            except Exception:
                pass  # Can't reach API — fall back to basic check

            needs_ingest = False
            if voting_count == 0:
                report.checks_failed += 1
                report.errors.append("No votings in database")
                needs_ingest = True
            elif missing_sittings:
                report.checks_failed += 1
                report.errors.append(
                    f"Missing votings from {len(missing_sittings)} sitting(s) "
                    f"(have {voting_count}/{expected_votings})"
                )
                needs_ingest = True
            elif expected_votings and voting_count < expected_votings * 0.95:
                report.warnings.append(
                    f"Have {voting_count}/{expected_votings} votings — data may be incomplete"
                )
                needs_ingest = True
            else:
                # Check freshness
                latest_voting = await session.scalar(select(func.max(Voting.date)))
                if latest_voting and (
                    datetime.now(timezone.utc).date() - latest_voting > timedelta(days=7)
                ):
                    report.warnings.append(
                        f"Latest voting from {latest_voting.strftime('%d.%m.%Y')} "
                        f"— data may be stale"
                    )
                    needs_ingest = True
                else:
                    report.checks_passed += 1
                    console.print(
                        f"  [green]OK {voting_count}"
                        f"{'/' + str(expected_votings) if expected_votings else ''}"
                        f" votings, data complete[/]"
                    )

            if needs_ingest and fix:
                from polish_law_helper.tasks import run_ingest_sejm

                console.print(
                    f"  [bold yellow]Fetching missing votings "
                    f"({voting_count}/{expected_votings})...[/]"
                )
                await run_ingest_sejm(since_days=2000)
                report.actions_taken.append(
                    f"Fetched Sejm votings ({voting_count} → {expected_votings})"
                )

        except Exception as e:
            report.checks_failed += 1
            report.errors.append(f"Error checking legislative processes: {e}")

        # ------------------------------------------------------------------
        # 6. Parliamentary prints -- presence and integrity
        # ------------------------------------------------------------------
        try:
            console.print("[bold]6/8 Checking parliamentary prints...[/]")

            # Check if any prints have been ingested at all
            prints_count = (
                await session.scalar(select(func.count()).select_from(SejmPrint)) or 0
            )
            if prints_count == 0:
                report.checks_failed += 1
                report.errors.append("No parliamentary prints in database")
                if fix:
                    console.print(
                        "  [bold yellow]No prints found — fetching parliamentary prints...[/]"
                    )
                    try:
                        from polish_law_helper.tasks import run_ingest_prints
                        await run_ingest_prints(since_days=2000)
                        report.actions_taken.append("Fetched parliamentary prints")
                    except Exception as e:
                        console.print(f"  [red]Failed to fetch prints: {e}[/]")
                        report.actions_taken.append(f"Failed to fetch prints: {e}")
            else:
                console.print(f"  {prints_count} parliamentary prints in database")
            prints_with_text_no_chunks_q = (
                select(SejmPrint.id, SejmPrint.print_number)
                .outerjoin(PrintChunk, PrintChunk.print_id == SejmPrint.id)
                .where(SejmPrint.text_content.isnot(None))
                .group_by(SejmPrint.id)
                .having(func.count(PrintChunk.id) == 0)
            )
            result = await session.execute(prints_with_text_no_chunks_q)
            prints_no_chunks = result.all()

            # PrintChunks with NULL embedding vectors
            null_print_embed_count = (
                await session.scalar(
                    select(func.count()).select_from(PrintChunk).where(
                        PrintChunk.embedding.is_(None)
                    )
                )
                or 0
            )

            issues_found = False
            if prints_no_chunks:
                issues_found = True
                report.warnings.append(
                    f"{len(prints_no_chunks)} print(s) with text but no chunks"
                )

            if null_print_embed_count > 0:
                issues_found = True
                report.warnings.append(
                    f"{null_print_embed_count} print chunk(s) without embeddings"
                )
                if fix:
                    await _fix_null_print_embeddings(session, console)
                    report.actions_taken.append(
                        f"Generated embeddings for {null_print_embed_count} print chunk(s)"
                    )

            if not issues_found:
                report.checks_passed += 1
                console.print("  [green]OK Parliamentary prints OK[/]")
        except Exception as e:
            report.checks_failed += 1
            report.errors.append(f"Error checking parliamentary prints: {e}")

        # ------------------------------------------------------------------
        # 7. Orphaned data
        # ------------------------------------------------------------------
        try:
            console.print("[bold]7/8 Checking orphaned data...[/]")
            orphan_issues: list[str] = []

            # Chunks referencing non-existent acts (broken FK)
            orphan_chunks = (
                await session.scalar(
                    select(func.count())
                    .select_from(Chunk)
                    .outerjoin(Act, Chunk.act_id == Act.id)
                    .where(Act.id.is_(None))
                )
                or 0
            )
            if orphan_chunks > 0:
                orphan_issues.append(
                    f"{orphan_chunks} chunk(s) without a linked act"
                )

            # PrintChunks referencing non-existent prints (broken FK)
            orphan_print_chunks = (
                await session.scalar(
                    select(func.count())
                    .select_from(PrintChunk)
                    .outerjoin(SejmPrint, PrintChunk.print_id == SejmPrint.id)
                    .where(SejmPrint.id.is_(None))
                )
                or 0
            )
            if orphan_print_chunks > 0:
                orphan_issues.append(
                    f"{orphan_print_chunks} print chunk(s) without a linked print"
                )

            if orphan_issues:
                report.checks_failed += 1
                for issue in orphan_issues:
                    report.errors.append(f"Orphaned data: {issue}")
            else:
                report.checks_passed += 1
                console.print("  [green]OK No orphaned data[/]")
        except Exception as e:
            report.checks_failed += 1
            report.errors.append(f"Error checking orphaned data: {e}")

        # ------------------------------------------------------------------
        # 8. Recent ingestion errors (last 24h)
        # ------------------------------------------------------------------
        try:
            console.print("[bold]8/8 Checking recent ingestion errors...[/]")
            since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
            error_logs_q = (
                select(IngestionLog)
                .where(
                    IngestionLog.status == "error",
                    IngestionLog.started_at >= since_24h,
                )
                .order_by(IngestionLog.started_at.desc())
                .limit(20)
            )
            result = await session.execute(error_logs_q)
            error_logs = result.scalars().all()

            if error_logs:
                for log in error_logs:
                    report.warnings.append(
                        f"Ingestion error [{log.source}] {log.identifier}: {log.message}"
                    )
                console.print(
                    f"  [yellow]! {len(error_logs)} ingestion error(s) in the last 24h[/]"
                )
            else:
                report.checks_passed += 1
                console.print("  [green]OK No ingestion errors in the last 24h[/]")
        except Exception as e:
            report.checks_failed += 1
            report.errors.append(f"Error checking ingestion logs: {e}")

    # Print summary table
    _print_report(console, report)
    return report


async def _fix_null_embeddings(session, console: Console) -> None:
    """Generate embeddings for act chunks that have NULL embedding vectors."""
    from polish_law_helper.embeddings.ollama_client import embedder

    batch_size = settings.embedding_batch_size

    # Fetch chunks with NULL embeddings in batches and fill them
    offset = 0
    total_fixed = 0
    while True:
        result = await session.execute(
            select(Chunk.id, Chunk.text_for_embedding)
            .where(Chunk.embedding.is_(None))
            .limit(batch_size)
            .offset(offset)
        )
        rows = result.all()
        if not rows:
            break

        texts = [r.text_for_embedding for r in rows]
        try:
            embeddings = await embedder.embed_batch(texts)
        except Exception as e:
            console.print(f"  [red]Error generating embeddings: {e}[/]")
            break

        for row, embedding in zip(rows, embeddings):
            await session.execute(
                Chunk.__table__.update()
                .where(Chunk.__table__.c.id == row.id)
                .values(embedding=embedding)
            )

        total_fixed += len(rows)
        console.print(f"  Fixed {total_fixed} chunks...")

        if len(rows) < batch_size:
            break

    await session.commit()
    console.print(f"  [green]Generated embeddings for {total_fixed} chunks[/]")


async def _fix_null_print_embeddings(session, console: Console) -> None:
    """Generate embeddings for print chunks that have NULL embedding vectors."""
    from polish_law_helper.embeddings.ollama_client import embedder

    batch_size = settings.embedding_batch_size

    offset = 0
    total_fixed = 0
    while True:
        result = await session.execute(
            select(PrintChunk.id, PrintChunk.text_for_embedding)
            .where(PrintChunk.embedding.is_(None))
            .limit(batch_size)
            .offset(offset)
        )
        rows = result.all()
        if not rows:
            break

        texts = [r.text_for_embedding for r in rows]
        try:
            embeddings = await embedder.embed_batch(texts)
        except Exception as e:
            console.print(f"  [red]Error generating print embeddings: {e}[/]")
            break

        for row, embedding in zip(rows, embeddings):
            await session.execute(
                PrintChunk.__table__.update()
                .where(PrintChunk.__table__.c.id == row.id)
                .values(embedding=embedding)
            )

        total_fixed += len(rows)
        console.print(f"  Fixed {total_fixed} print chunks...")

        if len(rows) < batch_size:
            break

    await session.commit()
    console.print(
        f"  [green]Generated embeddings for {total_fixed} print chunks[/]"
    )


def _print_report(console: Console, report: IntegrityReport) -> None:
    """Display the integrity report as a Rich table."""
    console.print()
    table = Table(title="Data Integrity Report")
    table.add_column("Status", style="bold", width=20)
    table.add_column("Details")

    table.add_row("[green]Checks OK[/]", str(report.checks_passed))
    table.add_row("[red]Errors[/]", str(report.checks_failed))
    table.add_row("[yellow]Warnings[/]", str(len(report.warnings)))

    if report.errors:
        for e in report.errors:
            table.add_row("[red]x[/]", e)
    if report.warnings:
        for w in report.warnings:
            table.add_row("[yellow]![/]", w)
    if report.actions_taken:
        for a in report.actions_taken:
            table.add_row("[blue]->[/]", a)

    console.print(table)
