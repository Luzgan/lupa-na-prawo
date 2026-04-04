"""Client for Senat API (api.sejm.gov.pl/senat)."""

import httpx

from polish_law_helper.config import settings
from polish_law_helper.ingestion.retry import with_retry


class SenatClient:
    def __init__(
        self,
        base_url: str = settings.senat_base_url,
        term: int = settings.senat_term,
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
    async def get_prints(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """List Senat prints (bills received from Sejm)."""
        client = await self._get_client()
        url = f"{self.base_url}/term{self.term}/prints"
        resp = await client.get(url, params={"limit": limit, "offset": offset})
        resp.raise_for_status()
        return resp.json()

    @with_retry()
    async def get_print(self, number: str) -> dict:
        """Get details of a specific Senat print."""
        client = await self._get_client()
        url = f"{self.base_url}/term{self.term}/prints/{number}"
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()

    @with_retry()
    async def get_votings(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """List Senat votings."""
        client = await self._get_client()
        url = f"{self.base_url}/term{self.term}/votings"
        resp = await client.get(url, params={"limit": limit, "offset": offset})
        resp.raise_for_status()
        return resp.json()

    @with_retry()
    async def get_proceedings(
        self,
        limit: int = 10,
    ) -> list[dict]:
        """List Senat proceedings (posiedzenia)."""
        client = await self._get_client()
        url = f"{self.base_url}/term{self.term}/proceedings"
        resp = await client.get(url, params={"limit": limit})
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
