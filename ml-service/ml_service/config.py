from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    questdb_http_addr: str = "http://questdb:9000"
    redis_url: str = "redis://redis:6379"
    ml_feature_store_path: str = "/data/features"
    ml_model_registry_path: str = "/data/models"
    log_level: str = "info"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
