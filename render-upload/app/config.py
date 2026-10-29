"""Settings, read from the .env file. Nothing secret is written in the code."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _database_url(raw: str) -> str:
    # Supabase gives "postgresql://..."; we use the pure-Python pg8000 driver.
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://"):]
    if raw.startswith("postgresql://"):
        raw = "postgresql+pg8000://" + raw[len("postgresql://"):]
    return raw


@dataclass(frozen=True)
class Settings:
    database_url: str
    secret_key: str
    env: str
    frontend_origins: list[str]
    session_hours: int = 12
    max_failed_logins: int = 5
    lock_minutes: int = 15
    timezone: str = "Asia/Kolkata"
    # True = staff must replace the temporary password at first login.
    force_password_change: bool = True

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"


def load_settings() -> Settings:
    url = os.getenv("DATABASE_URL", "").strip()
    secret = os.getenv("SECRET_KEY", "").strip()
    env = os.getenv("ENV", "prod").strip().lower()
    if not url:
        raise RuntimeError("DATABASE_URL is missing. Copy .env.example to .env and fill it in.")
    if len(secret) < 32 or secret.startswith("paste-"):
        raise RuntimeError("SECRET_KEY must be a random string of at least 32 characters (see .env.example).")
    origins = [o.strip().rstrip("/") for o in os.getenv("FRONTEND_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
    force = os.getenv("FORCE_PASSWORD_CHANGE", "true").strip().lower() != "false"
    return Settings(database_url=_database_url(url), secret_key=secret, env=env, frontend_origins=origins,
                    force_password_change=force)


settings = load_settings()
