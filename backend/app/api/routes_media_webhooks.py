import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import media_events
from app.core.livekit_media import verify_webhook
from app.core.media_server import MediaServerNotConfigured
from app.database import get_db
from app.models.class_run import MediaWebhookEvent

router = APIRouter(prefix="/media/webhooks", tags=["media-webhooks"])
logger = logging.getLogger("media_webhooks")
MAX_BODY_BYTES = 256 * 1024


@router.post("/livekit")
async def livekit_webhook(request: Request, db: Session = Depends(get_db)):
    """Receives LiveKit events. Authenticated by LiveKit's signed token, not by a
    user session: the token's signature, expiry, and the SHA-256 of the exact
    body are all verified before anything is read. Replays are harmless: the
    event id is stored in the same transaction as the event's effects."""
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Payload too large")
    try:
        event = verify_webhook(raw.decode("utf-8"), request.headers.get("authorization", ""))
    except MediaServerNotConfigured:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Media server isn't configured")
    except Exception:  # noqa: BLE001 - any verification failure is a plain 401; never echo why
        logger.warning("rejected a media webhook with an invalid signature")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook")

    if not event.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Event has no id")

    # The handlers are synchronous and may call the media server (which spins
    # up its own event loop), so they run in a worker thread rather than on the
    # server's event loop.
    return await run_in_threadpool(_process, db, event)


def _process(db: Session, event) -> dict:
    try:
        db.add(MediaWebhookEvent(id=event.id[:80], event=event.event[:40], room_name=(event.room.name or None)))
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"status": "duplicate"}

    try:
        outcome = media_events.handle_event(db, event)
        db.commit()
    except Exception:
        # Roll back the event id too, so LiveKit's retry is processed rather
        # than swallowed as a "duplicate".
        db.rollback()
        logger.exception("media webhook processing failed event=%s", event.event)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Processing failed")
    return {"status": outcome}
