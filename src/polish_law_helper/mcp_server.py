"""MCP server for Lupa na prawo — primary interface for LLM interaction."""

from datetime import date, timedelta

from mcp.server.fastmcp import FastMCP
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from polish_law_helper.config import settings
from polish_law_helper.db.engine import async_session
from polish_law_helper.db.models import Act, Chunk, LegislativeProcess, PrintChunk, SejmPrint, SenatProcess, Voting
from polish_law_helper.embeddings.ollama_client import embedder

mcp = FastMCP(
    "Lupa na prawo",
    instructions=(
        "Search Polish law codexes semantically and track Sejm/Senat legislative activity. "
        "Use search_law for natural language queries about Polish law. "
        "Use get_article for specific article lookups. "
        "Use search_prints to find parliamentary bills (druki sejmowe) by topic. "
        "Use Sejm tools for parliamentary processes and voting records."
    ),
)


@mcp.tool()
async def search_law(
    query: str,
    limit: int = 10,
    act_type: str | None = None,
    keywords: str | None = None,
    precision: str = "fast",
) -> str:
    """Search Polish law using semantic similarity.

    Args:
        query: Natural language query in Polish or English (e.g. "odpowiedzialność za szkodę", "tenant eviction rights")
        limit: Max results to return (default 10)
        act_type: Filter by act type (e.g. "ustawa", "rozporządzenie")
        keywords: Comma-separated keywords to filter by
        precision: Search precision mode. "fast" uses ANN/HNSW index (default, good for most queries). "precise" uses higher ef_search for better recall at the cost of speed. "exact" disables the index entirely and does brute-force KNN (slowest but perfect recall — use only when fast/precise results seem incomplete).
    """
    query_embedding = await embedder.embed(query, prefix="query")

    async with async_session() as session:
        # Set search precision via pgvector's ef_search parameter
        if precision == "precise":
            await session.execute(text("SET LOCAL hnsw.ef_search = 200"))
        elif precision == "exact":
            await session.execute(text("SET LOCAL enable_indexscan = off"))

        embedding_col = Chunk.embedding
        distance = embedding_col.cosine_distance(query_embedding)

        stmt = (
            select(
                Chunk.article_num,
                Chunk.paragraph_num,
                Chunk.point_num,
                Chunk.text_content,
                Chunk.chapter_title,
                Chunk.chapter_num,
                Chunk.section_title,
                Chunk.title_name,
                Chunk.part_title,
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

        if keywords:
            kw_list = [k.strip() for k in keywords.split(",")]
            stmt = stmt.where(Act.keywords.overlap(kw_list))

        result = await session.execute(stmt)
        rows = result.all()

    if not rows:
        return "No results found. Try a different query or check if acts have been ingested."

    output = []
    for row in rows:
        hierarchy = _build_hierarchy(row)
        score = f"{row.score:.3f}" if row.score else "N/A"
        output.append(
            f"**{row.act_title}** — {hierarchy}\n"
            f"Score: {score} | ELI: {row.eli_id}\n"
            f"{row.text_content}\n"
        )

    return "\n---\n".join(output)


@mcp.tool()
async def get_article(
    eli_id: str,
    article_num: str,
) -> str:
    """Get the full text of a specific article from a law act.

    Args:
        eli_id: ELI identifier (e.g. "DU/1964/93" for Kodeks cywilny)
        article_num: Article number (e.g. "415", "1a")
    """
    async with async_session() as session:
        stmt = (
            select(Chunk, Act.title)
            .join(Act, Chunk.act_id == Act.id)
            .where(Act.eli_id == eli_id, Chunk.article_num == article_num)
            .order_by(Chunk.paragraph_num, Chunk.point_num)
        )
        result = await session.execute(stmt)
        rows = result.all()

    if not rows:
        return f"Article {article_num} not found in act {eli_id}. Check the ELI ID and article number."

    act_title = rows[0][1]
    parts = [f"**{act_title}** — Art. {article_num}\n"]

    for chunk, _ in rows:
        label = f"Art. {chunk.article_num}"
        if chunk.paragraph_num:
            label += f" § {chunk.paragraph_num}"
        if chunk.point_num:
            label += f" pkt {chunk.point_num}"
        parts.append(f"**{label}**: {chunk.text_content}")

    return "\n\n".join(parts)


@mcp.tool()
async def list_acts(
    act_type: str | None = None,
) -> str:
    """List all ingested law acts.

    Args:
        act_type: Optional filter (e.g. "ustawa", "rozporządzenie")
    """
    async with async_session() as session:
        stmt = select(Act.eli_id, Act.title, Act.act_type, Act.status).order_by(Act.title)
        if act_type:
            stmt = stmt.where(Act.act_type == act_type)
        result = await session.execute(stmt)
        rows = result.all()

    if not rows:
        return "No acts ingested yet. Run the ingestion pipeline first."

    lines = []
    for row in rows:
        lines.append(f"- **{row.title}** ({row.eli_id}) — {row.act_type or 'N/A'}, status: {row.status or 'N/A'}")
    return "\n".join(lines)


@mcp.tool()
async def get_act_info(eli_id: str) -> str:
    """Get detailed metadata about a law act including keywords and references.

    Args:
        eli_id: ELI identifier (e.g. "DU/1964/93")
    """
    async with async_session() as session:
        stmt = select(Act).where(Act.eli_id == eli_id)
        result = await session.execute(stmt)
        act = result.scalar_one_or_none()

        if not act:
            return f"Act {eli_id} not found. Use list_acts to see available acts."

        chunk_count = await session.scalar(
            select(func.count()).where(Chunk.act_id == act.id)
        )

    keywords = ", ".join(act.keywords) if act.keywords else "none"
    return (
        f"**{act.title}**\n\n"
        f"- ELI ID: {act.eli_id}\n"
        f"- Type: {act.act_type or 'N/A'}\n"
        f"- Status: {act.status or 'N/A'}\n"
        f"- In force: {act.in_force or 'N/A'}\n"
        f"- Announcement date: {act.announcement_date or 'N/A'}\n"
        f"- Entry into force: {act.entry_into_force or 'N/A'}\n"
        f"- Keywords: {keywords}\n"
        f"- Indexed chunks: {chunk_count}\n"
        f"- Last fetched: {act.fetched_at or 'N/A'}"
    )


@mcp.tool()
async def search_sejm_processes(
    query: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> str:
    """Search Sejm legislative processes.

    Args:
        query: Text search in process titles (optional)
        status: Filter by status - not yet implemented, returns all
        limit: Max results (default 20)
    """
    _hidden = ("wniosek", "wniosek (bez druku)")
    async with async_session() as session:
        stmt = (
            select(LegislativeProcess)
            .where(LegislativeProcess.document_type.notin_(_hidden))
            .order_by(LegislativeProcess.change_date.desc().nullslast())
            .limit(limit)
        )

        if query:
            stmt = stmt.where(LegislativeProcess.title.ilike(f"%{query}%"))

        result = await session.execute(stmt)
        processes = result.scalars().all()

    if not processes:
        return "No processes found. Run Sejm ingestion first or try a different query."

    lines = []
    for p in processes:
        stages = ""
        if p.raw_json and "stages" in p.raw_json:
            stage_names = [s.get("stageName", "") for s in p.raw_json["stages"]]
            stages = f" | Stages: {' → '.join(stage_names[-3:])}"
        lines.append(
            f"- **[{p.process_number}]** {p.title}\n"
            f"  Type: {p.document_type or 'N/A'} | "
            f"Start: {p.process_start or 'N/A'}{stages}"
        )

    return "\n".join(lines)


@mcp.tool()
async def get_process_details(
    process_number: str,
) -> str:
    """Get full details of a Sejm legislative process including all stages.

    Args:
        process_number: Process number (e.g. "1", "234")
    """
    async with async_session() as session:
        stmt = select(LegislativeProcess).where(
            LegislativeProcess.term == settings.sejm_term,
            LegislativeProcess.process_number == process_number,
        )
        result = await session.execute(stmt)
        proc = result.scalar_one_or_none()

    if not proc:
        return f"Process {process_number} not found for term {settings.sejm_term}."

    output = [
        f"**{proc.title}**\n",
        f"- Process number: {proc.process_number}",
        f"- Term: {proc.term}",
        f"- Document type: {proc.document_type or 'N/A'}",
        f"- Process start: {proc.process_start or 'N/A'}",
        f"- Last change: {proc.change_date or 'N/A'}",
    ]

    if proc.raw_json:
        # Print stages
        stages = proc.raw_json.get("stages", [])
        if stages:
            output.append(f"\n**Legislative stages ({len(stages)}):**")
            for i, stage in enumerate(stages, 1):
                stage_name = stage.get("stageName", "Unknown")
                stage_date = stage.get("date", "N/A")
                output.append(f"  {i}. {stage_name} ({stage_date})")

        # Print related prints
        prints = proc.raw_json.get("prints", proc.raw_json.get("printNumbers", []))
        if prints:
            output.append(f"\n**Related prints:** {', '.join(str(p) for p in prints)}")

    return "\n".join(output)


@mcp.tool()
async def search_votings(
    query: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
) -> str:
    """Search Sejm voting records.

    Args:
        query: Text search in voting titles (optional)
        date_from: Start date YYYY-MM-DD (optional)
        date_to: End date YYYY-MM-DD (optional)
        limit: Max results (default 20)
    """
    async with async_session() as session:
        # Exclude procedural/agenda votings
        stmt = (
            select(Voting)
            .where(
                Voting.title.notilike("%proceduraln%"),
                Voting.title.notilike("%posiedzenie sejmu%"),
            )
            .order_by(Voting.date.desc())
            .limit(limit)
        )

        if query:
            stmt = stmt.where(Voting.title.ilike(f"%{query}%"))
        if date_from:
            stmt = stmt.where(Voting.date >= date.fromisoformat(date_from))
        if date_to:
            stmt = stmt.where(Voting.date <= date.fromisoformat(date_to))

        result = await session.execute(stmt)
        votings = result.scalars().all()

    if not votings:
        return "No votings found. Run Sejm ingestion or try a different query."

    lines = []
    for v in votings:
        result_str = v.result or "N/A"
        counts = f"Yes: {v.yes_count}, No: {v.no_count}, Abstain: {v.abstain_count}"
        lines.append(
            f"- **{v.title}**\n"
            f"  Date: {v.date} | Sitting {v.sitting}, Vote #{v.voting_number} | "
            f"Result: {result_str} | {counts}"
        )

    return "\n".join(lines)


@mcp.tool()
async def search_prints(
    query: str,
    limit: int = 10,
) -> str:
    """Search parliamentary prints (druki sejmowe) using semantic similarity.

    Use this to find bills being debated that match a topic.

    Args:
        query: Natural language query in Polish or English
        limit: Max results to return (default 10)
    """
    query_embedding = await embedder.embed(query, prefix="query")

    async with async_session() as session:
        distance = PrintChunk.embedding.cosine_distance(query_embedding)

        stmt = (
            select(
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

    if not rows:
        return "No results found. Try a different query or check if prints have been ingested."

    output = []
    for row in rows:
        score = f"{row.score:.3f}" if row.score else "N/A"
        date_str = str(row.document_date) if row.document_date else "N/A"
        process_ref = f" | Process: {row.process_number}" if row.process_number else ""
        output.append(
            f"**{row.print_title}** — Print {row.print_number} (chunk {row.chunk_index})\n"
            f"Score: {score} | Date: {date_str} | Term: {row.term}{process_ref}\n"
            f"{row.text_content}\n"
        )

    return "\n---\n".join(output)


@mcp.tool()
async def search_senat(
    query: str | None = None,
    limit: int = 20,
) -> str:
    """Search Senat processes and decisions.

    Args:
        query: Text search in Senat process titles (optional)
        limit: Max results (default 20)
    """
    async with async_session() as session:
        stmt = (
            select(SenatProcess)
            .order_by(SenatProcess.updated_at.desc())
            .limit(limit)
        )

        if query:
            stmt = stmt.where(SenatProcess.title.ilike(f"%{query}%"))

        result = await session.execute(stmt)
        processes = result.scalars().all()

    if not processes:
        return "No Senat processes found. Run Senat ingestion first or try a different query."

    lines = []
    for p in processes:
        decision_str = f" | Decision: {p.decision}" if p.decision else ""
        decision_date_str = f" ({p.decision_date})" if p.decision_date else ""
        sejm_ref = f" | Sejm process: {p.sejm_process_number}" if p.sejm_process_number else ""
        lines.append(
            f"- **[Print {p.print_number}]** {p.title}\n"
            f"  Term: {p.term}{sejm_ref}{decision_str}{decision_date_str}"
        )

    return "\n".join(lines)


@mcp.tool()
async def get_recent_activity(days: int = 7) -> str:
    """Get a summary of recent legislative activity.

    Args:
        days: How many days back to look (default 7)
    """
    since = date.today() - timedelta(days=days)

    async with async_session() as session:
        # Recent processes (exclude motions/wniosek)
        _hidden = ("wniosek", "wniosek (bez druku)")
        proc_stmt = (
            select(LegislativeProcess)
            .where(
                LegislativeProcess.change_date >= since,
                LegislativeProcess.document_type.notin_(_hidden),
            )
            .order_by(LegislativeProcess.change_date.desc())
            .limit(10)
        )
        proc_result = await session.execute(proc_stmt)
        processes = proc_result.scalars().all()

        # Recent votings (exclude procedural)
        vote_stmt = (
            select(Voting)
            .where(
                Voting.date >= since,
                Voting.title.notilike("%proceduraln%"),
                Voting.title.notilike("%posiedzenie sejmu%"),
            )
            .order_by(Voting.date.desc())
            .limit(10)
        )
        vote_result = await session.execute(vote_stmt)
        votings = vote_result.scalars().all()

        # Stats
        total_acts = await session.scalar(select(func.count()).select_from(Act))
        total_chunks = await session.scalar(select(func.count()).select_from(Chunk))

    output = [f"**Legislative Activity Summary (last {days} days)**\n"]
    output.append(f"Index: {total_acts} acts, {total_chunks} searchable chunks\n")

    if processes:
        output.append(f"**Recent Processes ({len(processes)}):**")
        for p in processes:
            output.append(f"- [{p.process_number}] {p.title[:100]}")
    else:
        output.append("No recent process updates.")

    output.append("")

    if votings:
        output.append(f"**Recent Votings ({len(votings)}):**")
        for v in votings:
            result_str = v.result or "N/A"
            output.append(f"- {v.date}: {v.title[:100]} — {result_str}")
    else:
        output.append("No recent votings.")

    return "\n".join(output)


def _build_hierarchy(row) -> str:
    """Build a human-readable hierarchy string from a query result row."""
    parts = []
    if row.part_title:
        parts.append(row.part_title)
    if row.title_name:
        parts.append(row.title_name)
    if row.section_title:
        parts.append(row.section_title)
    if row.chapter_title:
        ch = f"Rozdz. {row.chapter_num}" if row.chapter_num else ""
        if row.chapter_title:
            ch += f" {row.chapter_title}" if ch else row.chapter_title
        parts.append(ch)

    art = f"Art. {row.article_num}"
    if row.paragraph_num:
        art += f" § {row.paragraph_num}"
    if row.point_num:
        art += f" pkt {row.point_num}"
    parts.append(art)

    return " > ".join(parts)


if __name__ == "__main__":
    mcp.run()
