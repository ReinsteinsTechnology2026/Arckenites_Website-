"""Minimal API for the standalone feedback site (https://feedbackform.arckenites.com).

It serves only POST /api/feedback/inauguration (plus GET /api/health). Feedback goes straight to
the Google Sheet through app/core/feedback_sheets.py and nothing is stored here. No login, student,
admin, meeting or recording routes exist in this app, and no database connection is ever opened.

Run it with its own environment file (see backend/FEEDBACK_FORM_DEPLOYMENT.md):
    uvicorn app.feedback_app:app --host 127.0.0.1 --port 8100 --proxy-headers --forwarded-allow-ips 127.0.0.1
"""
from app.config import settings

# Fail closed BEFORE anything else is imported: this service never uses a database, so it must not
# even be configured with a real one. The settings loader also reads a ".env" file from the working
# directory; if one with the production DATABASE_URL were ever present and the service's own
# environment omitted DATABASE_URL, this refuses to start rather than quietly pointing at PostgreSQL.
# (The message deliberately does not print the URL.)
if not settings.database_url.startswith("sqlite"):
    raise RuntimeError(
        "app.feedback_app is database-free: set DATABASE_URL=sqlite:// in its own environment file. Refusing to start."
    )

from fastapi import FastAPI, Request  # noqa: E402  (deliberately after the guard)
from fastapi.responses import JSONResponse  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402
from slowapi.middleware import SlowAPIMiddleware  # noqa: E402

from app.api.routes_inauguration_feedback import router as inauguration_feedback_router  # noqa: E402
from app.core.rate_limit import limiter  # noqa: E402

# Same wording as the main API's feedback 429 (app/main.py); a test keeps the two in step.
RATE_LIMIT_MESSAGE = "You have sent feedback several times in a short while. Please wait a minute and try again."

# No interactive docs or OpenAPI schema on a public form endpoint.
app = FastAPI(title="Arckenites feedback form", docs_url=None, redoc_url=None, openapi_url=None)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    # An ordinary response with no database access (the main API's handler writes an audit row).
    return JSONResponse(status_code=429, headers={"Retry-After": "60"}, content={"detail": RATE_LIMIT_MESSAGE})


@app.get("/api/health")
def health():
    return {"status": "ok"}


app.include_router(inauguration_feedback_router, prefix="/api")
