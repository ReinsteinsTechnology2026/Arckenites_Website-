import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class LabVm(Base):
    """A physical/virtual Windows lab machine students RDP into. The
    student account's password is a short-lived, auto-rotating secret (see
    current_agent_password_encrypted below) — never the VM's real
    Administrator password, which this system never generates, stores, or
    displays. A student only ever sees the current rotating password while
    their own grant is active; it dies the instant that grant ends.

    agent_token_hash is a SEPARATE credential — the ONLY one the Windows
    Agent running on this VM authenticates itself with — a per-VM secret
    generated once at creation time, shown to the admin exactly once, and
    stored here only as a SHA-256 hash (never plaintext), same principle as
    a password hash. It is completely separate from the portal's own JWT
    secret and from the rotating RDP password above."""

    __tablename__ = "lab_vms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    rdp_port: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3389")
    operating_system: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The one shared local Windows account this VM's grants use for RDP —
    # see the plan's "one shared student RDP account per VM" decision.
    student_rdp_username: Mapped[str] = mapped_column(String(150), nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    agent_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    agent_last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # The password the agent should currently have set on student_rdp_username's
    # Windows account — Fernet-encrypted at rest (core/lab_vm_secrets.py), never
    # plaintext in the DB. Rotated to a fresh, nobody-shown value the instant a
    # grant ends (revoke/expiry), so a student who saved/wrote down the password
    # can't reuse it later even if group membership enforcement were ever
    # bypassed — this is a second, independent layer, not the primary one.
    current_agent_password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    access_entries: Mapped[list["LabVmAccess"]] = relationship(back_populates="vm")


class LabVmAccessStatus(str, enum.Enum):
    active = "active"
    expired = "expired"
    revoked = "revoked"


class LabVmAccess(Base):
    """The current/most-recent grant for one student. At most one row per
    student may have status='active' at a time — enforced by the partial
    unique index below, not just by application-level care, so a
    double-click on Grant (or two admins acting at once) can't produce two
    live grants. Re-granting supersedes (revokes) any existing active row
    for that student before inserting a fresh one — see
    routes_admin_lab_vm.py's grant endpoint."""

    __tablename__ = "lab_vm_access"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    vm_id: Mapped[int] = mapped_column(ForeignKey("lab_vms.id"), nullable=False, index=True)

    status: Mapped[LabVmAccessStatus] = mapped_column(
        Enum(LabVmAccessStatus, name="lab_vm_access_status_enum"),
        nullable=False,
        default=LabVmAccessStatus.active,
        index=True,
    )

    # Always UTC, always server-computed — never trust a client-supplied
    # time for either of these.
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    granted_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    # Snapshot of the password that was live on the VM's student account for
    # THIS grant, Fernet-encrypted — only ever decrypted and shown back to
    # the student while this row's status is still 'active'. Cleared (set to
    # None) the moment this row is revoked/expired, so it can never leak via
    # a later export/inspection even though nothing currently serializes it.
    rdp_password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    vm: Mapped["LabVm"] = relationship(back_populates="access_entries")
    student: Mapped["User"] = relationship(foreign_keys=[student_id])

    __table_args__ = (
        # A raw SQL predicate, not a Python-level column comparison — the
        # WHERE clause of a Postgres partial index is just SQL, and this
        # sidesteps any ambiguity about mapped_column() objects not yet
        # being fully-processed Column instances at class-definition time.
        Index(
            "uq_one_active_lab_vm_access_per_student",
            "student_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )


class LabVmAccessAuditLog(Base):
    """Immutable history of every VM-lab-access action — grants, revokes,
    expirations, connect/disconnect events the agent reports, and agent
    online/offline transitions. Never edited or deleted; vm_id is nullable
    so this history survives a VM inventory entry being removed later."""

    __tablename__ = "lab_vm_access_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    vm_id: Mapped[int | None] = mapped_column(ForeignKey("lab_vms.id"), nullable=True, index=True)

    # GRANTED / REVOKED / EXPIRED / CONNECTED / DISCONNECTED / AGENT_ONLINE /
    # AGENT_OFFLINE / ACCESS_DENIED — a plain string, not an enum, matching
    # the existing LabAccessAuditLog.action convention (String(30)) rather
    # than inventing a second enum-vs-string style within the same feature.
    action: Mapped[str] = mapped_column(String(30), nullable=False)

    # Null for system-driven actions (EXPIRED, AGENT_ONLINE/OFFLINE) that no
    # human performed.
    performed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    event_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
