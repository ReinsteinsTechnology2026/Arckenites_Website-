from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.responses import JSONResponse

from app.api.routes_admin_batches import router as admin_batches_router
from app.api.routes_admin_class_schedule import router as admin_class_schedule_router
from app.api.routes_admin_contact_enquiries import router as admin_contact_enquiries_router
from app.api.routes_admin_interviews import router as admin_interviews_router
from app.api.routes_admin_lab_access import router as admin_lab_access_router
from app.api.routes_admin_meetings import router as admin_meetings_router
from app.api.routes_meetings import router as meetings_router
from app.api.routes_admin_programs import router as admin_programs_router
from app.api.routes_admin_staff import router as admin_staff_router
from app.api.routes_admin_students import router as admin_students_router
from app.api.routes_admin_activity_logs import router as admin_activity_logs_router
from app.api.routes_admin_roles import router as admin_roles_router
from app.api.routes_admin_settings import router as admin_settings_router
from app.api.routes_admin_support import router as admin_support_router
from app.api.routes_admin_users import router as admin_users_router
from app.api.routes_auth import router as auth_router
from app.api.routes_chat import router as chat_router
from app.api.routes_contact import router as contact_router
from app.api.routes_dashboard import router as dashboard_router
from app.api.routes_health import router as health_router
from app.api.routes_profile import router as profile_router
from app.api.routes_sessions import router as sessions_router
from app.api.routes_staff import router as staff_router
from app.api.routes_students import router as students_router
from app.api.routes_video import router as video_router
from app.config import settings
from app.core.rate_limit import limiter
from app.crud.audit import write_audit_event
from app.database import SessionLocal
from app.models.audit_log import AuthEventType

app = FastAPI(title="Arckenites Portal API")

app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    # Best-effort: the request body may not be JSON (or may already be
    # consumed) at this point — a failed peek just means username stays None.
    username = None
    try:
        body = await request.json()
        username = body.get("username")
    except Exception:
        pass

    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "unknown")

    db = SessionLocal()
    try:
        write_audit_event(
            db, AuthEventType.rate_limited, ip, ua, username_attempted=username, detail="login rate limit exceeded"
        )
    finally:
        db.close()

    return JSONResponse(status_code=429, content={"detail": "Too many requests. Please try again shortly."})


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(health_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(dashboard_router, prefix="/api")
app.include_router(admin_students_router, prefix="/api")
app.include_router(admin_staff_router, prefix="/api")
app.include_router(admin_batches_router, prefix="/api")
app.include_router(admin_class_schedule_router, prefix="/api")
app.include_router(admin_interviews_router, prefix="/api")
app.include_router(admin_lab_access_router, prefix="/api")
app.include_router(admin_meetings_router, prefix="/api")
app.include_router(meetings_router, prefix="/api")
app.include_router(admin_programs_router, prefix="/api")
app.include_router(admin_contact_enquiries_router, prefix="/api")
app.include_router(admin_users_router, prefix="/api")
app.include_router(admin_roles_router, prefix="/api")
app.include_router(admin_activity_logs_router, prefix="/api")
app.include_router(admin_settings_router, prefix="/api")
app.include_router(admin_support_router, prefix="/api")
app.include_router(sessions_router, prefix="/api")
app.include_router(students_router, prefix="/api")
app.include_router(staff_router, prefix="/api")
app.include_router(profile_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(contact_router, prefix="/api")
app.include_router(video_router, prefix="/api")
