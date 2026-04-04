import httpx

from polish_law_helper.config import settings


class OllamaEmbedder:
    def __init__(
        self,
        base_url: str = settings.ollama_url,
        model: str = settings.ollama_model,
    ):
        self.base_url = base_url
        self.model = model
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=120.0)
        return self._client

    async def embed(self, text: str, prefix: str = "passage") -> list[float]:
        """Embed a single text. prefix: 'passage' for indexing, 'query' for search."""
        result = await self.embed_batch([text], prefix=prefix)
        return result[0]

    async def embed_batch(
        self, texts: list[str], prefix: str = "passage"
    ) -> list[list[float]]:
        """Embed a batch of texts. Returns list of embedding vectors."""
        prefixed = [f"{prefix}: {t}" for t in texts]
        client = await self._get_client()
        resp = await client.post(
            "/api/embed",
            json={"model": self.model, "input": prefixed},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["embeddings"]

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()


embedder = OllamaEmbedder()
