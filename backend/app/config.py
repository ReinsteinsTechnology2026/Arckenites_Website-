from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: str
    jwt_ttl_min: int = 60
    cors_origins: str = "http://localhost:5500,http://127.0.0.1:5500"
    env: str = "development"

    seed_admin1_username: str = "akadmin@001"
    seed_admin1_password: str = "admin#001"

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
