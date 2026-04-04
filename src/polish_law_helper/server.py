"""Unified server: FastAPI REST API + install guide dashboard + FastMCP streamable-http.

Run with:
    plh serve
    uvicorn polish_law_helper.server:app --port 8765

To update the database use the CLI:
    plh ingest-acts
    plh ingest-sejm
"""

import asyncio
import math
from contextlib import asynccontextmanager
from datetime import date as date_type, datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from rich.console import Console
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from polish_law_helper.config import settings
from polish_law_helper.db.engine import async_session, get_session
from polish_law_helper.db.models import Act, ActReference, Chunk, IngestionLog, LegislativeProcess, PrintChunk, SejmPrint, SenatProcess, Voting
from polish_law_helper.ingestion.eli_client import PRIORITY_ACTS
from polish_law_helper.main import router as api_router
from polish_law_helper.mcp_server import mcp
from polish_law_helper.tasks import run_ingest_acts, run_ingest_sejm, run_ingest_senat

_console = Console()


async def startup_check() -> None:
    """Run integrity check and attempt to fix issues on startup.

    Runs as a background task — never blocks server startup.
    """
    try:
        from polish_law_helper.integrity import run_integrity_check

        report = await run_integrity_check(fix=True)
        if report.ok:
            _console.print(
                "[bold green]Integrity check completed "
                "— all good.[/]"
            )
        else:
            _console.print(
                f"[bold yellow]Integrity check: "
                f"{report.checks_failed} issue(s), "
                f"remediation actions taken.[/]"
            )
            if report.actions_taken:
                for action in report.actions_taken:
                    _console.print(f"  [blue]→ {action}[/]")
    except Exception as e:
        _console.print(f"[red]Integrity check error: {e}[/]")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from polish_law_helper.scheduler import scheduler, setup_scheduler

    if not settings.skip_startup_ingest:
        asyncio.create_task(startup_check())

    if settings.cron_enabled:
        setup_scheduler()
        scheduler.start()
        _console.print(
            f"[bold green]Scheduler started — nightly sync at {settings.cron_hour:02d}:00[/bold green]"
        )

    yield

    if settings.cron_enabled and scheduler.running:
        scheduler.shutdown(wait=False)
        _console.print("[dim]Scheduler shut down.[/dim]")


# Filter out "wniosek" document types from process listings — not useful for end users
# Filter out motions from process listings
_HIDDEN_DOC_TYPES = ("wniosek", "wniosek (bez druku)")

# Filter condition for hiding procedural votings
from sqlalchemy import and_, not_, or_

def _voting_not_procedural():
    """SQLAlchemy filter to exclude procedural/agenda votings."""
    return and_(
        not_(Voting.title.ilike("%proceduraln%")),
        not_(Voting.title.ilike("%posiedzenie sejmu%")),
    )

app = FastAPI(
    title="Lupa na prawo",
    version="1.0.0",
    description="Semantic search over Polish law + Sejm/Senat legislative tracking.",
    lifespan=lifespan,
)

# MCP clients connect to: {settings.base_url}/mcp
app.mount("/mcp", mcp.streamable_http_app())
app.include_router(api_router)

_TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")
_TEMPLATES.env.globals["base_url"] = settings.base_url
_TEMPLATES.env.globals["app_name"] = "Lupa na prawo"

# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: AsyncSession = Depends(get_session)):
    # Query data for SSR (no hx-trigger="load" on initial content)
    acts_result = await session.execute(
        select(Act)
        .order_by(
            # Active acts first, expired last
            (Act.in_force == "NOT_IN_FORCE").asc(),
            Act.created_at.desc(),
        )
        .limit(4)
    )
    acts = acts_result.scalars().all()

    proc_result = await session.execute(
        select(LegislativeProcess)
        .where(LegislativeProcess.document_type.notin_(_HIDDEN_DOC_TYPES))
        .order_by(LegislativeProcess.process_start.desc().nullslast())
        .limit(3)
    )
    processes = proc_result.scalars().all()

    vot_result = await session.execute(
        select(Voting).where(_voting_not_procedural()).order_by(Voting.date.desc()).limit(3)
    )
    votings = vot_result.scalars().all()

    # Stats
    acts_count = await session.scalar(select(func.count()).select_from(Act)) or 0
    chunks_count = await session.scalar(select(func.count()).select_from(Chunk)) or 0
    processes_count = await session.scalar(select(func.count()).select_from(LegislativeProcess)) or 0
    votings_count = await session.scalar(
        select(func.count()).select_from(Voting).where(_voting_not_procedural())
    ) or 0

    return _TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {
            "acts": acts,
            "processes": processes,
            "votings": votings,
            "acts_count": acts_count,
            "chunks_count": chunks_count,
            "processes_count": processes_count,
            "votings_count": votings_count,
        },
    )


@app.get("/ustawy", response_class=HTMLResponse)
async def acts_list_page(
    request: Request,
    q: str | None = None,
    act_type: str | None = None,
    page: int = 1,
    session: AsyncSession = Depends(get_session),
):
    per_page = 24

    # Distinct act types for filter
    act_types_result = await session.execute(
        select(distinct(Act.act_type)).where(Act.act_type.isnot(None)).order_by(Act.act_type)
    )
    act_types = [row[0] for row in act_types_result.all()]

    # Count query
    count_stmt = select(func.count()).select_from(Act)
    if q and q.strip():
        count_stmt = count_stmt.where(Act.title.ilike(f"%{q}%"))
    if act_type:
        count_stmt = count_stmt.where(Act.act_type == act_type)
    total = await session.scalar(count_stmt) or 0

    import math
    total_pages = max(1, math.ceil(total / per_page))
    page = max(1, min(page, total_pages))

    # Acts query
    stmt = (
        select(Act)
        .order_by(
            (Act.in_force == "NOT_IN_FORCE").asc(),
            Act.title,
        )
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    if q and q.strip():
        stmt = stmt.where(Act.title.ilike(f"%{q}%"))
    if act_type:
        stmt = stmt.where(Act.act_type == act_type)

    result = await session.execute(stmt)
    acts = result.scalars().all()

    return _TEMPLATES.TemplateResponse(
        request,
        "acts_list.html",
        {
            "acts": acts,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "query": q,
            "act_type": act_type,
            "act_types": act_types,
        },
    )


@app.get("/szukaj", response_class=HTMLResponse)
async def search_page(
    request: Request,
    q: str | None = None,
    act_type: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    # Fetch distinct act types for sidebar filter
    act_types_result = await session.execute(
        select(distinct(Act.act_type)).where(Act.act_type.isnot(None)).order_by(Act.act_type)
    )
    act_types = [row[0] for row in act_types_result.all()]
    # If query is present, run semantic search for SSR
    results = []
    total = 0
    page = 1
    total_pages = 0

    search_error = None
    if q and q.strip():
        from polish_law_helper.embeddings.ollama_client import embedder

        per_page = 10
        try:
            query_embedding = await embedder.embed(q, prefix="query")
        except Exception:
            search_error = "Failed to connect to the search engine. Try again in a moment."
            query_embedding = None

        if query_embedding is None and search_error:
            return _TEMPLATES.TemplateResponse(
                request,
                "search.html",
                {
                    "query": q,
                    "act_type": act_type,
                    "act_types": act_types,
                    "results": [],
                    "total": 0,
                    "page": 1,
                    "total_pages": 0,
                    "search_error": search_error,
                },
            )
        distance = Chunk.embedding.cosine_distance(query_embedding)

        count_stmt = select(func.count()).select_from(Chunk).join(Act, Chunk.act_id == Act.id)
        if act_type:
            count_stmt = count_stmt.where(Act.act_type == act_type)
        total = min(await session.scalar(count_stmt) or 0, 100)
        total_pages = max(1, math.ceil(total / per_page))

        stmt = (
            select(
                Chunk.id,
                Chunk.article_num,
                Chunk.paragraph_num,
                Chunk.text_content,
                Chunk.chapter_title,
                Act.title.label("act_title"),
                Act.eli_id,
                Act.act_type.label("act_type"),
                (1 - distance).label("score"),
            )
            .join(Act, Chunk.act_id == Act.id)
            .order_by(distance)
            .limit(per_page)
        )
        if act_type:
            stmt = stmt.where(Act.act_type == act_type)

        result = await session.execute(stmt)
        rows = result.all()
        results = [
            {
                "chunk_id": str(r.id),
                "act_title": r.act_title,
                "eli_id": r.eli_id,
                "article_num": r.article_num,
                "paragraph_num": r.paragraph_num,
                "chapter": r.chapter_title,
                "text": r.text_content,
                "act_type": r.act_type,
                "score": round(r.score, 4) if r.score else None,
            }
            for r in rows
        ]

    return _TEMPLATES.TemplateResponse(
        request,
        "search.html",
        {
            "query": q,
            "act_type": act_type,
            "act_types": act_types,
            "results": results,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "search_error": search_error,
        },
    )


@app.get("/sejm", response_class=HTMLResponse)
async def sejm_page(request: Request, tab: str = "processes", session: AsyncSession = Depends(get_session)):
    per_page = 20

    count_stmt = (
        select(func.count()).select_from(LegislativeProcess)
        .where(LegislativeProcess.document_type.notin_(_HIDDEN_DOC_TYPES))
    )
    total = await session.scalar(count_stmt) or 0
    total_pages = max(1, math.ceil(total / per_page))

    stmt = (
        select(LegislativeProcess)
        .where(LegislativeProcess.document_type.notin_(_HIDDEN_DOC_TYPES))
        .order_by(LegislativeProcess.process_start.desc().nullslast())
        .limit(per_page)
    )
    result = await session.execute(stmt)
    processes = result.scalars().all()

    # Senat data
    senat_count_stmt = select(func.count()).select_from(SenatProcess)
    senat_total = await session.scalar(senat_count_stmt) or 0
    senat_total_pages = max(1, math.ceil(senat_total / per_page))

    senat_stmt = (
        select(SenatProcess)
        .order_by(SenatProcess.updated_at.desc())
        .limit(per_page)
    )
    senat_result = await session.execute(senat_stmt)
    senat_processes = senat_result.scalars().all()

    # Votings data (for SSR when votings tab is active)
    votings_data = []
    votings_total_pages = 1
    if tab == "votings":
        from datetime import date as date_type
        vot_count_stmt = select(func.count()).select_from(Voting).where(_voting_not_procedural())
        vot_total = await session.scalar(vot_count_stmt) or 0
        votings_total_pages = max(1, math.ceil(vot_total / per_page))

        vot_stmt = (
            select(Voting)
            .where(_voting_not_procedural())
            .order_by(Voting.date.desc())
            .limit(per_page)
        )
        vot_result = await session.execute(vot_stmt)
        votings_data = vot_result.scalars().all()

    return _TEMPLATES.TemplateResponse(
        request,
        "sejm.html",
        {
            "processes": processes,
            "query": None,
            "page": 1,
            "total_pages": total_pages,
            "senat_processes": senat_processes,
            "senat_page": 1,
            "senat_total_pages": senat_total_pages,
            "active_tab": tab,
            "votings": votings_data,
            "date_from": None,
            "date_to": None,
            # Override page/total_pages for votings tab SSR
            **({"page": 1, "total_pages": votings_total_pages} if tab == "votings" else {}),
        },
    )


@app.get("/mcp-poradnik", response_class=HTMLResponse)
async def mcp_guide_page(request: Request):
    return _TEMPLATES.TemplateResponse(request, "mcp_guide.html")


@app.get("/regulamin", response_class=HTMLResponse)
async def regulamin_page(request: Request):
    return _TEMPLATES.TemplateResponse(request, "regulamin.html")


@app.get("/prywatnosc", response_class=HTMLResponse)
async def prywatnosc_page(request: Request):
    return _TEMPLATES.TemplateResponse(request, "prywatnosc.html")


# ---------------------------------------------------------------------------
# Detail page routes
# ---------------------------------------------------------------------------


@app.get("/ustawa/{eli_id:path}", response_class=HTMLResponse)
async def act_detail(request: Request, eli_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Act).where(Act.eli_id == eli_id))
    act = result.scalar_one_or_none()
    if not act:
        return HTMLResponse(
            _not_found_page("Ustawa", f"Nie znaleziono aktu prawnego o ELI: {eli_id}"),
            status_code=404,
        )

    # Chunks ordered by hierarchy
    chunks_result = await session.execute(
        select(Chunk)
        .where(Chunk.act_id == act.id)
        .order_by(Chunk.chapter_num.nullsfirst(), Chunk.article_num, Chunk.paragraph_num.nullsfirst(), Chunk.point_num.nullsfirst())
    )
    chunks = chunks_result.scalars().all()

    # Build chapters list for TOC
    seen_chapters: dict[tuple, dict] = {}
    for c in chunks:
        key = (c.chapter_num, c.chapter_title)
        if key not in seen_chapters and (c.chapter_num or c.chapter_title):
            seen_chapters[key] = {"num": c.chapter_num, "title": c.chapter_title}
    chapters = list(seen_chapters.values())

    # Cross-references
    refs_result = await session.execute(
        select(ActReference).where(ActReference.source_act_id == act.id)
    )
    references = refs_result.scalars().all()

    # Related legislative process
    proc_result = await session.execute(
        select(LegislativeProcess).where(LegislativeProcess.related_act_eli == eli_id).limit(1)
    )
    related_process = proc_result.scalar_one_or_none()

    return _TEMPLATES.TemplateResponse(
        request,
        "act_detail.html",
        {
            "act": act,
            "chunks": chunks,
            "chunks_count": len(chunks),
            "chapters": chapters,
            "references": references,
            "related_process": related_process,
        },
    )


@app.get("/proces/{process_number}", response_class=HTMLResponse)
async def process_detail(request: Request, process_number: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(LegislativeProcess).where(
            LegislativeProcess.term == settings.sejm_term,
            LegislativeProcess.process_number == process_number,
        )
    )
    process = result.scalar_one_or_none()
    if not process:
        return HTMLResponse(
            _not_found_page("Proces legislacyjny", f"Nie znaleziono procesu nr {process_number}"),
            status_code=404,
        )

    # Related votings
    vot_result = await session.execute(
        select(Voting).where(Voting.process_id == process.id).order_by(Voting.date.desc())
    )
    votings = vot_result.scalars().all()

    # Related Senat process
    senat_result = await session.execute(
        select(SenatProcess).where(
            SenatProcess.sejm_process_number == process_number,
        ).limit(1)
    )
    senat_process = senat_result.scalar_one_or_none()

    # Derive process status from stages
    rj = process.raw_json or {}
    stages = rj.get("stages", [])
    stage_names_lower = [s.get("stageName", "").lower() for s in stages]
    stage_decisions_lower = [s.get("decision", "").lower() for s in stages if s.get("decision")]
    has_veto = any("weto" in sn for sn in stage_names_lower)
    has_signed = any("podpisa" in sn for sn in stage_names_lower)
    has_rejection = any("odrzuc" in d for d in stage_decisions_lower)
    veto_overridden = has_veto and sum(1 for sn in stage_names_lower if "uchwalon" in sn) > 1

    if has_veto and not veto_overridden:
        process_status = "veto"
    elif veto_overridden:
        process_status = "veto_overridden"
    elif has_signed:
        process_status = "signed"
    elif has_rejection:
        process_status = "rejected"
    elif rj.get("passed"):
        process_status = "passed"
    elif not process.closure_date:
        process_status = "in_progress"
    elif process.closure_date and not rj.get("passed"):
        process_status = "rejected"
    else:
        process_status = "unknown"

    return _TEMPLATES.TemplateResponse(
        request,
        "process_detail.html",
        {
            "process": process,
            "votings": votings,
            "senat_process": senat_process,
            "process_status": process_status,
        },
    )


@app.get("/glosowanie/{sitting}/{voting_number}", response_class=HTMLResponse)
async def voting_detail(request: Request, sitting: int, voting_number: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(Voting).where(
            Voting.term == settings.sejm_term,
            Voting.sitting == sitting,
            Voting.voting_number == voting_number,
        )
    )
    voting = result.scalar_one_or_none()
    if not voting:
        return HTMLResponse(
            _not_found_page("Głosowanie", f"Nie znaleziono głosowania (posiedzenie {sitting}, nr {voting_number})"),
            status_code=404,
        )

    # Parent process
    parent_process = None
    if voting.process_id:
        proc_result = await session.execute(
            select(LegislativeProcess).where(LegislativeProcess.id == voting.process_id)
        )
        parent_process = proc_result.scalar_one_or_none()

    return _TEMPLATES.TemplateResponse(
        request,
        "voting_detail.html",
        {
            "voting": voting,
            "parent_process": parent_process,
        },
    )


@app.get("/druk/{print_number}", response_class=HTMLResponse)
async def print_detail(request: Request, print_number: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(SejmPrint).where(
            SejmPrint.term == settings.sejm_term,
            SejmPrint.print_number == print_number,
        )
    )
    print_record = result.scalar_one_or_none()
    if not print_record:
        # Redirect to Sejm website if we don't have the print locally
        from fastapi.responses import RedirectResponse
        return RedirectResponse(
            url=f"https://www.sejm.gov.pl/Sejm{settings.sejm_term}.nsf/druk.xsp?nr={print_number}",
            status_code=302,
        )

    return _TEMPLATES.TemplateResponse(
        request,
        "print_detail.html",
        {"print_record": print_record},
    )


@app.get("/senat/{print_number}", response_class=HTMLResponse)
async def senat_detail(request: Request, print_number: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(SenatProcess).where(
            SenatProcess.term == settings.senat_term,
            SenatProcess.print_number == print_number,
        )
    )
    senat = result.scalar_one_or_none()
    if not senat:
        return HTMLResponse(
            _not_found_page("Proces senacki", f"Nie znaleziono druku senackiego nr {print_number}"),
            status_code=404,
        )

    return _TEMPLATES.TemplateResponse(
        request,
        "senat_detail.html",
        {"senat": senat},
    )


def _not_found_page(entity: str, message: str) -> str:
    """Return a styled 404 HTML page."""
    return f"""<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{entity} — nie znaleziono</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Manrope:wght@600;700;800&display=swap" rel="stylesheet">
  <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,400,0,0" rel="stylesheet">
  <style>body {{ font-family: 'Inter', sans-serif; }} h1,h2 {{ font-family: 'Manrope', sans-serif; }}</style>
</head>
<body class="bg-gray-50 min-h-screen flex items-center justify-center">
  <div class="text-center px-4">
    <span class="material-symbols-outlined text-6xl text-gray-300 mb-4">search_off</span>
    <h1 class="text-2xl font-bold text-gray-700 mb-2">{entity} — nie znaleziono</h1>
    <p class="text-gray-500 mb-6">{message}</p>
    <a href="/" class="inline-flex items-center gap-2 px-6 py-3 rounded-lg text-white text-sm font-semibold"
       style="background: linear-gradient(135deg, #ba002e 0%, #e31c40 100%);">
      <span class="material-symbols-outlined text-sm">home</span>
      Wróć na stronę główną
    </a>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Partial routes (HTMX fragments)
# ---------------------------------------------------------------------------


@app.get("/partials/stats", response_class=HTMLResponse)
async def stats_partial(session: AsyncSession = Depends(get_session)):
    try:
        async with asyncio.timeout(5):
            acts = await session.scalar(select(func.count()).select_from(Act))
            chunks = await session.scalar(select(func.count()).select_from(Chunk))
            processes = await session.scalar(select(func.count()).select_from(LegislativeProcess))
            votings = await session.scalar(select(func.count()).select_from(Voting))
            prints = await session.scalar(select(func.count()).select_from(SejmPrint))
            last_log = (
                await session.execute(
                    select(IngestionLog)
                    .where(IngestionLog.status == "success")
                    .order_by(IngestionLog.completed_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
    except (asyncio.TimeoutError, Exception) as e:
        msg = "DB timeout — is PostgreSQL running?" if isinstance(e, asyncio.TimeoutError) else str(e)
        return HTMLResponse(
            f'<div class="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-800">'
            f'<strong>Baza danych niedostępna:</strong> {msg}</div>'
        )

    last_updated = (
        last_log.completed_at.strftime("%d.%m.%Y %H:%M") if last_log else "Nigdy"
    )
    return HTMLResponse(f"""
<div class="grid grid-cols-2 md:grid-cols-4 gap-4">
  <div class="bg-white rounded-lg shadow-sm p-4 text-center">
    <div class="text-3xl font-bold text-primary font-headline">{acts}</div>
    <div class="text-xs text-gray-500 mt-1">Aktów prawnych</div>
  </div>
  <div class="bg-white rounded-lg shadow-sm p-4 text-center">
    <div class="text-3xl font-bold text-green-600 font-headline">{chunks:,}</div>
    <div class="text-xs text-gray-500 mt-1">Przepisów</div>
  </div>
  <div class="bg-white rounded-lg shadow-sm p-4 text-center">
    <div class="text-3xl font-bold text-amber-600 font-headline">{processes:,}</div>
    <div class="text-xs text-gray-500 mt-1">Procesów w Sejmie</div>
  </div>
  <div class="bg-white rounded-lg shadow-sm p-4 text-center">
    <div class="text-3xl font-bold text-indigo-600 font-headline">{votings:,}</div>
    <div class="text-xs text-gray-500 mt-1">Głosowań</div>
  </div>
</div>
<p class="text-gray-400 text-xs mt-3 text-center">Ostatnia aktualizacja: {last_updated}</p>
""")


@app.get("/partials/recent-acts", response_class=HTMLResponse)
async def recent_acts_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Act)
        .order_by(
            (Act.in_force == "NOT_IN_FORCE").asc(),
            Act.created_at.desc(),
        )
        .limit(4)
    )
    acts = result.scalars().all()
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/recent_acts.html",
        {"acts": acts},
    )


@app.get("/partials/sejm-live", response_class=HTMLResponse)
async def sejm_live_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    proc_result = await session.execute(
        select(LegislativeProcess)
        .where(LegislativeProcess.document_type.notin_(_HIDDEN_DOC_TYPES))
        .order_by(LegislativeProcess.process_start.desc().nullslast())
        .limit(3)
    )
    processes = proc_result.scalars().all()

    vot_result = await session.execute(
        select(Voting)
        .where(_voting_not_procedural())
        .order_by(Voting.date.desc())
        .limit(3)
    )
    votings = vot_result.scalars().all()

    return _TEMPLATES.TemplateResponse(
        request,
        "partials/sejm_live.html",
        {"processes": processes, "votings": votings},
    )


@app.get("/partials/search-results", response_class=HTMLResponse)
async def search_results_partial(
    request: Request,
    q: str = "",
    page: int = Query(1, ge=1),
    act_type: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    per_page = 10

    if not q.strip():
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/search_results.html",
            {"results": [], "query": q, "act_type": act_type, "total": 0, "page": page, "total_pages": 0},
        )

    from polish_law_helper.embeddings.ollama_client import embedder

    query_embedding = await embedder.embed(q, prefix="query")
    distance = Chunk.embedding.cosine_distance(query_embedding)

    # Count total matching results (capped at 100 for performance)
    count_stmt = select(func.count()).select_from(Chunk).join(Act, Chunk.act_id == Act.id)
    if act_type:
        count_stmt = count_stmt.where(Act.act_type == act_type)
    # For semantic search, we limit total accessible results
    total = min(await session.scalar(count_stmt) or 0, 100)
    total_pages = max(1, math.ceil(total / per_page))
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    stmt = (
        select(
            Chunk.id,
            Chunk.article_num,
            Chunk.paragraph_num,
            Chunk.text_content,
            Chunk.chapter_title,
            Act.title.label("act_title"),
            Act.eli_id,
            Act.act_type.label("act_type"),
            (1 - distance).label("score"),
        )
        .join(Act, Chunk.act_id == Act.id)
        .order_by(distance)
        .offset(offset)
        .limit(per_page)
    )

    if act_type:
        stmt = stmt.where(Act.act_type == act_type)

    result = await session.execute(stmt)
    rows = result.all()

    results = [
        {
            "chunk_id": str(r.id),
            "act_title": r.act_title,
            "eli_id": r.eli_id,
            "article_num": r.article_num,
            "paragraph_num": r.paragraph_num,
            "chapter": r.chapter_title,
            "text": r.text_content,
            "act_type": r.act_type,
            "score": round(r.score, 4) if r.score else None,
        }
        for r in rows
    ]

    return _TEMPLATES.TemplateResponse(
        request,
        "partials/search_results.html",
        {
            "results": results,
            "query": q,
            "act_type": act_type,
            "total": total,
            "page": page,
            "total_pages": total_pages,
        },
    )


@app.get("/partials/sejm-processes", response_class=HTMLResponse)
async def sejm_processes_partial(
    request: Request,
    query: str | None = None,
    page: int = Query(1, ge=1),
    session: AsyncSession = Depends(get_session),
):
    per_page = 20

    count_stmt = (
        select(func.count()).select_from(LegislativeProcess)
        .where(LegislativeProcess.document_type.notin_(_HIDDEN_DOC_TYPES))
    )
    if query:
        count_stmt = count_stmt.where(LegislativeProcess.title.ilike(f"%{query}%"))
    total = await session.scalar(count_stmt) or 0
    total_pages = max(1, math.ceil(total / per_page))
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    stmt = (
        select(LegislativeProcess)
        .where(LegislativeProcess.document_type.notin_(_HIDDEN_DOC_TYPES))
        .order_by(LegislativeProcess.process_start.desc().nullslast())
        .offset(offset)
        .limit(per_page)
    )
    if query:
        stmt = stmt.where(LegislativeProcess.title.ilike(f"%{query}%"))

    result = await session.execute(stmt)
    processes = result.scalars().all()

    return _TEMPLATES.TemplateResponse(
        request,
        "partials/sejm_processes.html",
        {
            "processes": processes,
            "query": query,
            "page": page,
            "total_pages": total_pages,
        },
    )


@app.get("/partials/sejm-votings", response_class=HTMLResponse)
async def sejm_votings_partial(
    request: Request,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = Query(1, ge=1),
    session: AsyncSession = Depends(get_session),
):
    per_page = 20

    count_stmt = select(func.count()).select_from(Voting).where(_voting_not_procedural())
    if date_from:
        count_stmt = count_stmt.where(Voting.date >= date_type.fromisoformat(date_from))
    if date_to:
        count_stmt = count_stmt.where(Voting.date <= date_type.fromisoformat(date_to))
    total = await session.scalar(count_stmt) or 0
    total_pages = max(1, math.ceil(total / per_page))
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    stmt = (
        select(Voting)
        .where(_voting_not_procedural())
        .order_by(Voting.date.desc())
        .offset(offset)
        .limit(per_page)
    )
    if date_from:
        stmt = stmt.where(Voting.date >= date_type.fromisoformat(date_from))
    if date_to:
        stmt = stmt.where(Voting.date <= date_type.fromisoformat(date_to))

    result = await session.execute(stmt)
    votings = result.scalars().all()

    return _TEMPLATES.TemplateResponse(
        request,
        "partials/sejm_votings.html",
        {
            "votings": votings,
            "date_from": date_from,
            "date_to": date_to,
            "page": page,
            "total_pages": total_pages,
        },
    )


@app.get("/partials/senat-processes", response_class=HTMLResponse)
async def senat_processes_partial(
    request: Request,
    query: str | None = None,
    page: int = Query(1, ge=1),
    session: AsyncSession = Depends(get_session),
):
    per_page = 20

    count_stmt = select(func.count()).select_from(SenatProcess)
    if query:
        count_stmt = count_stmt.where(SenatProcess.title.ilike(f"%{query}%"))
    total = await session.scalar(count_stmt) or 0
    total_pages = max(1, math.ceil(total / per_page))
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    stmt = (
        select(SenatProcess)
        .order_by(SenatProcess.updated_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    if query:
        stmt = stmt.where(SenatProcess.title.ilike(f"%{query}%"))

    result = await session.execute(stmt)
    senat_processes = result.scalars().all()

    return _TEMPLATES.TemplateResponse(
        request,
        "partials/senat_processes.html",
        {
            "senat_processes": senat_processes,
            "query": query,
            "page": page,
            "total_pages": total_pages,
        },
    )


# ---------------------------------------------------------------------------
# Install endpoint (unchanged)
# ---------------------------------------------------------------------------


@app.post("/api/install/{client}", response_class=HTMLResponse)
async def install_client(client: str):
    from polish_law_helper import installer

    fn = installer.CLIENTS.get(client)
    if not fn:
        return HTMLResponse(
            f'<span class="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800">Nieznany klient: {client}</span>'
        )
    try:
        msg = fn()
        return HTMLResponse(
            f'<span class="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">Zainstalowano</span>'
            f' <span class="text-gray-500 text-xs">{msg}</span>'
        )
    except Exception as e:
        return HTMLResponse(
            f'<span class="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800">Błąd</span>'
            f' <span class="text-red-600 text-xs">{e}</span>'
        )
