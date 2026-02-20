"""Configuration management for runna-intervals.

Credentials are loaded from .env in the current directory (repo root),
with environment variables taking highest priority.

Run `runna-intervals config` to create the .env file interactively.
"""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOCAL_ENV = Path(".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_LOCAL_ENV),
        env_file_encoding="utf-8",
        env_prefix="RUNNA_INTERVALS_",
        extra="ignore",
    )

    intervals_api_key: SecretStr
    intervals_athlete_id: str = "i0"
    intervals_base_url: str = "https://intervals.icu"

    # Runna private ICS calendar feed URL (from the Runna app → Profile → Connected Apps & Devices → Connect Calendar → Other Calendar)
    runna_ics_url: str

    # Fallback easy pace in sec/mi used for steps with no explicit pace target.
    # 520 ≈ 8:40/mi. Set RUNNA_INTERVALS_EASY_PACE_SEC_MI in .env to override.
    easy_pace_sec_mi: int = 520


def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
