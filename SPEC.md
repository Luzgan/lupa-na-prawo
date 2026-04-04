# Polish Law Helper

> Make Polish law more accessible for every user.

## Overview

Polish Law Helper is a semantic search and legislative tracking platform. It lets users search through all current Polish laws using natural language, follow what is being debated and voted on in the Sejm, and connect their own AI agents to the legal database via MCP.

## Components

1. **Frontend** — a Polish-language web interface for searching current laws, browsing Sejm debates, and viewing voting records. Includes a guide explaining how to set up MCP for use with personal AI assistants.
2. **Backend** — a REST API exposing semantic search over current laws and legislative proceedings.
3. **MCP server** — allows users to connect any MCP-compatible AI agent to the database and ask questions about Polish law directly from their chat.
4. **Ingestion pipeline** — a scheduled job that collects and indexes data from the Sejm and Senat APIs.

The backend runs an initial ingestion of all current laws on first startup if the database is empty. A nightly cron job then keeps the data up to date by collecting the latest proceedings, votings, and any newly published legislation.

---

## Technical choices

### Language & runtime
- **Python 3.12+**
- Package manager: **uv** (with `pyproject.toml` + `uv.lock`)
- Build backend: **Hatchling**

### Backend framework
- **FastAPI** with **Uvicorn** (ASGI)
- Jinja2 templates for the server-side dashboard
- CLI via **Typer** + **Rich** (entry point: `plh`)

### Database
- **PostgreSQL 17** with **pgvector** extension (via `pgvector/pgvector:pg17` Docker image)
- ORM: **SQLAlchemy 2.0** (async, using `asyncpg` driver)
- Migrations: **Alembic**
- Vector index: **HNSW** with `vector_cosine_ops` (m=16, ef_construction=64)
- Embedding dimension: **1024**

### Database schema
- **acts** — law act metadata (ELI ID, title, type, status, keywords, dates)
- **chunks** — article/paragraph/point text with full hierarchy metadata + embedding vector
- **act_references** — cross-references between acts
- **legislative_processes** — Sejm legislative processes with stages (raw JSON stored)
- **votings** — Sejm voting records (yes/no/abstain counts, linked to processes)
- **ingestion_log** — tracking ingestion runs and their status

### Embeddings
- **Ollama** (self-hosted, running in Docker on port 11434)
- Model: **`jeffh/intfloat-multilingual-e5-large:f16`** (multilingual E5-large, 1024-dim)
- Prefix convention: `"query: "` for search queries, `"passage: "` for indexed text
- Batch embedding support with configurable batch size (default 32)

### Data sources
- **ELI API** (`api.sejm.gov.pl/eli`) — law act metadata, full HTML text, cross-references
- **Sejm API** (`api.sejm.gov.pl/sejm`) — legislative processes, votings, prints, proceedings, MPs, clubs, committees
- Current Sejm term: **10**
- HTTP client: **httpx** (async)

### Ingestion pipeline
- HTML parsing: **BeautifulSoup4** + **lxml**
- Custom HTML parser that extracts legal structure hierarchy (Part > Title > Section > Chapter > Article > Paragraph > Point)
- Chunking strategy: article-level by default, splits into paragraphs at 1500 chars, into points at 2000 chars
- Each chunk stores full hierarchy context in `text_for_embedding` for better retrieval
- Priority codexes pre-defined (Kodeks cywilny, karny, pracy, KPC, KPK, KSH, KRO, KPA, KW, Konstytucja, Ordynacja podatkowa, PPSA)
- Supports ingesting all in-force Polish laws (~8-15k acts)

### MCP server
- Built with **FastMCP** (`mcp[cli]` package)
- Transport: stdio (default) or streamable-http (mounted at `/mcp` on the FastAPI app)
- Tools exposed:
  - `search_law` — semantic search with optional act_type and keyword filters
  - `get_article` — fetch specific article by ELI ID + article number
  - `list_acts` — list all ingested acts
  - `get_act_info` — detailed act metadata
  - `search_sejm_processes` — search legislative processes
  - `get_process_details` — full process with stages
  - `search_votings` — search voting records by query/date
  - `get_recent_activity` — summary of recent legislative activity

### REST API endpoints
- `GET /api/health` — health check
- `GET /api/stats` — database statistics
- `GET /api/search?q=...` — semantic search over law chunks
- `GET /api/acts` — list acts (optional type filter)
- `GET /api/acts/{eli_id}` — act detail
- `GET /api/legislative/processes` — legislative processes
- `GET /api/legislative/votings` — voting records
- `POST /api/install/{client}` — one-click MCP client installation

### Infrastructure
- **Docker Compose** for local dev (PostgreSQL + Ollama)
- Server runs on port **8765**
- All config via environment variables with `PLH_` prefix (using pydantic-settings)
