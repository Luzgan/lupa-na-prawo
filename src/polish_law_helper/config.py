from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://lupa_na_prawo:dev@localhost:5432/polish_law"
    database_url_sync: str = "postgresql+psycopg://lupa_na_prawo:dev@localhost:5432/polish_law"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "jeffh/intfloat-multilingual-e5-large:f16"
    embedding_dim: int = 1024
    embedding_batch_size: int = 32
    eli_base_url: str = "https://api.sejm.gov.pl/eli"
    sejm_base_url: str = "https://api.sejm.gov.pl/sejm"
    sejm_term: int = 10
    senat_base_url: str = "https://api.sejm.gov.pl/senat"
    senat_term: int = 11  # Current Senat term
    request_delay: float = 1.0
    skip_startup_ingest: bool = False
    cron_hour: int = 3
    cron_enabled: bool = True
    base_url: str = "http://localhost:8765"

    model_config = {"env_prefix": "PLH_"}


settings = Settings()
