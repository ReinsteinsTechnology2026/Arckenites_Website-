from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.core.deps import require_role
from app.database import get_db
from app.models.user import User

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/admin/system/health")
def system_health(db: Session = Depends(get_db), _admin: User = Depends(require_role("admin"))):
    """Only 3 real checks — no fake "Storage" status, since this backend has
    no storage service to check. Database is a real SELECT 1; Authentication
    is tied to DB + JWT config actually being reachable/configured (there's
    no separate identity provider in this architecture); API is self-evident
    from this endpoint having responded 200 at all."""
    try:
        db.execute(text("SELECT 1"))
        database_healthy = True
    except Exception:
        database_healthy = False

    auth_healthy = database_healthy and bool(settings.jwt_secret)

    return {
        "database": "healthy" if database_healthy else "unreachable",
        "authentication": "healthy" if auth_healthy else "degraded",
        "api": "healthy",
    }
