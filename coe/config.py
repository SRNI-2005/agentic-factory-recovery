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
    solver_time_limit_seconds: int = 60
    solver_alpha_weight: float = 1.0
    solver_beta_weight: float = 1.0
    solver_normalize_objectives: bool = True
    solver_random_seed: int = 42
    solver_num_search_workers: int = 1


@lru_cache
def get_settings() -> Settings:
    return Settings()
