from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment / .env (spec §8)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://coe:coe@localhost:5432/coe"
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    default_seed: int = 42
    telemetry_chunk_interval_minutes: int = 10080


@lru_cache
def get_settings() -> Settings:
    return Settings()
