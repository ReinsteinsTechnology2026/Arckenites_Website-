"""Authorized playback of published class recordings.

* A <video> element cannot send an Authorization header, so playback uses a
  short-lived token minted by an authenticated, authorized call. The token is
  scoped to ONE recording and ONE user and is useless anywhere else.
* The stream endpoint re-checks, from the database, that the user is STILL
  allowed to watch on EVERY request (including each byte-range request), so
  removing a student from a batch stops their playback within the token's life
  and blocks any new token immediately.
* Nothing exposes a filesystem path or storage URL. Responses are private and
  uncacheable. There is no download endpoint or button; as with any web
  video, this controls access, it cannot make capture impossible.
"""
import re
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core import class_runs
from app.core.deps import get_current_user
from app.core.rate_limit import limiter
from app.core.recording_storage import StagingStore, StorageError
from app.crud.audit import write_audit_event
from app.crud.permissions import user_has_permission
from app.database import get_db
from app.models.audit_log import AuthEventType
from app.models.batch import Batch
from app.models.class_run import PUBLICATION_PUBLISHED, ClassRecording
from app.models.user import RoleEnum, User

router = APIRouter(prefix="/recordings", tags=["recordings"])

# Long enough to cover the byte-range requests of a long lesson; the player also
# refreshes it. Access is re-checked from the database on every request, so the
# token only identifies the viewer - it never keeps a revoked viewer watching.
PLAYBACK_TTL = timedelta(minutes=30)
PURPOSE = "recording-playback"
CHUNK = 256 * 1024
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording not found")


def _load(db: Session, recording_id: str) -> ClassRecording:
    try:
        rid = uuid.UUID(recording_id)
    except ValueError:
        raise _not_found()
    rec = db.get(ClassRecording, rid)
    if rec is None or rec.publication_state != PUBLICATION_PUBLISHED or rec.deleted_at is not None:
        raise _not_found()
    return rec


def may_watch(db: Session, user: User, rec: ClassRecording) -> bool:
    """Current standing, evaluated fresh each time: an enrolled student of the
    recording's own batch, that batch's trainer, or an admin allowed to view
    recordings. Students of other batches are never allowed."""
    if not user.is_active:
        return False
    batch = db.get(Batch, rec.batch_id)
    if batch is None:
        return False
    if user.role == RoleEnum.student:
        return class_runs.is_enrolled(db, user, batch)
    if user.role == RoleEnum.staff:
        return batch.trainer_id == user.id
    if user.role == RoleEnum.admin:
        return user_has_permission(db, user, "recordings.view")
    return False


@router.post("/{recording_id}/playback-token")
@limiter.limit("60/minute")
def mint_playback_token(recording_id: str, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    rec = _load(db, recording_id)
    if not may_watch(db, user, rec):
        raise _not_found()  # don't confirm another batch's recording exists
    now = datetime.now(timezone.utc)
    token = jwt.encode({"purpose": PURPOSE, "rid": str(rec.id), "sub": str(user.id), "iat": now,
                        "exp": now + PLAYBACK_TTL, "nonce": uuid.uuid4().hex}, settings.jwt_secret, algorithm="HS256")
    ip = request.client.host if request.client else "unknown"
    write_audit_event(db, AuthEventType.recording_playback, ip, request.headers.get("user-agent", "unknown"),
                      user=user, detail="Playback authorized", module="recordings", target=f"recording:{rec.id}",
                      status="success")
    return {"stream_url": f"/api/recordings/{rec.id}/stream?t={token}", "expires_in": int(PLAYBACK_TTL.total_seconds())}


def _user_from_token(db: Session, token: str, recording_id: uuid.UUID) -> User:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], options={"require": ["exp", "sub", "rid"]})
        if claims.get("purpose") != PURPOSE or claims["rid"] != str(recording_id):
            raise ValueError("wrong scope")
        user = db.get(User, int(claims["sub"]))
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Playback link expired or invalid")
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Playback link expired or invalid")
    return user


def parse_range(header: str | None, size: int) -> tuple[int, int] | None | str:
    """None = serve the whole file; 'invalid' = 416; else inclusive (start, end).
    Only a single byte range is honoured; anything fancier gets the full file,
    which RFC 9110 permits."""
    if not header:
        return None
    match = _RANGE_RE.match(header.strip())
    if match is None:
        return None
    first, last = match.groups()
    if first == "" and last == "":
        return None
    if first == "":  # suffix: the last N bytes
        count = int(last)
        if count == 0:
            return "invalid"
        return max(size - count, 0), size - 1
    start = int(first)
    end = size - 1 if last == "" else min(int(last), size - 1)
    if start >= size or start > end:
        return "invalid"
    return start, end


def _iter_file(path, start: int, end: int):
    remaining = end - start + 1
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            data = handle.read(min(CHUNK, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data


@router.api_route("/{recording_id}/stream", methods=["GET", "HEAD"])
def stream_recording(recording_id: str, request: Request, t: str = Query(min_length=10, max_length=2000),
                     db: Session = Depends(get_db)):
    rec = _load(db, recording_id)
    user = _user_from_token(db, t, rec.id)
    if not may_watch(db, user, rec):
        raise _not_found()

    store = StagingStore()
    try:
        path = store.path_for(rec.staging_key or "")
    except StorageError:
        raise _not_found()
    if not path.is_file() or (rec.size_bytes is not None and path.stat().st_size != rec.size_bytes):
        # The verified local copy is missing or changed. Serving from the
        # OneDrive copy is not implemented yet; fail honestly, never guess.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="This recording is temporarily unavailable")
    size = path.stat().st_size

    headers = {
        "Accept-Ranges": "bytes", "Content-Type": "video/mp4", "Content-Disposition": "inline",
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
    }
    window = parse_range(request.headers.get("range"), size)
    if window == "invalid":
        return Response(status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                        headers={**headers, "Content-Range": f"bytes */{size}"})
    if window is None:
        start, end, code = 0, size - 1, status.HTTP_200_OK
    else:
        (start, end), code = window, status.HTTP_206_PARTIAL_CONTENT
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(end - start + 1)
    if request.method == "HEAD":
        return Response(status_code=code, headers=headers)
    return StreamingResponse(_iter_file(path, start, end), status_code=code, headers=headers)
