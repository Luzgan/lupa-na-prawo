# Frontend Implementation Plan

## Architecture Decision

**Jinja2 templates + HTMX + Tailwind CSS** — keep server-side rendering (no SPA), match the Stitch design direction.

- Tailwind CSS via CDN with custom config (matching Stitch designs)
- HTMX for interactive updates only (pagination, filters, tab switching) — NOT for initial page content
- Google Fonts: Manrope (headlines) + Inter (body)
- Material Symbols Outlined icons

### SEO Requirement

Pages must be fully indexable by search engines. All critical content must be rendered server-side in the initial HTML response. HTMX should only be used for user-initiated interactions (clicking pagination, switching tabs, submitting filters), never for initial content loading via `hx-trigger="load"`.

**Pattern:** Page routes in server.py must query data and pass it to the template context. Templates render the data inline. HTMX partials are then used for subsequent updates (next page, filter change, tab click).
- Color scheme from Stitch: primary `#031636` (navy), secondary `#ba002e` (red), tertiary `#241300` (brown)

## Pages

### 1. Landing Page (`/`)
**Template:** `templates/index.html` (replace existing)
- Nav bar: logo "Pomocnik Prawny", links: "Szukaj w prawie", "Sejm", "MCP", optional login placeholder
- Hero section: gradient background, headline about accessible Polish law, prominent search bar
- "Aktualne ustawy" section: 3-4 cards showing recently indexed laws (title, type, date) — loaded via HTMX from `/partials/recent-acts`
- "Na żywo z Sejmu" section: latest Sejm processes and recent votings summary — loaded via HTMX from `/partials/sejm-live`
- CTA section: "Połącz ze swoim Agentem AI" linking to MCP guide
- Footer: copyright, links (Regulamin, Polityka Prywatności, Kontakt)
- Database stats shown subtly (acts count, chunks count, etc.)

### 2. Search Results Page (`/szukaj`)
**Template:** `templates/search.html`
- Search bar at top (pre-filled with query `q`)
- Results loaded via HTMX from `/partials/search-results?q=...&page=...&act_type=...`
- Each result card: act title, article/paragraph reference, text excerpt (highlighted), relevance score badge, law type tag
- Sidebar filters: act type dropdown (ustawa, rozporządzenie, etc.), loaded from distinct types in DB
- Pagination at bottom (HTMX-powered, 10 results per page)
- Empty state: friendly message when no results

### 3. Sejm Page (`/sejm`)
**Template:** `templates/sejm.html`
- Two tabs: "Procesy legislacyjne" and "Głosowania" — tab switching via HTMX
- **Processes tab** (`/partials/sejm-processes?page=...&query=...`):
  - Search bar for filtering processes
  - Cards showing: process number, title, document type, start date, latest stage name
  - Pagination
- **Votings tab** (`/partials/sejm-votings?page=...&date_from=...&date_to=...`):
  - Date range filter
  - Cards showing: title, date, sitting info, result badge (przyjęto/odrzucono)
  - Vote bar: horizontal stacked bar showing yes (green) / no (red) / abstain (gray) proportions
  - Pagination

### 4. MCP Guide Page (`/mcp-poradnik`)
**Template:** `templates/mcp_guide.html`
- Title: "Jak podłączyć MCP do swojego asystenta AI"
- Step-by-step cards (numbered 1-3):
  1. "Uruchom serwer" — command to start the server (`plh serve`)
  2. "Skonfiguruj klienta" — per-client config with copyable code blocks + one-click install buttons (reuse existing `/api/install/{client}` endpoints via HTMX)
  3. "Zacznij korzystać" — example prompts to try
- Supported clients table (Claude Desktop, Claude Code, Cursor, Windsurf, VS Code) with install buttons
- ChatGPT section (Custom Action via OpenAPI)
- FAQ accordion at bottom (what is MCP, is it free, does it work offline, etc.)

## Backend Changes

### New routes in `server.py`:
```python
GET /szukaj              → renders search.html
GET /sejm                → renders sejm.html
GET /mcp-poradnik        → renders mcp_guide.html
```

### New HTMX partials in `server.py`:
```python
GET /partials/recent-acts          → 4 most recently ingested acts as cards
GET /partials/sejm-live            → latest 3 processes + 3 votings summary
GET /partials/search-results       → paginated search results (q, page, act_type)
GET /partials/sejm-processes       → paginated processes (query, page)
GET /partials/sejm-votings         → paginated votings (date_from, date_to, page)
```

### Pagination:
- Add `offset` parameter to existing API logic (offset = (page-1) * per_page)
- Return total count for pagination controls
- Default 10 items per page for search, 20 for Sejm data

## Template Structure

```
templates/
  base.html            ← shared layout: head, nav, footer, Tailwind config
  index.html           ← landing page (extends base)
  search.html          ← search page (extends base)
  sejm.html            ← sejm page (extends base)
  mcp_guide.html       ← MCP guide (extends base)
  partials/
    stats.html         ← DB stats (update existing)
    recent_acts.html   ← recent acts cards
    sejm_live.html     ← sejm live summary
    search_results.html ← search result cards + pagination
    sejm_processes.html ← process list + pagination
    sejm_votings.html  ← voting list + pagination
```

## Styling Notes (from Stitch)

- Hero gradient: `radial-gradient` overlay using secondary/primary at reduced opacity
- CTA buttons: `linear-gradient(135deg, #ba002e 0%, #e31c40 100%)` with white text
- Cards: white bg, subtle shadow, rounded corners (0.5rem)
- Status badges: colored pills (green for active, gray for closed)
- Vote bars: horizontal stacked bar with rounded ends
- All UI text in Polish
- Responsive: works on mobile (single column) and desktop (sidebar filters on search page)

## Startup Validation & Auto-Ingestion

On server startup, check if the database has been populated. If data is missing, trigger ingestion automatically in the background.

### Implementation

**config.py** — add one new setting:
```python
skip_startup_ingest: bool = False  # PLH_SKIP_STARTUP_INGEST=true to disable
```

**server.py** — add a FastAPI lifespan context manager:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.skip_startup_ingest:
        asyncio.create_task(startup_check())
    yield
```

**server.py** — add `startup_check()` function:
1. Query count of Acts in DB
2. Check how many of the 12 PRIORITY_ACTS exist (by eli_id)
3. Query count of LegislativeProcesses and most recent Voting date
4. Decision logic:
   - If 0 acts → run full `run_ingest_acts()` (priority codexes)
   - If some priority acts missing → run `run_ingest_acts(eli_ids=missing_ids)`
   - If 0 processes OR newest voting is older than 7 days → run `run_ingest_sejm(since_days=30)`
5. Log to console via Rich what it's doing
6. Runs as background task — server starts immediately, doesn't block requests

### Key constraints:
- Use `asyncio.create_task()` so it doesn't block server startup
- Reuse existing `run_ingest_acts()` and `run_ingest_sejm()` from tasks.py
- Import PRIORITY_ACTS from `ingestion.eli_client`
- All ingestion is idempotent (acts check HTML hash, Sejm uses upserts)

### File changes:
| Action | File |
|--------|------|
| Modify | `config.py` — add `skip_startup_ingest` setting |
| Modify | `server.py` — add lifespan + startup_check function |
| No change | `tasks.py`, ingestion code, models, templates |

## Feature: Nightly Cron Job

Keep the database up to date by running a nightly sync that detects new/amended laws and refreshes Sejm data.

### Implementation

**New file: `src/polish_law_helper/scheduler.py`**

Use APScheduler (async) to run jobs inside the server process. No external cron dependency.

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
```

Jobs:
1. **`nightly_sync_acts()`** — calls `eli_client.get_changes_since(yesterday)`, then ingests any new/changed acts
2. **`nightly_sync_sejm()`** — calls `run_ingest_sejm(since_days=7)`

Schedule: both run daily at 03:00 (configurable via `PLH_CRON_HOUR` setting).

**config.py** — add:
```python
cron_hour: int = 3       # PLH_CRON_HOUR
cron_enabled: bool = True # PLH_CRON_ENABLED
```

**server.py** — start scheduler in lifespan:
```python
if settings.cron_enabled:
    scheduler.start()
# ... yield ...
scheduler.shutdown()
```

**cli.py** — add `plh sync` command for manual trigger of the same nightly logic.

**pyproject.toml** — add `apscheduler>=4.0` dependency.

### File changes:
| Action | File |
|--------|------|
| Create | `scheduler.py` |
| Modify | `config.py` — add cron_hour, cron_enabled |
| Modify | `server.py` — start/stop scheduler in lifespan |
| Modify | `cli.py` — add sync command |
| Modify | `pyproject.toml` — add apscheduler dep |

---

## Feature: Bill Text Ingestion (Druki Sejmowe)

Enable semantic search over parliamentary prints — the actual text of bills being debated.

### New model: `PrintChunk`

```python
class Print(Base):
    __tablename__ = "prints"
    id: UUID primary key
    term: int
    print_number: str (unique per term)
    title: str
    document_date: date
    process_number: str | None (FK to legislative_processes)
    pdf_url: str | None
    text_content: str  # extracted full text
    text_hash: str     # for change detection
    raw_json: JSONB
    created_at, updated_at: datetime

class PrintChunk(Base):
    __tablename__ = "print_chunks"
    id: UUID primary key
    print_id: UUID (FK to prints)
    chunk_index: int
    text_content: str
    text_for_embedding: str
    embedding: Vector(1024)
    char_count: int
    created_at: datetime
```

### Ingestion flow:
1. `SejmClient.get_prints()` — list prints (already exists)
2. For each print, fetch the PDF/HTML attachment
3. Extract text (PDF → text via a lightweight extractor, or use the HTML version if available)
4. Chunk the text (simpler than acts — just split by size, ~1500 chars with overlap)
5. Embed chunks and store

### Search integration:
- Add `search_prints` MCP tool and `/api/search-prints` endpoint
- Or extend existing `search_law` to search both acts AND prints (with a `source` filter)
- Frontend search page: add a toggle "Szukaj w: ☑ Ustawy ☑ Projekty ustaw"

### File changes:
| Action | File |
|--------|------|
| Modify | `db/models.py` — add Print, PrintChunk models |
| Create | `ingestion/ingest_prints.py` — print ingestion pipeline |
| Create | `ingestion/print_parser.py` — text extraction from print HTML/PDF |
| Modify | `mcp_server.py` — add search_prints tool |
| Modify | `main.py` — add /api/search-prints endpoint |
| Modify | `server.py` — add print search to frontend partials |
| Modify | `cli.py` — add `plh ingest-prints` command |
| Migration | new alembic migration for prints + print_chunks tables |

---

## Feature: Senat Tracking

Track bills through the Senat (upper chamber) after they pass the Sejm.

### Implementation:

**New file: `ingestion/senat_client.py`**

The Senat API is at `api.sejm.gov.pl/senat`. Similar structure to Sejm API.

Key endpoints:
- `/term{N}/proceedings` — Senat proceedings
- `/term{N}/votings` — Senat votings
- `/term{N}/prints` — Senat prints (bills received from Sejm)

**New model:**
```python
class SenatProcess(Base):
    __tablename__ = "senat_processes"
    id: UUID primary key
    term: int
    print_number: str
    title: str
    sejm_process_number: str | None  # link back to Sejm
    decision: str | None  # accepted/amended/rejected
    decision_date: date | None
    raw_json: JSONB
    created_at, updated_at: datetime
```

**config.py** — add `senat_base_url` and `senat_term` settings.

### File changes:
| Action | File |
|--------|------|
| Create | `ingestion/senat_client.py` |
| Create | `ingestion/ingest_senat.py` |
| Modify | `db/models.py` — add SenatProcess |
| Modify | `config.py` — add senat_base_url, senat_term |
| Modify | `mcp_server.py` — add senat search tools |
| Modify | `server.py` — add senat data to Sejm page |
| Modify | `cli.py` — add `plh ingest-senat` command |
| Modify | `scheduler.py` — add senat sync to nightly job |
| Migration | new alembic migration |

---

## Feature: Process → Act Linking

Connect legislative processes to resulting published acts.

### Implementation:

When a bill passes and gets published in Dziennik Ustaw, it gets an ELI ID. The nightly sync (via `get_changes_since`) picks it up as a new act.

**Linking logic** (in `scheduler.py` or a dedicated `linker.py`):
1. After ingesting new acts, check `legislative_processes` where `related_act_eli IS NULL`
2. For each unlinked process, compare the process title against newly ingested act titles (fuzzy match or check if the Sejm API's process JSON contains a reference to the ELI ID)
3. If match found, set `legislative_processes.related_act_eli = act.eli_id`

The `related_act_eli` field already exists in the model — it just needs to be populated.

**Alternative (simpler):** The Sejm API process JSON often contains print numbers and references. Check if the ELI API metadata for a new act references a Sejm process number, or vice versa.

### File changes:
| Action | File |
|--------|------|
| Create | `ingestion/linker.py` — process-to-act linking logic |
| Modify | `scheduler.py` — run linker after nightly sync |
| Modify | `cli.py` — add `plh link` command |

---

## Feature: Detail Pages (Clickable Items)

Every item shown in lists (acts, processes, votings, prints, Senat processes) must link to a dedicated detail page.

### Pages & Routes

#### 1. Act Detail Page — `GET /ustawa/{eli_id:path}`
**Template:** `templates/act_detail.html` (extends base)

Shows:
- Title, type, status, in-force badge
- Announcement date, entry into force date
- Keywords as tags
- ELI ID with link to official source
- **Table of contents** — list all chapters/sections with article ranges (from chunks)
- **Articles list** — grouped by chapter, each showing article number + text content
- Related legislative process (if linked via `related_act_eli`)
- Cross-references (from `act_references` table)
- Stats: number of articles/provisions indexed

Data: query Act + its Chunks (ordered by chapter, article, paragraph, point) + ActReferences where source_act_id matches.

#### 2. Legislative Process Detail Page — `GET /proces/{process_number}`
**Template:** `templates/process_detail.html` (extends base)

Shows:
- Title, process number, document type
- Start date, last change date
- Urgency status badge (if urgent)
- **Stages timeline** — extracted from `raw_json.stages`, shown as vertical timeline with dates
- Related prints (from `raw_json.prints` or `raw_json.printNumbers`)
- Related Senat process (if any, query SenatProcess by sejm_process_number)
- Linked act (if `related_act_eli` is set, link to act detail page)
- Related votings (query Voting where process_id matches)

Data: query LegislativeProcess by term + process_number, then related Votings and SenatProcess.

#### 3. Voting Detail Page — `GET /glosowanie/{sitting}/{voting_number}`
**Template:** `templates/voting_detail.html` (extends base)

Shows:
- Title, date, sitting number, voting number
- Result badge (przyjęto/odrzucono)
- **Vote bar** — large horizontal bar with yes/no/abstain
- Exact counts: Za, Przeciw, Wstrzymało się
- Link to parent legislative process (if process_id is set)
- Raw voting details (from `raw_json` if available — e.g., per-club breakdown)

Data: query Voting by term + sitting + voting_number.

#### 4. Print Detail Page — `GET /druk/{print_number}`
**Template:** `templates/print_detail.html` (extends base)

Shows:
- Title, print number, document date
- Related legislative process (link if process_number is set)
- **Full text** — rendered from `text_content` field on SejmPrint
- If no text available, show "Tekst niedostępny" with link to original on Sejm website
- Attachment link (if `attachment_url` is set)

Data: query SejmPrint by term + print_number.

#### 5. Senat Process Detail Page — `GET /senat/{print_number}`
**Template:** `templates/senat_detail.html` (extends base)

Shows:
- Title, print number
- Decision badge (przyjęto/odrzucono/poprawki)
- Decision date
- Link back to Sejm process (if `sejm_process_number` is set)
- Raw details from `raw_json`

Data: query SenatProcess by term + print_number.

### Links to Add in Existing Templates

Every place items are listed, make them clickable:

| Template | What to link | Link target |
|----------|-------------|-------------|
| `partials/recent_acts.html` | Act title/card | `/ustawa/{eli_id}` |
| `partials/search_results.html` | Act title in results | `/ustawa/{eli_id}` |
| `partials/sejm_live.html` | Process titles | `/proces/{process_number}` |
| `partials/sejm_live.html` | Voting titles | `/glosowanie/{sitting}/{voting_number}` |
| `partials/sejm_processes.html` | Process cards | `/proces/{process_number}` |
| `partials/sejm_votings.html` | Voting cards | `/glosowanie/{sitting}/{voting_number}` |
| `partials/senat_processes.html` | Senat process cards | `/senat/{print_number}` |
| `templates/process_detail.html` | Related prints | `/druk/{print_number}` |
| `templates/process_detail.html` | Related act | `/ustawa/{eli_id}` |
| `templates/process_detail.html` | Related votings | `/glosowanie/{sitting}/{voting_number}` |

### Backend — server.py

5 new page routes, each querying data and passing to template context (SSR, same pattern as existing pages):

```python
GET /ustawa/{eli_id:path}
GET /proces/{process_number}
GET /glosowanie/{sitting}/{voting_number}
GET /druk/{print_number}
GET /senat/{print_number}
```

### File changes:
| Action | File |
|--------|------|
| Create | `templates/act_detail.html` |
| Create | `templates/process_detail.html` |
| Create | `templates/voting_detail.html` |
| Create | `templates/print_detail.html` |
| Create | `templates/senat_detail.html` |
| Modify | `server.py` — add 5 detail page routes |
| Modify | `partials/recent_acts.html` — add links |
| Modify | `partials/search_results.html` — add links |
| Modify | `partials/sejm_live.html` — add links |
| Modify | `partials/sejm_processes.html` — add links |
| Modify | `partials/sejm_votings.html` — add links |
| Modify | `partials/senat_processes.html` — add links |

---

## File Changes Summary

| Action | File |
|--------|------|
| Create | `templates/base.html` |
| Rewrite | `templates/index.html` |
| Create | `templates/search.html` |
| Create | `templates/sejm.html` |
| Create | `templates/mcp_guide.html` |
| Create | `templates/partials/recent_acts.html` |
| Create | `templates/partials/sejm_live.html` |
| Create | `templates/partials/search_results.html` |
| Create | `templates/partials/sejm_processes.html` |
| Create | `templates/partials/sejm_votings.html` |
| Modify | `server.py` — add page routes + partial endpoints |
| No change | `main.py`, `mcp_server.py`, `config.py`, models, etc. |
