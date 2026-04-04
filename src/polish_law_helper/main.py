"""FastAPI REST routes — exposed as a router so server.py can include them."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from polish_law_helper.db.engine import get_session
from polish_law_helper.db.models import Act, Chunk, LegislativeProcess, PrintChunk, SejmPrint, Voting
from polish_law_helper.embeddings.ollama_client import embedder

# Router used by server.py (and included in the standalone app below)
router = APIRouter()

# Standalone app (kept for backward-compat / direct uvicorn use)
app = FastAPI(title="Lupa na prawo", version="0.1.0")
app.include_router(router)


@router.get("/api/health")
async def health():
    return {"status": "ok"}


@router.get("/api/stats")
async def stats(session: AsyncSession = Depends(get_session)):
    acts = await session.scalar(select(func.count()).select_from(Act))
    chunks = await session.scalar(select(func.count()).select_from(Chunk))
    processes = await session.scalar(select(func.count()).select_from(LegislativeProcess))
    votings = await session.scalar(select(func.count()).select_from(Voting))
    prints = await session.scalar(select(func.count()).select_from(SejmPrint))
    print_chunks = await session.scalar(select(func.count()).select_from(PrintChunk))
    return {
        "acts": acts,
        "chunks": chunks,
        "processes": processes,
        "votings": votings,
        "prints": prints,
        "print_chunks": print_chunks,
    }


@router.get("/api/search")
async def search(
    q: str,
    limit: int = Query(10, le=50),
    act_type: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    query_embedding = await embedder.embed(q, prefix="query")
    distance = Chunk.embedding.cosine_distance(query_embedding)

    stmt = (
        select(
            Chunk.id,
            Chunk.article_num,
            Chunk.paragraph_num,
            Chunk.text_content,
            Chunk.chapter_title,
            Act.title.label("act_title"),
            Act.eli_id,
            (1 - distance).label("score"),
        )
        .join(Act, Chunk.act_id == Act.id)
        .order_by(distance)
        .limit(limit)
    )

    if act_type:
        stmt = stmt.where(Act.act_type == act_type)

    result = await session.execute(stmt)
    rows = result.all()

    return {
        "query": q,
        "results": [
            {
                "chunk_id": str(r.id),
                "act_title": r.act_title,
                "eli_id": r.eli_id,
                "article_num": r.article_num,
                "paragraph_num": r.paragraph_num,
                "chapter": r.chapter_title,
                "text": r.text_content,
                "score": round(r.score, 4) if r.score else None,
            }
            for r in rows
        ],
    }


@router.get("/api/search-prints")
async def search_prints(
    q: str,
    limit: int = Query(10, le=50),
    session: AsyncSession = Depends(get_session),
):
    query_embedding = await embedder.embed(q, prefix="query")
    distance = PrintChunk.embedding.cosine_distance(query_embedding)

    stmt = (
        select(
            PrintChunk.id,
            PrintChunk.chunk_index,
            PrintChunk.text_content,
            SejmPrint.title.label("print_title"),
            SejmPrint.print_number,
            SejmPrint.term,
            SejmPrint.document_date,
            SejmPrint.process_number,
            (1 - distance).label("score"),
        )
        .join(SejmPrint, PrintChunk.print_id == SejmPrint.id)
        .order_by(distance)
        .limit(limit)
    )

    result = await session.execute(stmt)
    rows = result.all()

    return {
        "query": q,
        "results": [
            {
                "chunk_id": str(r.id),
                "print_title": r.print_title,
                "print_number": r.print_number,
                "term": r.term,
                "chunk_index": r.chunk_index,
                "document_date": str(r.document_date) if r.document_date else None,
                "process_number": r.process_number,
                "text": r.text_content,
                "score": round(r.score, 4) if r.score else None,
            }
            for r in rows
        ],
    }


@router.get("/api/acts")
async def list_acts(
    act_type: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Act.id, Act.eli_id, Act.title, Act.act_type, Act.status).order_by(Act.title)
    if act_type:
        stmt = stmt.where(Act.act_type == act_type)
    result = await session.execute(stmt)
    return [
        {"id": str(r.id), "eli_id": r.eli_id, "title": r.title, "type": r.act_type, "status": r.status}
        for r in result.all()
    ]


@router.get("/api/acts/{eli_id:path}")
async def get_act(eli_id: str, session: AsyncSession = Depends(get_session)):
    act = (await session.execute(select(Act).where(Act.eli_id == eli_id))).scalar_one_or_none()
    if not act:
        return {"error": "Act not found"}
    chunk_count = await session.scalar(select(func.count()).where(Chunk.act_id == act.id))
    return {
        "eli_id": act.eli_id,
        "title": act.title,
        "type": act.act_type,
        "status": act.status,
        "keywords": act.keywords,
        "chunks": chunk_count,
    }


@router.get("/api/legislative/processes")
async def list_processes(
    query: str | None = None,
    limit: int = Query(20, le=100),
    session: AsyncSession = Depends(get_session),
):
    _hidden = ("wniosek", "wniosek (bez druku)")
    stmt = (
        select(LegislativeProcess)
        .where(LegislativeProcess.document_type.notin_(_hidden))
        .order_by(LegislativeProcess.change_date.desc().nullslast())
        .limit(limit)
    )
    if query:
        stmt = stmt.where(LegislativeProcess.title.ilike(f"%{query}%"))
    result = await session.execute(stmt)
    return [
        {
            "process_number": p.process_number,
            "title": p.title,
            "document_type": p.document_type,
            "process_start": str(p.process_start) if p.process_start else None,
        }
        for p in result.scalars().all()
    ]


@router.get("/api/legislative/votings")
async def list_votings(
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(20, le=100),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(Voting)
        .where(
            Voting.title.notilike("%proceduraln%"),
            Voting.title.notilike("%posiedzenie sejmu%"),
        )
        .order_by(Voting.date.desc())
        .limit(limit)
    )
    if date_from:
        stmt = stmt.where(Voting.date >= date.fromisoformat(date_from))
    if date_to:
        stmt = stmt.where(Voting.date <= date.fromisoformat(date_to))
    result = await session.execute(stmt)
    return [
        {
            "sitting": v.sitting,
            "voting_number": v.voting_number,
            "title": v.title,
            "date": str(v.date),
            "result": v.result,
            "yes": v.yes_count,
            "no": v.no_count,
            "abstain": v.abstain_count,
        }
        for v in result.scalars().all()
    ]
