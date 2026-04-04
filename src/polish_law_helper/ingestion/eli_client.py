import asyncio
import hashlib

import httpx

from polish_law_helper.config import settings
from polish_law_helper.ingestion.retry import with_retry

# Priority Polish codexes - ELI IDs (publisher/year/position)
# Using consolidated text (tekst jednolity) versions where available
PRIORITY_ACTS = [
    "DU/1964/93",     # Kodeks cywilny
    "DU/1964/296",    # Kodeks postępowania cywilnego
    "DU/1997/553",    # Kodeks karny
    "DU/1997/555",    # Kodeks postępowania karnego
    "DU/2023/1465",   # Kodeks pracy (tekst jednolity 2023)
    "DU/2024/18",     # Kodeks spółek handlowych (tekst jednolity 2024)
    "DU/2023/2809",   # Kodeks rodzinny i opiekuńczy (tekst jednolity 2023)
    "DU/2024/572",    # Kodeks postępowania administracyjnego (tekst jednolity 2024)
    "DU/2023/2119",   # Kodeks wykroczeń (tekst jednolity 2023)
    "DU/1997/483",    # Konstytucja RP (PDF-only, no HTML available)
    "DU/2017/1257",   # Ordynacja podatkowa (tekst jednolity)
    "DU/2024/1451",   # Prawo o postępowaniu przed sądami administracyjnymi (tekst jednolity 2024)
]


class ELIClient:
    def __init__(self, base_url: str = settings.eli_base_url):
        self.base_url = base_url
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=60.0,
                headers={
                    "User-Agent": "PolishLawHelper/0.1 (legal research tool)",
                    "Accept": "application/json",
                },
                follow_redirects=True,
            )
        return self._client

    @with_retry()
    async def get_act_metadata(self, publisher: str, year: str, position: str) -> dict:
        """Fetch act metadata from ELI API."""
        client = await self._get_client()
        url = f"{self.base_url}/acts/{publisher}/{year}/{position}"
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()

    async def get_act_pdf_text(self, publisher: str, year: str, position: str) -> str | None:
        """Fetch act PDF and extract text. Returns None if not available."""
        client = await self._get_client()
        url = f"{self.base_url}/acts/{publisher}/{year}/{position}/text.pdf"
        try:
            resp = await client.get(url, headers={"Accept": "application/pdf"}, timeout=120.0)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            import fitz  # PyMuPDF

            doc = fitz.open(stream=resp.content, filetype="pdf")
            text_parts = []
            for page in doc:
                text_parts.append(page.get_text())
            doc.close()
            return "\n".join(text_parts)
        except Exception:
            return None

    @with_retry()
    async def get_act_html(self, publisher: str, year: str, position: str) -> str:
        """Fetch full act text as HTML."""
        client = await self._get_client()
        url = f"{self.base_url}/acts/{publisher}/{year}/{position}/text.html"
        resp = await client.get(url, headers={"Accept": "text/html"})
        resp.raise_for_status()
        return resp.text

    @with_retry()
    async def get_act_references(self, publisher: str, year: str, position: str) -> dict:
        """Fetch cross-references for an act."""
        client = await self._get_client()
        url = f"{self.base_url}/acts/{publisher}/{year}/{position}/references"
        resp = await client.get(url)
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()

    @with_retry()
    async def search_acts(
        self,
        in_force: bool = True,
        publisher: str = "DU",
        limit: int = 500,
        offset: int = 0,
    ) -> dict:
        """Search acts with pagination. Returns dict with items + totalCount."""
        client = await self._get_client()
        url = f"{self.base_url}/acts/search"
        params = {
            "publisher": publisher,
            "limit": limit,
            "offset": offset,
        }
        if in_force:
            params["inForce"] = "1"
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    @with_retry()
    async def get_changes_since(self, since_date: str) -> list[dict]:
        """Fetch acts changed since a date (YYYY-MM-DD)."""
        client = await self._get_client()
        url = f"{self.base_url}/changes/acts"
        resp = await client.get(url, params={"since": since_date})
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "json" not in content_type:
            return []
        text = resp.text.strip()
        if not text:
            return []
        return resp.json()

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @staticmethod
    def parse_eli_id(eli_id: str) -> tuple[str, str, str]:
        """Parse 'DU/1964/93' into (publisher, year, position)."""
        parts = eli_id.split("/")
        if len(parts) != 3:
            raise ValueError(f"Invalid ELI ID format: {eli_id}")
        return parts[0], parts[1], parts[2]

    @staticmethod
    def html_hash(html: str) -> str:
        return hashlib.sha256(html.encode()).hexdigest()
