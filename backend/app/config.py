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
    seed_admin1_password: str = ""

    # Public site origin used to build links inside outbound emails (the
    # "View on Arckenites Community" button). Never hardcode it in templates.
    public_site_url: str = "https://arckenites.com"

    # Google sign-in for existing Arckenites Portal accounts (student, staff,
    # admin) only. Never used for Community accounts. All three must be set in
    # the server environment; the client secret is never sent to the browser.
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""
    # Fernet key for encrypting TOTP secrets and keying recovery-code hashes.
    # Kept separate from JWT_SECRET so either can rotate independently.
    mfa_encryption_key: str = ""

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.google_redirect_uri)

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

    # Microsoft Graph — the preferred outbound-email transport when configured
    # (see core/email.py). Uses the OAuth2 client-credentials flow against an
    # Entra app registration with Mail.Send (Application) permission, so it
    # works even with Microsoft Entra Security Defaults enabled and does NOT
    # depend on Basic SMTP auth being allowed for the mailbox. SMTP above
    # stays as the fallback transport only when Graph is left unconfigured
    # (e.g. local dev against a personal Gmail account) — the two are never
    # both attempted for the same message.
    ms_graph_tenant_id: str = ""
    ms_graph_client_id: str = ""
    ms_graph_client_secret: str = ""
    ms_graph_sender_email: str = ""

    @property
    def ms_graph_configured(self) -> bool:
        return bool(self.ms_graph_tenant_id and self.ms_graph_client_id and self.ms_graph_client_secret and self.ms_graph_sender_email)

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

    # Trainer scheduling. Availability, batch class times and class sessions are
    # wall-clock times in this IANA zone (the repo has always stored them
    # naive); availability exceptions are stored as UTC instants.
    scheduling_timezone: str = "Asia/Kolkata"
    scheduling_max_weekly_hours: float = 40.0
    scheduling_default_session_minutes: int = 60
    scheduling_slot_minutes: int = 30

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
