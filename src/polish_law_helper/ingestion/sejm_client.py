"""Client for Sejm API (api.sejm.gov.pl/sejm)."""

import httpx

from polish_law_helper.config import settings
from polish_law_helper.ingestion.retry import with_retry


class SejmClient:
    def __init__(
        self,
        base_url: str = settings.sejm_base_url,
        term: int = settings.sejm_term,
    ):
        self.base_url = base_url
        self.term = term
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=60.0,
                headers={
                    "User-Agent": "PolishLawHelper/0.1",
                    "Accept": "application/json",
                },
                follow_redirects=True,
            )
        return self._client

    @with_retry()
    async def get_processes(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """List legislative processes for current term."""
        client = await self._get_client()
        url = f"{self.base_url}/term{self.term}/processes"
        resp = await client.get(url, params={"limit": limit, "offset": offset})
        resp.raise_for_status()
        return resp.json()

    @with_retry()
    async def get_process(self, process_number: str) -> dict:
        """Get details of a specific legislative process."""
        client = await self._get_client()
        url = f"{self.base_url}/term{self.term}/processes/{process_number}"
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()

    @with_retry()
    async def get_votings(
        self,
        sitting: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """List votings. Can filter by sitting number or date range."""
        client = await self._get_client()

        if sitting:
            url = f"{self.base_url}/term{self.term}/votings/{sitting}"
            resp = await client.get(url)
        else:
            url = f"{self.base_url}/term{self.term}/votings"
            params = {"limit": limit, "offset": offset}
            if date_from:
                params["dateFrom"] = date_from
            if date_to:
                params["dateTo"] = date_to
            resp = await client.get(url, params=params)

        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "json" not in content_type:
            return []
        text = resp.text.strip()
        if not text:
            return []
        return resp.json()

    @with_retry()
    async def get_voting(self, sitting: int, voting_number: int) -> dict:
        """Get details of a specific voting."""
        client = await self._get_client()
        url = f"{self.base_url}/term{self.term}/votings/{sitting}/{voting_number}"
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()

    @with_retry()
    async def get_prints(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """List parliamentary prints (druki sejmowe)."""
        client = await self._get_client()
        url = f"{self.base_url}/term{self.term}/prints"
        resp = await client.get(url, params={"limit": limit, "offset": offset})
        resp.raise_for_status()
        return resp.json()

    @with_retry()
    async def get_print(self, print_number: str) -> dict:
        """Get details of a specific parliamentary print."""
        client = await self._get_client()
        url = f"{self.base_url}/term{self.term}/prints/{print_number}"
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()

    @with_retry()
    async def get_proceedings(self, limit: int = 10) -> list[dict]:
        """List recent proceedings (posiedzenia)."""
        client = await self._get_client()
        url = f"{self.base_url}/term{self.term}/proceedings"
        resp = await client.get(url, params={"limit": limit})
        resp.raise_for_status()
        return resp.json()

    @with_retry()
    async def get_mps(self) -> list[dict]:
        """List all MPs for current term."""
        client = await self._get_client()
        url = f"{self.base_url}/term{self.term}/MP"
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()

    @with_retry()
    async def get_clubs(self) -> list[dict]:
        """List parliamentary clubs."""
        client = await self._get_client()
        url = f"{self.base_url}/term{self.term}/clubs"
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()

    @with_retry()
    async def get_committees(self) -> list[dict]:
        """List parliamentary committees."""
        client = await self._get_client()
        url = f"{self.base_url}/term{self.term}/committees"
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
