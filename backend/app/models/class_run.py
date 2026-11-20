"""Class occurrences, their recordings, and the durable job queue that drives them.

A `ClassSessionRun` is ONE occurrence of a class: the unit that owns the
trainer's recording approval, the live room, and (at most) one recording.
Keying approval to the occurrence rather than to the `ClassSession` row is
what makes "rescheduling requires a new approval" and "ad-hoc batch classes
use the same workflow" fall out naturally: a reschedule changes the
occurrence key (a new row, approval PENDING again), and an ad-hoc class is
just an occurrence with no `ClassSession` behind it.

States are plain strings guarded by CHECK constraints rather than Postgres
enum types, because this state machine is expected to grow and a Postgres
enum can add values but never remove them.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Lifecycle of the occurrence itself.
RUN_SCHEDULED = "SCHEDULED"
RUN_ROOM_OPEN = "ROOM_OPEN"
RUN_LIVE = "LIVE"
RUN_ENDED = "ENDED"
RUN_CANCELLED = "CANCELLED"
RUN_MISSED = "MISSED"
RUN_STATES = (RUN_SCHEDULED, RUN_ROOM_OPEN, RUN_LIVE, RUN_ENDED, RUN_CANCELLED, RUN_MISSED)
RUN_OPEN_STATES = (RUN_ROOM_OPEN, RUN_LIVE)
RUN_TERMINAL_STATES = (RUN_ENDED, RUN_CANCELLED, RUN_MISSED)

RUN_KIND_SCHEDULED = "SCHEDULED"
RUN_KIND_AD_HOC = "AD_HOC"

# Trainer's explicit decision. PENDING is also what a dismissed popup leaves
# behind: closing the dialog is never consent.
APPROVAL_PENDING = "PENDING"
APPROVAL_APPROVED = "APPROVED"
APPROVAL_DECLINED = "DECLINED"
APPROVAL_STATUSES = (APPROVAL_PENDING, APPROVAL_APPROVED, APPROVAL_DECLINED)

# Only the class trainer can approve recording. There is deliberately no admin override.
APPROVER_TRAINER = "TRAINER"

# Recording state shown to users. Mirrors the approved list: awaiting
# approval, approved, starting, recording, processing, published, declined,
# failed; plus STOPPING (a stop is in flight) and NOT_RECORDED (the class
# ended without a recording ever being approved or started).
REC_AWAITING_APPROVAL = "AWAITING_APPROVAL"
REC_APPROVED = "APPROVED"
REC_STARTING = "STARTING"
REC_RECORDING = "RECORDING"
REC_STOPPING = "STOPPING"
REC_PROCESSING = "PROCESSING"
REC_PUBLISHED = "PUBLISHED"
REC_DECLINED = "DECLINED"
REC_FAILED = "FAILED"
REC_NOT_RECORDED = "NOT_RECORDED"
RECORDING_STATES = (
    REC_AWAITING_APPROVAL, REC_APPROVED, REC_STARTING, REC_RECORDING, REC_STOPPING,
    REC_PROCESSING, REC_PUBLISHED, REC_DECLINED, REC_FAILED, REC_NOT_RECORDED,
)

# Capture (media on disk) and publication (visible in Class Videos) are
# separate on purpose: a file that is safely stored but failed to publish is
# retried as a publish, never re-recorded.
CAPTURE_STARTING = "STARTING"
CAPTURE_RECORDING = "RECORDING"
CAPTURE_FINALIZING = "FINALIZING"
CAPTURE_VERIFYING = "VERIFYING"
CAPTURE_STORED = "STORED"
CAPTURE_FAILED = "FAILED"
CAPTURE_STATES = (
    CAPTURE_STARTING, CAPTURE_RECORDING, CAPTURE_FINALIZING, CAPTURE_VERIFYING, CAPTURE_STORED, CAPTURE_FAILED,
)

PUBLICATION_NOT_PUBLISHED = "NOT_PUBLISHED"
PUBLICATION_PUBLISHING = "PUBLISHING"
PUBLICATION_PUBLISHED = "PUBLISHED"
PUBLICATION_FAILED = "PUBLISH_FAILED"
PUBLICATION_REVOKED = "REVOKED"
PUBLICATION_STATES = (
    PUBLICATION_NOT_PUBLISHED, PUBLICATION_PUBLISHING, PUBLICATION_PUBLISHED, PUBLICATION_FAILED, PUBLICATION_REVOKED,
)

JOB_PENDING = "PENDING"
JOB_RUNNING = "RUNNING"
JOB_SUCCEEDED = "SUCCEEDED"
JOB_DEAD = "DEAD"
JOB_STATES = (JOB_PENDING, JOB_RUNNING, JOB_SUCCEEDED, JOB_DEAD)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ClassSessionRun(Base):
    __tablename__ = "class_session_runs"
    __table_args__ = (
        CheckConstraint(_in("state", RUN_STATES), name="ck_class_session_runs_state"),
        CheckConstraint(_in("kind", (RUN_KIND_SCHEDULED, RUN_KIND_AD_HOC)), name="ck_class_session_runs_kind"),
        CheckConstraint(_in("approval_status", APPROVAL_STATUSES), name="ck_class_session_runs_approval"),
        CheckConstraint(_in("recording_state", RECORDING_STATES), name="ck_class_session_runs_recording_state"),
        CheckConstraint(
            "approval_source IS NULL OR approval_source = 'TRAINER'",
            name="ck_class_session_runs_approval_source",
        ),
        # Database-level duplicate prevention for ad-hoc classes: a batch can
        # have only one open ad-hoc occurrence, however many requests race.
        Index(
            "uq_class_session_runs_open_ad_hoc", "batch_id", unique=True,
            postgresql_where=text("kind = 'AD_HOC' AND state IN ('ROOM_OPEN', 'LIVE')"),
            sqlite_where=text("kind = 'AD_HOC' AND state IN ('ROOM_OPEN', 'LIVE')"),
        ),
        Index("ix_class_session_runs_state_end", "state", "scheduled_end_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Natural key of the occurrence. For a scheduled class it embeds the
    # scheduled start, so a reschedule is a different key (and so a new row
    # that needs its own approval). Uniqueness makes get-or-create race-safe.
    occurrence_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)

    class_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("class_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False, index=True)
    # Snapshot of the batch trainer when the occurrence was created. Approval
    # authority follows the batch's CURRENT trainer, not this snapshot.
    trainer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    meeting_id: Mapped[int | None] = mapped_column(ForeignKey("meetings.id"), nullable=True, index=True)

    scheduled_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # scheduled_end_at plus any authorized extension. The recording stop job
    # is driven by this, never by scheduled_end_at directly.
    effective_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actual_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Set when the trainer's last connection drops; cleared on reconnect.
    # The lifecycle worker ends the class only after the grace period.
    trainer_left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Timestamp (as reported by the media server) of the newest trainer
    # presence event applied. Older or duplicate events are ignored, so
    # out-of-order webhook delivery cannot flip the trainer's presence back.
    trainer_presence_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    state: Mapped[str] = mapped_column(String(16), nullable=False, default=RUN_SCHEDULED)

    approval_status: Mapped[str] = mapped_column(String(16), nullable=False, default=APPROVAL_PENDING)
    approval_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    approval_decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approval_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Claimed atomically the first time the popup is shown for this
    # occurrence, so a refresh, reconnect or second tab never re-prompts.
    approval_prompt_shown_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_text_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    recording_state: Mapped[str] = mapped_column(String(20), nullable=False, default=REC_AWAITING_APPROVAL)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ClassRecording(Base):
    """One recording attempt for an occurrence: the media artifact itself.

    A row here is NOT proof that media exists. `capture_state` is STORED only
    after the file was finalized, found nonempty, and passed media checks.
    """

    __tablename__ = "class_recordings"
    __table_args__ = (
        CheckConstraint(_in("capture_state", CAPTURE_STATES), name="ck_class_recordings_capture_state"),
        CheckConstraint(_in("publication_state", PUBLICATION_STATES), name="ck_class_recordings_publication_state"),
        # At most one live (non-failed) recording per occurrence; a retry
        # after FAILED is a new attempt row, never a second concurrent one.
        Index(
            "uq_class_recordings_active_per_run", "run_id", unique=True,
            postgresql_where=text("capture_state <> 'FAILED'"),
            sqlite_where=text("capture_state <> 'FAILED'"),
        ),
        Index("ix_class_recordings_batch_publication", "batch_id", "publication_state"),
    )

    # The only identifier ever exposed to clients.
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[int] = mapped_column(ForeignKey("class_session_runs.id"), nullable=False, index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False)
    class_session_id: Mapped[int | None] = mapped_column(ForeignKey("class_sessions.id", ondelete="SET NULL"), nullable=True)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    egress_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)

    capture_state: Mapped[str] = mapped_column(String(16), nullable=False, default=CAPTURE_STARTING)
    publication_state: Mapped[str] = mapped_column(String(16), nullable=False, default=PUBLICATION_NOT_PUBLISHED)

    # Safe local staging location, relative to the configured staging root.
    # Server-generated; never built from user input.
    staging_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    storage_provider: Mapped[str | None] = mapped_column(String(24), nullable=True)
    remote_item_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # The drive that holds remote_item_id, recorded at upload time. NULL for
    # recordings uploaded before this column existed: those fall back to the
    # configured ONEDRIVE_DRIVE_ID (never backfilled automatically).
    remote_drive_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    remote_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    media_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RecordingEvent(Base):
    """Append-only log of every state transition for an occurrence/recording,
    kept separately from the admin audit log so operators can reconstruct
    exactly what the pipeline did and why."""

    __tablename__ = "recording_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("class_session_runs.id"), nullable=False, index=True)
    recording_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("class_recordings.id"), nullable=True)
    event: Mapped[str] = mapped_column(String(48), nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MediaWebhookEvent(Base):
    """Every media-server webhook we have accepted, keyed by the media server's
    own event id. Inserting in the same transaction as the event's effects makes
    a replayed or retried delivery a no-op, while a delivery whose processing
    failed (and so rolled back) is still processed when the server retries."""

    __tablename__ = "media_webhook_events"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    event: Mapped[str] = mapped_column(String(40), nullable=False)
    room_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BackgroundJob(Base):
    """Durable, retryable work item. The queue lives in PostgreSQL so a
    process restart resumes exactly where it stopped (the same approach the
    Community email queue already uses). `dedupe_key` is unique, so
    enqueueing the same logical job twice can never produce two jobs."""

    __tablename__ = "background_jobs"
    __table_args__ = (
        CheckConstraint(_in("state", JOB_STATES), name="ck_background_jobs_state"),
        Index("ix_background_jobs_state_run_at", "state", "run_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    state: Mapped[str] = mapped_column(String(12), nullable=False, default=JOB_PENDING)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    locked_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
