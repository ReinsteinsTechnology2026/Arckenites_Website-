"""add class occurrences, recording approval, recordings and the durable job queue

Revision ID: d4e8a1b7c356
Revises: 9d2f6b8c4e31

Adds only new tables and new audit-event enum values; no existing table or
column is altered, so the previous application version keeps working against
the upgraded schema. The recordings.* permission rows are created by the
deploy-time RBAC sync (seed.py --rbac-only), as for the meetings module.

Downgrade drops the new tables (approval decisions, recording metadata and
queued jobs are lost). It cannot remove the added audit-event enum values:
Postgres does not support dropping enum values, and leaving them is harmless.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d4e8a1b7c356"
down_revision: Union[str, None] = "9d2f6b8c4e31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_AUDIT_EVENTS = [
    "recording_approval_requested",
    "recording_approval_granted",
    "recording_approval_declined",
    "recording_started",
    "recording_stopped",
    "recording_processed",
    "recording_failed",
    "recording_published",
    "recording_playback",
    "recording_revoked",
    "recording_deleted",
    "class_run_cancelled",
]


def _in(column: str, values: tuple) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # Each ADD VALUE runs in its own auto-committed statement, as in the
        # meetings migration.
        with op.get_context().autocommit_block():
            for event in NEW_AUDIT_EVENTS:
                op.execute(f"ALTER TYPE auth_event_type ADD VALUE IF NOT EXISTS '{event}'")

    op.create_table(
        "class_session_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("occurrence_key", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("class_session_id", sa.Integer(), sa.ForeignKey("class_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("batches.id"), nullable=False),
        sa.Column("trainer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("meeting_id", sa.Integer(), sa.ForeignKey("meetings.id"), nullable=True),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actual_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.String(32), nullable=True),
        sa.Column("trainer_left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trainer_presence_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("approval_status", sa.String(16), nullable=False),
        sa.Column("approval_source", sa.String(16), nullable=True),
        sa.Column("approval_decided_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approval_decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_prompt_shown_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_text_version", sa.String(32), nullable=True),
        sa.Column("recording_state", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("occurrence_key"),
        sa.CheckConstraint(
            _in("state", ("SCHEDULED", "ROOM_OPEN", "LIVE", "ENDED", "CANCELLED", "MISSED")),
            name="ck_class_session_runs_state",
        ),
        sa.CheckConstraint(_in("kind", ("SCHEDULED", "AD_HOC")), name="ck_class_session_runs_kind"),
        sa.CheckConstraint(
            _in("approval_status", ("PENDING", "APPROVED", "DECLINED")), name="ck_class_session_runs_approval"
        ),
        sa.CheckConstraint(
            _in("recording_state", (
                "AWAITING_APPROVAL", "APPROVED", "STARTING", "RECORDING", "STOPPING",
                "PROCESSING", "PUBLISHED", "DECLINED", "FAILED", "NOT_RECORDED",
            )),
            name="ck_class_session_runs_recording_state",
        ),
        sa.CheckConstraint(
            "approval_source IS NULL OR approval_source = 'TRAINER'",
            name="ck_class_session_runs_approval_source",
        ),
    )
    op.create_index("ix_class_session_runs_class_session_id", "class_session_runs", ["class_session_id"])
    op.create_index("ix_class_session_runs_batch_id", "class_session_runs", ["batch_id"])
    op.create_index("ix_class_session_runs_meeting_id", "class_session_runs", ["meeting_id"])
    op.create_index("ix_class_session_runs_state_end", "class_session_runs", ["state", "scheduled_end_at"])
    op.create_index(
        "uq_class_session_runs_open_ad_hoc", "class_session_runs", ["batch_id"], unique=True,
        postgresql_where=sa.text("kind = 'AD_HOC' AND state IN ('ROOM_OPEN', 'LIVE')"),
        sqlite_where=sa.text("kind = 'AD_HOC' AND state IN ('ROOM_OPEN', 'LIVE')"),
    )

    op.create_table(
        "class_recordings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("class_session_runs.id"), nullable=False),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("batches.id"), nullable=False),
        sa.Column("class_session_id", sa.Integer(), sa.ForeignKey("class_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("egress_id", sa.String(80), nullable=True),
        sa.Column("capture_state", sa.String(16), nullable=False),
        sa.Column("publication_state", sa.String(16), nullable=False),
        sa.Column("staging_key", sa.String(300), nullable=True),
        sa.Column("storage_provider", sa.String(24), nullable=True),
        sa.Column("remote_item_id", sa.String(300), nullable=True),
        sa.Column("remote_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("media_info", sa.Text(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_detail", sa.String(500), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            _in("capture_state", ("STARTING", "RECORDING", "FINALIZING", "VERIFYING", "STORED", "FAILED")),
            name="ck_class_recordings_capture_state",
        ),
        sa.CheckConstraint(
            _in("publication_state", ("NOT_PUBLISHED", "PUBLISHING", "PUBLISHED", "PUBLISH_FAILED", "REVOKED")),
            name="ck_class_recordings_publication_state",
        ),
    )
    op.create_index("ix_class_recordings_run_id", "class_recordings", ["run_id"])
    op.create_index("ix_class_recordings_egress_id", "class_recordings", ["egress_id"])
    op.create_index("ix_class_recordings_batch_publication", "class_recordings", ["batch_id", "publication_state"])
    op.create_index(
        "uq_class_recordings_active_per_run", "class_recordings", ["run_id"], unique=True,
        postgresql_where=sa.text("capture_state <> 'FAILED'"),
        sqlite_where=sa.text("capture_state <> 'FAILED'"),
    )

    op.create_table(
        "recording_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("class_session_runs.id"), nullable=False),
        sa.Column("recording_id", sa.Uuid(), sa.ForeignKey("class_recordings.id"), nullable=True),
        sa.Column("event", sa.String(48), nullable=False),
        sa.Column("from_state", sa.String(24), nullable=True),
        sa.Column("to_state", sa.String(24), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("detail", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_recording_events_run_id", "recording_events", ["run_id"])

    op.create_table(
        "media_webhook_events",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("event", sa.String(40), nullable=False),
        sa.Column("room_name", sa.String(120), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "background_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("dedupe_key", sa.String(160), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_by", sa.String(80), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("dedupe_key"),
        sa.CheckConstraint(
            _in("state", ("PENDING", "RUNNING", "SUCCEEDED", "DEAD")), name="ck_background_jobs_state"
        ),
    )
    op.create_index("ix_background_jobs_state_run_at", "background_jobs", ["state", "run_at"])


def downgrade() -> None:
    op.drop_table("background_jobs")
    op.drop_table("media_webhook_events")
    op.drop_table("recording_events")
    op.drop_table("class_recordings")
    op.drop_table("class_session_runs")
    # The audit-event enum values added in upgrade() are intentionally left in
    # place: Postgres cannot drop enum values.
