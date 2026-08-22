from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Fixed IDs for the three built-in role tiers — referenced directly (not just
# by slug) by the migration's backfill step and by require_permission's
# Super Admin bypass, so they're pinned here as the single source of truth.
SUPER_ADMIN_ROLE_ID = 1
ADMIN_ROLE_ID = 2
SUPPORT_ADMIN_ROLE_ID = 3

SUPER_ADMIN_SLUG = "super_admin"


class AdminRole(Base):
    """An RBAC tier for role=admin users — layered on top of RoleEnum.admin,
    not a replacement for it. Super Admin's permission set is special-cased
    in code (see crud/permissions.py) rather than stored in
    admin_role_permissions, so it can never be accidentally crippled by a
    bad grant edit."""

    __tablename__ = "admin_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # System roles (Super Admin / Admin / Support Admin) can't be renamed or
    # deleted. Only custom roles created via the Roles UI are is_system=False.
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    granted_permissions: Mapped[list["AdminRolePermission"]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )


class Permission(Base):
    """Static catalog of permission keys (e.g. "students.delete"). Seeded by
    seed.py, not user-editable via any API — only role<->permission grants
    (AdminRolePermission rows) are editable."""

    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    module: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdminRolePermission(Base):
    """Grant table: which permission keys a given admin role has. Super
    Admin never has rows here (see AdminRole docstring) — its access is
    computed, not stored."""

    __tablename__ = "admin_role_permissions"
    __table_args__ = (UniqueConstraint("admin_role_id", "permission_key", name="uq_admin_role_permission"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_role_id: Mapped[int] = mapped_column(ForeignKey("admin_roles.id"), nullable=False, index=True)
    permission_key: Mapped[str] = mapped_column(ForeignKey("permissions.key"), nullable=False, index=True)

    role: Mapped["AdminRole"] = relationship(back_populates="granted_permissions")
