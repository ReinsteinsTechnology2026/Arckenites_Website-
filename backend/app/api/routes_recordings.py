"""Authorized playback of published class recordings.

* A <video> element cannot send an Authorization header, so playback uses a
  short-lived token minted by an authenticated, authorized call. The token is
  scoped to ONE recording and ONE user and is useless anywhere else.
* The stream endpoint re-checks, from the database, that the user is STILL
  allowed to watch on EVERY request (including each byte-range request), so
  removing a student from a batch stops their playback within the token's life
  and blocks any new token immediately.
* When the local staging copy is gone (e.g. after retention cleanup) the same
  endpoint streams the verified OneDrive copy THROUGH this server: the browser
  only ever sees an Arckenites URL; Graph credentials, OneDrive item ids and
  Microsoft download URLs stay server-side. Authorization is identical.
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
from app.core.onedrive_store import OneDriveStore
from app.core.recording_storage import StagingStore, StorageError
from app.crud.audit import write_audit_event
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
    """Current standing, evaluated fresh each time: a CURRENTLY ENROLLED student
    of the recording's own batch, or that batch's assigned trainer. Nobody else.

    Admins and Super Admins are deliberately NOT here: they may see a class's
    recording status and non-sensitive metadata (see class_runs.can_view_status)
    but can never play or download the recording. Students of other batches,
    removed students, other trainers and deactivated accounts are refused."""
    if not user.is_active:
        return False
    batch = db.get(Batch, rec.batch_id)
    if batch is None:
        return False
    if user.role == RoleEnum.student:
        return class_runs.is_enrolled(db, user, batch)
    if user.role == RoleEnum.staff:
        return batch.trainer_id == user.id
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


def _new_store(drive_id: str | None = None) -> OneDriveStore:
    """The recording's own drive when known; NULL (older rows) uses the configured one."""
    return OneDriveStore(drive_id=drive_id or None)


def _remote_source(rec: ClassRecording) -> OneDriveStore | None:
    """The OneDrive store to read from, only if this recording has a recorded,
    verified remote copy of known size and the store can currently be read."""
    if (rec.storage_provider != OneDriveStore.provider or not rec.remote_item_id or rec.remote_verified_at is None
            or not rec.size_bytes):
        return None
    store = _new_store(rec.remote_drive_id)
    ready, _ = store.read_readiness()
    return store if ready else None


def _stream_remote(store: OneDriveStore, rec: ClassRecording, size: int, byte_range, headers: dict):
    from app.core.portal_storage import remote_response  # shared, already-hardened Graph streaming
    upstream = remote_response(store, rec.remote_item_id, size=size, media_type="video/mp4",
                               byte_range=byte_range, head=False)
    # Our own headers (no-store, nosniff, inline, ...) win; upstream's body/length are verified by remote_response.
    upstream.headers.update({k: v for k, v in headers.items()})
    return upstream


@router.api_route("/{recording_id}/stream", methods=["GET", "HEAD"])
@limiter.limit("300/minute")  # generous: a player issues many Range requests; this stops scraping/abuse
def stream_recording(recording_id: str, request: Request, t: str = Query(min_length=10, max_length=2000),
                     db: Session = Depends(get_db)):
    rec = _load(db, recording_id)
    user = _user_from_token(db, t, rec.id)
    if not may_watch(db, user, rec):
        raise _not_found()

    store = StagingStore()
    path = None
    try:
        candidate = store.path_for(rec.staging_key or "")
        if candidate.is_file() and (rec.size_bytes is None or candidate.stat().st_size == rec.size_bytes):
            path = candidate
    except StorageError:
        pass
    remote = None
    if path is None:
        remote = _remote_source(rec)
        if remote is None:
            # Neither a matching local copy nor a verified remote copy we can read: fail honestly, never guess.
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="This recording is temporarily unavailable")
    size = rec.size_bytes if path is None else path.stat().st_size

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
    if remote is not None:
        return _stream_remote(remote, rec, size, None if window is None else (start, end), headers)
    return StreamingResponse(_iter_file(path, start, end), status_code=code, headers=headers)
