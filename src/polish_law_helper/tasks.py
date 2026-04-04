"""Background ingestion task state and runner functions."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class IngestionState:
    running: bool = False
    job_type: str = ""
    started_at: Optional[datetime] = None
    progress: str = ""
    last_completed: Optional[datetime] = None
    last_error: Optional[str] = None


# Module-level singleton — shared across the server process
state = IngestionState()


async def run_ingest_acts(force: bool = False) -> None:
    """Wrapper that tracks ingestion state while running ingest_acts."""
    from polish_law_helper.ingestion.ingest_acts import ingest_acts

    state.running = True
    state.job_type = "acts"
    state.started_at = datetime.now(timezone.utc)
    state.progress = "Ingesting law acts…"
    state.last_error = None
    try:
        await ingest_acts(force=force)
        state.last_completed = datetime.now(timezone.utc)
        state.progress = ""
    except Exception as e:
        state.last_error = str(e)
        state.progress = ""
    finally:
        state.running = False


async def run_ingest_prints(since_days: int = 30, force: bool = False) -> None:
    """Wrapper that tracks ingestion state while running ingest_prints."""
    from polish_law_helper.ingestion.ingest_prints import ingest_prints

    state.running = True
    state.job_type = "prints"
    state.started_at = datetime.now(timezone.utc)
    state.progress = "Ingesting Sejm prints\u2026"
    state.last_error = None
    try:
        await ingest_prints(since_days=since_days, force=force)
        state.last_completed = datetime.now(timezone.utc)
        state.progress = ""
    except Exception as e:
        state.last_error = str(e)
        state.progress = ""
    finally:
        state.running = False


async def run_ingest_senat() -> None:
    """Wrapper that tracks ingestion state while running ingest_senat."""
    from polish_law_helper.ingestion.ingest_senat import ingest_senat

    state.running = True
    state.job_type = "senat"
    state.started_at = datetime.now(timezone.utc)
    state.progress = "Ingesting Senat data\u2026"
    state.last_error = None
    try:
        await ingest_senat()
        state.last_completed = datetime.now(timezone.utc)
        state.progress = ""
    except Exception as e:
        state.last_error = str(e)
        state.progress = ""
    finally:
        state.running = False


async def run_ingest_sejm(since_days: int = 30) -> None:
    """Wrapper that tracks ingestion state while running ingest_sejm."""
    from polish_law_helper.ingestion.ingest_sejm import ingest_sejm

    state.running = True
    state.job_type = "sejm"
    state.started_at = datetime.now(timezone.utc)
    state.progress = "Ingesting Sejm data…"
    state.last_error = None
    try:
        await ingest_sejm(since_days=since_days)
        state.last_completed = datetime.now(timezone.utc)
        state.progress = ""
    except Exception as e:
        state.last_error = str(e)
        state.progress = ""
    finally:
        state.running = False
