"""CLI entry point for Lupa na prawo."""

import asyncio

import typer
from rich.console import Console

app = typer.Typer(help="Lupa na prawo — semantic search over Polish law")
console = Console()


@app.command()
def ingest_acts(
    eli_ids: list[str] = typer.Argument(
        None, help="ELI IDs to ingest (e.g. DU/1964/93). Defaults to priority codexes."
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Re-ingest even if unchanged"),
    all_priority: bool = typer.Option(False, "--all", "-a", help="Ingest all priority codexes"),
    all_laws: bool = typer.Option(False, "--all-laws", help="Ingest ALL in-force Polish laws with HTML text (~8-15k acts, takes hours)"),
    publisher: str = typer.Option("DU", help="Publisher filter for --all-laws (DU or MP)"),
    include_not_in_force: bool = typer.Option(False, "--include-historical", help="Include acts no longer in force"),
):
    """Ingest law acts from the ELI API."""
    from polish_law_helper.ingestion.ingest_acts import ingest_acts as _ingest
    from polish_law_helper.ingestion.ingest_acts import ingest_all_laws

    if all_laws:
        asyncio.run(ingest_all_laws(
            force=force,
            publisher=publisher,
            in_force_only=not include_not_in_force,
        ))
    else:
        ids = eli_ids if eli_ids else None
        if all_priority:
            ids = None  # Will use PRIORITY_ACTS
        asyncio.run(_ingest(eli_ids=ids, force=force))


@app.command()
def ingest_sejm(
    since_days: int = typer.Option(30, "--since", "-s", help="Ingest data from last N days"),
):
    """Ingest Sejm parliamentary data (processes + votings)."""
    from polish_law_helper.ingestion.ingest_sejm import ingest_sejm as _ingest

    asyncio.run(_ingest(since_days=since_days))


@app.command()
def ingest_prints(
    since_days: int = typer.Option(30, "--since", "-s", help="Ingest prints from last N days"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-ingest even if unchanged"),
):
    """Ingest Sejm parliamentary prints (druki sejmowe)."""
    from polish_law_helper.ingestion.ingest_prints import ingest_prints as _ingest

    asyncio.run(_ingest(since_days=since_days, force=force))


@app.command()
def ingest_senat():
    """Ingest Senat parliamentary data (prints / processes)."""
    from polish_law_helper.ingestion.ingest_senat import ingest_senat as _ingest

    asyncio.run(_ingest())


@app.command()
def check(
    fix: bool = typer.Option(False, "--fix", "-f", help="Sprobuj naprawic znalezione problemy"),
):
    """Sprawdz integralnosc danych w bazie."""
    from polish_law_helper.integrity import run_integrity_check

    asyncio.run(run_integrity_check(fix=fix))


@app.command()
def sync():
    """Run nightly sync manually (fetch new/changed acts + Sejm + Senat data)."""
    from polish_law_helper.scheduler import run_nightly_sync

    asyncio.run(run_nightly_sync())


@app.command()
def link():
    """Link processes to acts and votings to processes."""
    from polish_law_helper.ingestion.linker import (
        link_processes_to_acts,
        link_votings_to_processes,
    )

    async def _run():
        acts_linked = await link_processes_to_acts()
        votings_linked = await link_votings_to_processes()
        return acts_linked, votings_linked

    acts_linked, votings_linked = asyncio.run(_run())
    console.print(f"Linked {acts_linked} processes to acts, {votings_linked} votings to processes.")


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Host to bind"),
    port: int = typer.Option(8765, help="Port to bind"),
):
    """Start the unified server (dashboard + REST API + MCP at /mcp)."""
    import uvicorn

    uvicorn.run("polish_law_helper.server:app", host=host, port=port, reload=True)


@app.command()
def mcp(
    transport: str = typer.Option("stdio", help="MCP transport: stdio or sse"),
):
    """Start the MCP server."""
    from polish_law_helper.mcp_server import mcp as mcp_server

    mcp_server.run(transport=transport)


@app.command()
def migrate():
    """Run database migrations."""
    import subprocess

    subprocess.run(["alembic", "upgrade", "head"], check=True)


@app.command()
def stats():
    """Show database statistics."""
    from polish_law_helper.db.engine import async_session
    from polish_law_helper.db.models import Act, Chunk, LegislativeProcess, SenatProcess, Voting
    from sqlalchemy import func, select

    async def _stats():
        async with async_session() as session:
            acts = await session.scalar(select(func.count()).select_from(Act))
            chunks = await session.scalar(select(func.count()).select_from(Chunk))
            processes = await session.scalar(select(func.count()).select_from(LegislativeProcess))
            votings = await session.scalar(select(func.count()).select_from(Voting))
            senat = await session.scalar(select(func.count()).select_from(SenatProcess))

        console.print(f"[bold]Lupa na prawo — Database Stats[/bold]")
        console.print(f"  Acts: {acts}")
        console.print(f"  Chunks: {chunks}")
        console.print(f"  Legislative processes: {processes}")
        console.print(f"  Votings: {votings}")
        console.print(f"  Senat processes: {senat}")

    asyncio.run(_stats())


if __name__ == "__main__":
    app()
