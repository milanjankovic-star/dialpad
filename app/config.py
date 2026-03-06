from pydantic_settings import BaseSettings
from pydantic import field_validator
from functools import lru_cache


class Settings(BaseSettings):
    # Dialpad
    dialpad_api_key: str = ""
    dialpad_webhook_secret: str = ""
    dialpad_api_base_url: str = "https://sandbox.dialpad.com/api/v2"

    # Database
    database_url: str = "postgresql+asyncpg://dialpad:dialpad@localhost:5432/dialpad_calls"

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "info"

    @field_validator("database_url", mode="before")
    @classmethod
    def fix_db_url_scheme(cls, v: str) -> str:
        """
        Render provides DATABASE_URL as 'postgresql://...' but asyncpg
        needs 'postgresql+asyncpg://...'. Auto-convert.
        """
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgresql://") and "+asyncpg" not in v:
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
