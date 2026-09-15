from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: str
    jwt_ttl_min: int = 60
    cors_origins: str = "http://localhost:5500,http://127.0.0.1:5500"
    env: str = "development"

    seed_admin1_username: str = "admin1@arckenites.com"
    seed_admin1_password: str = "admin#001"

    # Self-hosted Jitsi deployment backing the Meetings module — never
    # hardcode this in frontend JS; every meeting join URL is built from it.
    meet_domain: str = "meet.arckenites.com"

    # Outbound email for the notification-toggle feature (app/core/notify.py).
    # Gmail SMTP by default: smtp_username is the sending Gmail address,
    # smtp_password is a 16-char Google App Password (NOT the account
    # password — the account needs 2FA on to generate one). Left blank,
    # sends are skipped with a warning log instead of failing loudly.
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_address: str = ""
    smtp_from_name: str = "Arckenites"

    @field_validator("database_url")
    @classmethod
    def _use_psycopg3_driver(cls, v: str) -> str:
        # Render (and most providers) hand out plain postgresql:// URLs, which
        # SQLAlchemy defaults to the psycopg2 driver for — not installed here,
        # since requirements.txt installs psycopg (v3) instead.
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+psycopg://", 1)
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+psycopg://", 1)
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
