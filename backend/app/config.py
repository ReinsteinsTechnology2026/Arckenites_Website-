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
    def livekit_configured(self) -> bool:
        return bool(self.livekit_url and self.livekit_api_key and self.livekit_api_secret)

    @property
    def is_production(self) -> bool:
        """ENV compared the same way everywhere: case and surrounding whitespace never
        turn a production server into a non-production one."""
        return (self.env or "").strip().lower() == "production"

    @property
    def classroom_routes_enabled(self) -> bool:
        """Whether the LiveKit class routes exist at all. Production needs the explicit
        classroom flag; RECORDING_ENABLED alone does not expose them (recording and
        classroom availability are separate)."""
        return (not self.is_production) or self.livekit_classroom_enabled

    @property
    def classroom_available(self) -> bool:
        """What the browser is told: use the LiveKit classroom only when it is switched on,
        its routes are registered and the media server is configured."""
        return self.livekit_classroom_enabled and self.livekit_configured and self.classroom_routes_enabled

    @property
    def meetings_on_livekit(self) -> bool:
        return self.livekit_meetings_enabled and self.livekit_configured

    @property
    def livekit_server_api_url(self) -> str:
        if self.livekit_api_url:
            return self.livekit_api_url
        return self.livekit_url.replace("wss://", "https://", 1).replace("ws://", "http://", 1)

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.google_redirect_uri)

    # Self-hosted Jitsi deployment backing the Meetings module — never
    # hardcode this in frontend JS; every meeting join URL is built from it.
    meet_domain: str = "meet.arckenites.com"

    # Self-hosted class recording (app/core/class_runs.py). Off until the
    # media server and the staging directory are actually provisioned, so an
    # unconfigured server can never tell a trainer that a class is recording.
    recording_enabled: bool = False
    # Bump when the consent wording shown to the trainer changes; an approval
    # for an older wording is rejected so a stale page cannot grant consent.
    recording_consent_version: str = "2026-10-v1"
    # Local staging root for finalized recordings. Must be an absolute path on
    # persistent storage OUTSIDE the application checkout.
    recording_staging_dir: str = ""
    recording_min_free_gb: float = 20.0
    # How long a trainer may be disconnected before the class is ended.
    # Rejoining inside this window cancels the pending termination.
    trainer_reconnect_grace_seconds: int = 60
    # The room opens this many minutes before the scheduled start so the
    # trainer can enter early. Nothing is recorded and no attendance is
    # created by opening the room.
    class_room_open_lead_minutes: int = 5
    # Most a trainer may extend a class past its scheduled end, in total.
    class_extension_max_minutes: int = 60
    # Background job queue (app/core/job_queue.py).
    job_lease_seconds: int = 120
    job_poll_seconds: float = 2.0
    # OneDrive publishing stays off until access and the destination have been
    # verified (backend/maintenance/check_onedrive_access.py).
    onedrive_upload_enabled: bool = False
    # The destination, set only after check_onedrive_access confirmed it. With
    # application permissions there is no "my drive": a specific drive id is required.
    onedrive_drive_id: str = ""
    onedrive_folder_path: str = "ArckenitesRecordings"
    # Graph requires upload chunks to be a multiple of 320 KiB.
    onedrive_chunk_bytes: int = 320 * 1024 * 32
    # DEVELOPMENT ONLY: publish a verified recording straight from local
    # staging so playback can be exercised before OneDrive access exists.
    # Ignored unless ENV is development/test; entries are labelled LOCAL_STAGING.
    recording_publish_from_staging: bool = False
    # Local-copy retention (app/core/recording_retention.py). It only ever
    # removes the LOCAL staging copy of a recording whose OneDrive copy has been
    # re-verified byte-for-byte; it never deletes remote files, database rows or
    # anything else. OFF until explicitly approved, and even when enabled it
    # only REPORTS (dry run) until dry_run is switched off as a second step.
    recording_retention_enabled: bool = False
    recording_retention_dry_run: bool = True
    # Keep the local copy at least this long after the remote copy was
    # verified. Values below RETENTION_MIN_DAYS are raised to that floor.
    recording_retention_local_days: int = 30
    # Most files examined per run (bounds hashing work and blast radius).
    recording_retention_batch_limit: int = 10
    # Self-hosted LiveKit. Blank = media server not configured. The API key and
    # secret are server-side only and are never sent to a browser; browsers get
    # short-lived, room-scoped join tokens instead.
    livekit_url: str = ""            # browser signalling URL, wss://...
    livekit_api_url: str = ""        # server-to-server URL; derived from livekit_url when blank
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    livekit_token_ttl_seconds: int = 900
    # Three independent switches, ALL off by default so production keeps using Jitsi for
    # batch classes and general meetings until each is deliberately turned on:
    #   LIVEKIT_CLASSROOM_ENABLED  batch classrooms on LiveKit (class routes + "Join class" links)
    #   LIVEKIT_MEETINGS_ENABLED   general meetings on LiveKit (never recorded)
    #   RECORDING_ENABLED          trainer-approved recording inside a LiveKit classroom
    # Having LiveKit configured (URL, key, secret) does not turn any of them on.
    livekit_classroom_enabled: bool = False
    livekit_meetings_enabled: bool = False
    # Egress writes to this path INSIDE the Egress container; it must be the
    # container-side mount of recording_staging_dir.
    livekit_egress_output_prefix: str = "/out"
    livekit_recording_layout: str = "speaker"
    livekit_recording_preset: str = "H264_720P_30"
    # A room outlives an empty moment this long, which must exceed the trainer
    # reconnect grace period or a brief drop would close the room and recording.
    livekit_room_empty_timeout_seconds: int = 600
    # Presence webhooks older than this are ignored (egress events never are).
    media_webhook_presence_max_age_seconds: int = 600
    recording_max_start_attempts: int = 2
    # How long an approved recording waits for someone to publish camera, mic or
    # screen share before it gives up with a clear message (Egress cannot start
    # on an empty stage). The trainer can retry once media is on.
    recording_start_wait_seconds: int = 90
    # Egress joined the room but never produced a recording within this long.
    recording_activation_timeout_seconds: int = 120
    # An Egress whose room participant has been missing this long is treated as
    # lost (crashed/killed container) and the recording is marked failed.
    recording_egress_lost_grace_seconds: int = 45

    # Private portal file storage; independent of any recording-capture feature.
    portal_onedrive_enabled: bool = False
    portal_onedrive_folder_path: str = "ArckenitesPortalFiles"

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

    @field_validator("livekit_api_secret")
    @classmethod
    def _livekit_secret_is_long_enough(cls, v: str) -> str:
        # Blank means "media server not configured" (the default). A configured secret signs
        # join tokens and webhooks, so a short guessable one is refused at startup.
        if v and len(v) < 32:
            raise ValueError("LIVEKIT_API_SECRET must be at least 32 characters (generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\")")
        return v

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
