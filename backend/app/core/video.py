import hashlib
import hmac
import secrets

from app.config import settings


def generate_batch_room_name(batch_id: int) -> str:
    """Deterministic, unguessable Jitsi room name for one batch — one
    permanent room reused across every class that batch has, so Batch A
    always meets in the same room and Batch B always meets in a different
    one. Same formula on both the student and trainer endpoints means both
    land in the identical room, and the HMAC keeps it unguessable from the
    batch id alone without needing a new secret/column."""
    digest = hmac.new(
        settings.jwt_secret.encode("utf-8"),
        f"batch:{batch_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:20]
    return f"ArckBatch-{batch_id}-{digest}"


def generate_adhoc_room_name() -> str:
    """Fresh, random room for an unscheduled/instant meeting — the room name
    itself is the shareable invite, same trust model as any Meet/Zoom link."""
    return f"ArckMeet-{secrets.token_urlsafe(9)}"
