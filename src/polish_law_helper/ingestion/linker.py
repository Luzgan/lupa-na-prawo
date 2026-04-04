"""Link legislative processes to published acts, and votings to processes."""

import re

from rich.console import Console
from sqlalchemy import select

from polish_law_helper.db.engine import async_session
from polish_law_helper.db.models import Act, LegislativeProcess, Voting

_console = Console()

# Pattern to match ELI-style references like "DU/2024/123" or "/eli/DU/2024/123"
_ELI_PATTERN = re.compile(r"(?:^|/eli/|/)((?:DU|MP)/\d{4}/\d+)")

# Common prefixes to strip for title normalisation
_TITLE_PREFIX_RE = re.compile(
    r"^(?:ustawa\s+z\s+dnia\s+\d{1,2}\s+\w+\s+\d{4}\s*(?:r\.?)?\s*(?:o|w\s+sprawie)?\s*)",
    re.IGNORECASE,
)

# Minimum length for a normalised title to be useful for matching
_MIN_TITLE_LEN = 10

# Minimum ratio of overlap required for a title match
_MATCH_THRESHOLD = 0.85


def _normalize_title(title: str) -> str:
    """Strip common prefixes, lowercase, collapse whitespace."""
    text = title.lower().strip()
    text = _TITLE_PREFIX_RE.sub("", text)
    # Also strip leading "projekt ustawy o" / "projekt ustawy w sprawie"
    text = re.sub(
        r"^(?:projekt\s+ustawy\s*(?:o|w\s+sprawie)?\s*)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _try_extract_eli_from_json(raw_json: dict | None) -> str | None:
    """Scan the process JSON for ELI references."""
    if not raw_json:
        return None

    try:
        # Direct ELI field
        for key in ("eli", "ELI", "actEli", "act_eli"):
            val = raw_json.get(key)
            if val and isinstance(val, str):
                m = _ELI_PATTERN.search(val)
                if m:
                    return m.group(1)

        # Look inside stages for Dziennik Ustaw publication references
        stages = raw_json.get("stages") or raw_json.get("processStages") or []
        if isinstance(stages, list):
            for stage in stages:
                if not isinstance(stage, dict):
                    continue
                # Some stages have a reference to the published act
                for field in ("eli", "ELI", "comment", "description", "result"):
                    val = stage.get(field)
                    if val and isinstance(val, str):
                        m = _ELI_PATTERN.search(val)
                        if m:
                            return m.group(1)

        # Look in prints for ELI references
        prints_data = raw_json.get("prints") or raw_json.get("printNumbers") or []
        if isinstance(prints_data, list):
            for item in prints_data:
                if isinstance(item, str):
                    m = _ELI_PATTERN.search(item)
                    if m:
                        return m.group(1)
                elif isinstance(item, dict):
                    for field in ("eli", "ELI"):
                        val = item.get(field)
                        if val and isinstance(val, str):
                            m = _ELI_PATTERN.search(val)
                            if m:
                                return m.group(1)

        # Check top-level string values as a last resort
        for key in ("documentReference", "rpiPrint", "rcl"):
            val = raw_json.get(key)
            if val and isinstance(val, str):
                m = _ELI_PATTERN.search(val)
                if m:
                    return m.group(1)

    except Exception:
        # Never crash on unexpected JSON structure
        pass

    return None


def _try_match_by_title(
    process_title: str, acts: dict[str, str]
) -> str | None:
    """Conservative fuzzy title matching.

    Returns the ELI ID of the best matching act, or None.
    We require that the normalised process title is substantially contained
    within the normalised act title (or vice-versa) to avoid false positives.
    """
    norm_proc = _normalize_title(process_title)
    if len(norm_proc) < _MIN_TITLE_LEN:
        return None

    best_eli: str | None = None
    best_score: float = 0.0

    for eli_id, act_title in acts.items():
        norm_act = _normalize_title(act_title)
        if len(norm_act) < _MIN_TITLE_LEN:
            continue

        # Check containment in both directions
        if norm_proc in norm_act or norm_act in norm_proc:
            # Compute similarity as ratio of shorter / longer
            shorter = min(len(norm_proc), len(norm_act))
            longer = max(len(norm_proc), len(norm_act))
            score = shorter / longer
            if score > best_score and score >= _MATCH_THRESHOLD:
                best_score = score
                best_eli = eli_id

    return best_eli


async def link_processes_to_acts() -> int:
    """
    Find unlinked legislative processes and try to match them to published acts.
    Returns the number of newly linked processes.
    """
    linked = 0

    try:
        async with async_session() as session:
            # Get unlinked processes
            stmt = select(LegislativeProcess).where(
                LegislativeProcess.related_act_eli.is_(None)
            )
            result = await session.execute(stmt)
            processes = result.scalars().all()

            if not processes:
                _console.print("[dim]No unlinked processes found.[/dim]")
                return 0

            _console.print(
                f"[bold]Checking {len(processes)} unlinked process(es) for act matches...[/bold]"
            )

            # Get all acts for matching
            acts_result = await session.execute(select(Act.eli_id, Act.title))
            acts = {row.eli_id: row.title for row in acts_result.all()}

            if not acts:
                _console.print("[dim]No acts in database to match against.[/dim]")
                return 0

            for proc in processes:
                try:
                    # Approach 1: ELI reference in process JSON
                    eli_id = _try_extract_eli_from_json(proc.raw_json)
                    if eli_id and eli_id in acts:
                        proc.related_act_eli = eli_id
                        linked += 1
                        _console.print(
                            f"  [green]Linked[/green] process {proc.process_number} "
                            f"-> {eli_id} (from JSON)"
                        )
                        continue

                    # Approach 2: Title similarity
                    matched_eli = _try_match_by_title(proc.title, acts)
                    if matched_eli:
                        proc.related_act_eli = matched_eli
                        linked += 1
                        _console.print(
                            f"  [green]Linked[/green] process {proc.process_number} "
                            f"-> {matched_eli} (title match)"
                        )
                        continue

                except Exception as exc:
                    _console.print(
                        f"  [yellow]Error matching process {proc.process_number}: {exc}[/yellow]"
                    )

            await session.commit()

    except Exception as exc:
        _console.print(f"[bold red]Linking failed: {exc}[/bold red]")
        return linked

    _console.print(f"[bold green]Linked {linked} process(es) to acts.[/bold green]")
    return linked


# Pattern to extract druk numbers from voting title (handles druk/druku/druki/druków)
_DRUK_PATTERN = re.compile(r"druk(?:u|i|ów|ach)?\s*(?:nr\s*)?(\d+[a-zA-Z]?)", re.IGNORECASE)
# Pattern to find all druk numbers in a string (including "druki nr 123 i 456")
_ALL_DRUKS_PATTERN = re.compile(r"(?:druk(?:u|i|ów|ach)?\s*(?:nr\s*)?)?(\d{2,}[a-zA-Z]?)", re.IGNORECASE)


async def link_votings_to_processes() -> int:
    """Link votings to legislative processes via druk numbers in titles.

    Extracts druk numbers from voting titles, then finds which process
    has that druk in its raw_json.printNumbers. Returns count of newly linked.
    """
    linked = 0

    try:
        async with async_session() as session:
            # Get unlinked votings
            result = await session.execute(
                select(Voting).where(Voting.process_id.is_(None))
            )
            unlinked = result.scalars().all()

            if not unlinked:
                _console.print("[dim]All votings are already linked.[/dim]")
                return 0

            _console.print(
                f"[bold]Linking {len(unlinked)} voting(s) to legislative processes...[/bold]"
            )

            # Build druk->process_id lookup from all processes
            proc_result = await session.execute(
                select(
                    LegislativeProcess.id,
                    LegislativeProcess.process_number,
                    LegislativeProcess.raw_json,
                )
            )
            druk_to_process: dict[str, object] = {}
            for proc_id, proc_num, rj in proc_result.all():
                if not rj:
                    continue
                # Top-level print numbers (sometimes present)
                print_numbers = rj.get("printNumbers") or rj.get("prints") or []
                for pn in print_numbers:
                    druk_to_process[str(pn)] = proc_id
                # Extract print numbers from stages (more reliable)
                for stage in rj.get("stages") or []:
                    pn = stage.get("printNumber")
                    if pn:
                        druk_to_process[str(pn)] = proc_id
                    # Some stages list multiple prints
                    for pn in stage.get("printNumbers") or []:
                        druk_to_process[str(pn)] = proc_id
                # Also index the process number itself
                druk_to_process[str(proc_num)] = proc_id

            _console.print(f"  Found {len(druk_to_process)} print-to-process mappings")

            for voting in unlinked:
                matched = False
                # Search title and topic for druk references
                for text in [voting.title or "", (voting.raw_json or {}).get("topic", "")]:
                    if matched:
                        break
                    # Find the first druk-like pattern to anchor, then extract all numbers nearby
                    anchor = _DRUK_PATTERN.search(text)
                    if anchor:
                        # Extract all numbers from the surrounding context
                        for num_match in _ALL_DRUKS_PATTERN.finditer(text[anchor.start():]):
                            druk_num = num_match.group(1)
                            proc_id = druk_to_process.get(druk_num)
                            if proc_id:
                                voting.process_id = proc_id
                                linked += 1
                                matched = True
                                break

            await session.commit()

    except Exception as exc:
        _console.print(f"[bold red]Voting linking failed: {exc}[/bold red]")
        return linked

    _console.print(f"[bold green]Linked {linked} voting(s) to processes.[/bold green]")
    return linked
