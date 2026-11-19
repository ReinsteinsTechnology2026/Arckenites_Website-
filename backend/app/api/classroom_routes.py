"""Which LiveKit class routes exist, decided in one place.

* `routes_classroom_config` (the availability check) is always registered: it only reports a
  boolean, so the browser can choose between the LiveKit classroom and the existing Jitsi
  meeting room without guessing.
* The class routes themselves (join, approval, notes, actions, recordings, media webhooks) are
  registered outside production, and in production only when LIVEKIT_CLASSROOM_ENABLED is set.
"""
from fastapi import FastAPI

from app.api.routes_class_actions import router as class_actions_router
from app.api.routes_class_notes import router as class_notes_router
from app.api.routes_class_runs import router as class_runs_router
from app.api.routes_classroom_config import router as classroom_config_router
from app.api.routes_media_webhooks import router as media_webhooks_router
from app.api.routes_recordings import router as recordings_router
from app.config import settings


def include_classroom_routes(app: FastAPI, prefix: str = "/api") -> bool:
    """Registers the availability endpoint always, and the class routes when enabled.
    Returns whether the class routes were registered."""
    app.include_router(classroom_config_router, prefix=prefix)
    if not settings.classroom_routes_enabled:
        return False
    for router in (class_runs_router, class_notes_router, class_actions_router, media_webhooks_router, recordings_router):
        app.include_router(router, prefix=prefix)
    return True
