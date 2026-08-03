from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration. No other module hardcodes a filesystem path."""

    model_config = SettingsConfigDict(env_prefix="APP_", frozen=True)

    db_path: str = "var/app.db"
    schema_path: str = "app/storage/schema.sql"
    seeds_path: str = "seeds/rules.json"

    scheduler_impl: Literal["scan", "heap"] = "scan"
    tick_interval_sec: int = Field(default=5, gt=0)
    replay_speed: float = Field(default=1.0, gt=0)
