"""The boundary between Arckenites and the self-hosted media server.

The class lifecycle and recording pipeline only ever talk to this interface.
`LiveKitMediaServer` (app/core/livekit_media.py) is the real implementation and
is used automatically once LiveKit is configured; tests plug a fake in with
`set_media_server_factory`. When nothing is configured, `get_media_server()`
raises `MediaServerNotConfigured`, and callers must report that truthfully
instead of pretending a room or recording exists.

Contract every implementation must honour (callers rely on it for
idempotency, since jobs are delivered at least once):

* ensure_room: succeeds if the room already exists.
* delete_room: succeeds if the room is already gone.
* find_active_recording: returns the egress id of a live recording for the
  room, or None. Used to adopt a recording instead of starting a second one
  after a crash between "started" and "saved".
* stop_recording: succeeds if the recording already stopped.
* is_participant_present: True/False, or None when it can't be determined.
"""
import hashlib
import hmac
import re
from collections.abc import Callable
from typing import Protocol

from app.config import settings


class MediaServerUnavailable(Exception):
    """The media server could not be reached or refused the request. Transient."""


class MediaServerNotConfigured(MediaServerUnavailable):
    """No media server is configured at all, so there is nothing to talk to."""


class MediaServer(Protocol):
    def ensure_room(self, room_name: str, metadata: str = "") -> None: ...

    def delete_room(self, room_name: str) -> None: ...

    def is_participant_present(self, room_name: str, identity: str) -> bool | None: ...

    def find_active_recording(self, room_name: str) -> str | None: ...

    def start_room_recording(self, room_name: str, staging_key: str) -> str: ...

    def stop_recording(self, egress_id: str) -> None: ...


_factory: Callable[[], MediaServer] | None = None


def set_media_server_factory(factory: Callable[[], MediaServer] | None) -> None:
    global _factory
    _factory = factory


def get_media_server() -> MediaServer:
    if _factory is not None:
        return _factory()
    if not settings.livekit_configured:
        raise MediaServerNotConfigured("The meeting media server isn't configured.")
    from app.core.livekit_media import LiveKitMediaServer
    return LiveKitMediaServer.from_settings()


def _room_digest(run_id: int) -> str:
    return hmac.new(settings.jwt_secret.encode("utf-8"), f"class-run:{run_id}".encode("utf-8"),
                    hashlib.sha256).hexdigest()[:16]


def room_name_for_run(run_id: int) -> str:
    """Deterministic room name for an occurrence. The HMAC suffix keeps it
    unguessable from the id alone; access is still gated by short-lived,
    room-scoped tokens that only the Arckenites backend issues."""
    return f"class-{run_id}-{_room_digest(run_id)}"


_ROOM_RE = re.compile(r"^class-(\d{1,12})-([0-9a-f]{16})$")


def run_id_from_room_name(room_name: str) -> int | None:
    """Inverse of room_name_for_run. Only a name WE generated parses: the HMAC
    is checked in constant time, so a forged or foreign room never maps to a
    class, even if a webhook for it were somehow delivered."""
    match = _ROOM_RE.match(room_name or "")
    if match is None:
        return None
    run_id = int(match.group(1))
    return run_id if hmac.compare_digest(match.group(2), _room_digest(run_id)) else None
