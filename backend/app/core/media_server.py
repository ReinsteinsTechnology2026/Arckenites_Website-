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
* room_has_published_media / egress_health: optional (see the helpers below).
"""
import hashlib
import hmac
import re
from collections.abc import Callable
from typing import Protocol

from app.config import settings


class RecordingRefused(Exception):
    """Recording was refused on policy grounds: the room is not a batch-class
    room, or the class was not approved by its trainer. Permanent: retrying
    cannot change the answer."""


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

    # In-class actions, always scoped to ONE room by the server after it has
    # authorized the caller. They never touch recording.
    def set_participant_attributes(self, room_name: str, identity: str, attributes: dict[str, str]) -> None: ...

    def mute_participant_microphone(self, room_name: str, identity: str) -> None: ...

    def remove_participant(self, room_name: str, identity: str) -> None: ...


# Optional capabilities. A media server (or a test fake) that does not offer
# them is treated as "can't tell": callers fall back to the old behaviour
# (start immediately / rely on webhooks) instead of guessing.
EGRESS_OK, EGRESS_MISSING, EGRESS_FAILED, EGRESS_COMPLETE, EGRESS_GONE, EGRESS_UNKNOWN = (
    "ok", "missing", "failed", "complete", "gone", "unknown")


def room_has_published_media(media, room_name: str) -> bool | None:
    """True/False when the media server can say whether anyone in the room is
    publishing camera, microphone or screen share; None when it can't."""
    probe = getattr(media, "room_has_published_media", None)
    return None if probe is None else probe(room_name)


def egress_health(media, room_name: str, egress_id: str) -> tuple[str, str]:
    """(state, code): state is one of the EGRESS_* constants; code names the
    failure for EGRESS_FAILED/EGRESS_GONE ('' otherwise)."""
    probe = getattr(media, "egress_health", None)
    return (EGRESS_UNKNOWN, "") if probe is None else probe(room_name, egress_id)


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


def require_class_room(room_name: str) -> int:
    """The only rooms that may ever be recorded are batch-class rooms WE named
    (see room_name_for_run). A general-meeting room, a forged name or any other
    string is refused before a recording request is built, so even a bug
    elsewhere cannot make Egress record a general meeting."""
    run_id = run_id_from_room_name(room_name)
    if run_id is None:
        raise RecordingRefused("Recording is only possible for batch class rooms.")
    return run_id


# ---------------------------------------------------------------------------
# General-meeting rooms. Deliberately a DIFFERENT namespace from class rooms
# ("class-<id>-<hmac>"): require_class_room() only accepts class names, so a
# general-meeting room can never be passed to Egress.
# ---------------------------------------------------------------------------
_MEETING_ROOM_RE = re.compile(r"^meeting-(\d{1,12})-([0-9a-f]{16})$")


def _meeting_digest(meeting_id: int) -> str:
    return hmac.new(settings.jwt_secret.encode("utf-8"), f"general-meeting:{meeting_id}".encode("utf-8"),
                    hashlib.sha256).hexdigest()[:16]


def meeting_room_name(meeting_id: int) -> str:
    return f"meeting-{meeting_id}-{_meeting_digest(meeting_id)}"


def meeting_id_from_room_name(room_name: str) -> int | None:
    match = _MEETING_ROOM_RE.match(room_name or "")
    if match is None:
        return None
    meeting_id = int(match.group(1))
    return meeting_id if hmac.compare_digest(match.group(2), _meeting_digest(meeting_id)) else None
