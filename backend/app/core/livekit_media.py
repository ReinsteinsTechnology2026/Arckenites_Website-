"""LiveKit implementation of the MediaServer interface, plus join tokens and
webhook verification. Uses the official, Apache-2.0 `livekit-api` SDK for every
wire format (tokens, Twirp requests, webhook signatures) rather than anything
hand-rolled.

Secrets: the API key/secret are read from settings, used to sign short-lived
tokens and to authenticate server-to-server calls, and are never logged,
returned, or put in an exception message.

NOTE ON VERIFICATION: the request construction here is unit-tested against the
SDK's own protobuf types and a fake client, which proves the shapes are valid
and that our safety logic is right. It does NOT prove a real LiveKit server
accepts them or that a real Egress produces a file. That is only established by
running the stack (infra/livekit) and recording for real.
"""
import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import aiohttp
from livekit import api

from app.config import settings
from app.core.media_server import (EGRESS_COMPLETE, EGRESS_FAILED, EGRESS_GONE, EGRESS_MISSING, EGRESS_OK,
                                   EGRESS_UNKNOWN, MediaServerNotConfigured, MediaServerUnavailable,
                                   require_class_room)

logger = logging.getLogger("livekit_media")

ACTIVE_EGRESS = (api.EgressStatus.EGRESS_STARTING, api.EgressStatus.EGRESS_ACTIVE)
# Twirp codes meaning "that thing is already gone / already finished".
GONE_CODES = {"not_found", "failed_precondition", "already_exists"}
FAILED_EGRESS = (api.EgressStatus.EGRESS_FAILED, api.EgressStatus.EGRESS_ABORTED, api.EgressStatus.EGRESS_LIMIT_REACHED)
MEDIA_SOURCES = (api.TrackSource.CAMERA, api.TrackSource.MICROPHONE, api.TrackSource.SCREEN_SHARE,
                 api.TrackSource.SCREEN_SHARE_AUDIO)
REQUEST_TIMEOUT_SECONDS = 15


def _require_configured() -> None:
    if not settings.livekit_configured:
        raise MediaServerNotConfigured("The meeting media server isn't configured.")


def room_join_token(*, identity: str, name: str, room: str, is_trainer: bool, run_id: int,
                    ttl_seconds: int | None = None) -> str:
    """A short-lived token that lets exactly one identity join exactly one room.

    Students may publish audio/video/screen and subscribe, but may not send
    arbitrary data messages (in-class chat uses Arckenites' own authenticated
    channel). The trainer additionally gets room-admin rights over THIS room
    only (to mute or remove participants). Nobody is ever granted the right to
    record or to create/list rooms: recording is controlled solely by the
    server, after the trainer's approval.
    """
    _require_configured()
    grants = api.VideoGrants(
        room_join=True, room=room, can_subscribe=True, can_publish=True,
        can_publish_data=is_trainer, room_admin=is_trainer,
    )
    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(identity).with_name(name)
        .with_ttl(timedelta(seconds=ttl_seconds or settings.livekit_token_ttl_seconds))
        .with_metadata(json.dumps({"role": "trainer" if is_trainer else "student", "run": run_id}))
        .with_grants(grants)
    )
    return token.to_jwt()


def meeting_join_token(*, identity: str, name: str, room: str, is_moderator: bool,
                       ttl_seconds: int | None = None) -> str:
    """Token for a GENERAL meeting room. Same shape as a class token but with a
    different metadata marker, no data publishing, and no room-admin right for
    anyone (the host moderates through Arckenites' own endpoints). Recording is
    not a grantable right in any token, and require_class_room() keeps Egress
    away from this room namespace."""
    _require_configured()
    grants = api.VideoGrants(room_join=True, room=room, can_subscribe=True, can_publish=True,
                             can_publish_data=False, room_admin=False)
    return (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(identity).with_name(name)
        .with_ttl(timedelta(seconds=ttl_seconds or settings.livekit_token_ttl_seconds))
        .with_metadata(json.dumps({"role": "moderator" if is_moderator else "participant", "kind": "meeting"}))
        .with_grants(grants)
        .to_jwt()
    )


def verify_webhook(body: str, auth_header: str) -> "api.WebhookEvent":
    """Authenticates a LiveKit webhook and returns the parsed event.

    The SDK checks the JWT signature with our API secret, that it has not
    expired, and that the token's `sha256` claim matches the exact body
    received, so neither a forged request nor a tampered body gets through.
    Raises on any failure (callers answer 401).
    """
    _require_configured()
    token = (auth_header or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise ValueError("missing webhook authorization")
    verifier = api.TokenVerifier(settings.livekit_api_key, settings.livekit_api_secret)
    return api.WebhookReceiver(verifier).receive(body, token)


class LiveKitMediaServer:
    def __init__(self, api_url: str, api_key: str, api_secret: str, *, output_prefix: str,
                 layout: str, preset: str, empty_timeout: int):
        self.api_url, self.api_key, self.api_secret = api_url, api_key, api_secret
        self.output_prefix = output_prefix.rstrip("/")
        self.layout, self.preset, self.empty_timeout = layout, preset, empty_timeout

    @classmethod
    def from_settings(cls) -> "LiveKitMediaServer":
        _require_configured()
        return cls(settings.livekit_server_api_url, settings.livekit_api_key, settings.livekit_api_secret,
                   output_prefix=settings.livekit_egress_output_prefix, layout=settings.livekit_recording_layout,
                   preset=settings.livekit_recording_preset, empty_timeout=settings.livekit_room_empty_timeout_seconds)

    # -- plumbing -------------------------------------------------------------
    def _client(self) -> "api.LiveKitAPI":
        return api.LiveKitAPI(url=self.api_url, api_key=self.api_key, api_secret=self.api_secret,
                              timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS))

    def _run(self, call):
        """Runs one async SDK call from synchronous code (job handlers and
        request handlers run in worker threads with no event loop).

        If a caller DOES have a running event loop (an `async def` route that
        reached sync code), asyncio.run() would raise "cannot be called from a
        running event loop" - which once turned every participant_left webhook
        into an HTTP 500. In that case the call runs on its own thread, with
        its own loop, so it works from any context."""
        async def go():
            client = self._client()
            try:
                return await call(client)
            finally:
                await client.aclose()
        try:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(go())
            with ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, go()).result()
        except api.TwirpError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise MediaServerUnavailable(f"media server unreachable ({type(exc).__name__})") from exc

    def _tolerate_gone(self, call):
        """Like _run, but 'already gone/finished' is success (idempotency)."""
        try:
            return self._run(call)
        except api.TwirpError as exc:
            if exc.code in GONE_CODES:
                return None
            raise MediaServerUnavailable(f"media server refused the request ({exc.code})") from exc

    def _checked(self, call):
        try:
            return self._run(call)
        except api.TwirpError as exc:
            raise MediaServerUnavailable(f"media server refused the request ({exc.code})") from exc

    # -- MediaServer interface -----------------------------------------------
    def ensure_room(self, room_name: str, metadata: str = "") -> None:
        request = api.CreateRoomRequest(name=room_name, empty_timeout=self.empty_timeout, metadata=metadata)
        try:
            self._run(lambda lk: lk.room.create_room(request))
        except api.TwirpError as exc:
            if exc.code != "already_exists":  # an existing room is exactly what we want
                raise MediaServerUnavailable(f"media server refused the request ({exc.code})") from exc

    def delete_room(self, room_name: str) -> None:
        self._tolerate_gone(lambda lk: lk.room.delete_room(api.DeleteRoomRequest(room=room_name)))

    def is_participant_present(self, room_name: str, identity: str) -> bool | None:
        request = api.RoomParticipantIdentity(room=room_name, identity=identity)
        try:
            self._run(lambda lk: lk.room.get_participant(request))
            return True
        except api.TwirpError as exc:
            return False if exc.code == "not_found" else None
        except MediaServerUnavailable:
            return None

    def room_has_published_media(self, room_name: str) -> bool | None:
        """True when any non-Egress participant publishes camera, microphone or
        screen share. Egress cannot start recording an empty stage, so the
        pipeline asks before starting it. None when the answer is unavailable."""
        try:
            response = self._checked(lambda lk: lk.room.list_participants(api.ListParticipantsRequest(room=room_name)))
        except MediaServerUnavailable:
            return None
        for participant in response.participants:
            if participant.identity.startswith("EG_"):
                continue  # the recorder itself
            if any(track.source in MEDIA_SOURCES for track in participant.tracks):
                return True
        return False

    def egress_health(self, room_name: str, egress_id: str) -> tuple[str, str]:
        """Is a recording we started still alive? Egress joins the room as a
        participant named after its egress id; a crashed or killed Egress
        container never reports back, but that participant disappears."""
        response = self._checked(lambda lk: lk.egress.list_egress(api.ListEgressRequest(egress_id=egress_id)))
        items = [i for i in response.items if i.egress_id == egress_id]
        if not items:
            return EGRESS_GONE, "egress_not_found"
        status = items[0].status
        if status in FAILED_EGRESS:
            return EGRESS_FAILED, "egress_" + api.EgressStatus.Name(status).removeprefix("EGRESS_").lower()
        if status == api.EgressStatus.EGRESS_COMPLETE:
            return EGRESS_COMPLETE, ""
        present = self.is_participant_present(room_name, egress_id)
        if present is None:
            return EGRESS_UNKNOWN, ""
        return (EGRESS_OK if present else EGRESS_MISSING), ""

    def find_active_recording(self, room_name: str) -> str | None:
        request = api.ListEgressRequest(room_name=room_name, active=True)
        response = self._checked(lambda lk: lk.egress.list_egress(request))
        for info in response.items:
            if info.status in ACTIVE_EGRESS:
                return info.egress_id
        return None

    def start_room_recording(self, room_name: str, staging_key: str) -> str:
        require_class_room(room_name)  # Egress only ever records an authorized batch-class room
        request = api.RoomCompositeEgressRequest(
            room_name=room_name, layout=self.layout,
            preset=getattr(api.EncodingOptionsPreset, self.preset),
            file_outputs=[api.EncodedFileOutput(file_type=api.EncodedFileType.MP4,
                                                filepath=f"{self.output_prefix}/{staging_key}")],
        )
        info = self._checked(lambda lk: lk.egress.start_room_composite_egress(request))
        if not info.egress_id:
            raise MediaServerUnavailable("media server did not return a recording id")
        return info.egress_id

    def set_participant_attributes(self, room_name: str, identity: str, attributes: dict[str, str]) -> None:
        request = api.UpdateParticipantRequest(room=room_name, identity=identity, attributes=attributes)
        self._tolerate_gone(lambda lk: lk.room.update_participant(request))   # left already: nothing to update

    def mute_participant_microphone(self, room_name: str, identity: str) -> None:
        ident = api.RoomParticipantIdentity(room=room_name, identity=identity)
        try:
            info = self._run(lambda lk: lk.room.get_participant(ident))
        except api.TwirpError as exc:
            if exc.code in GONE_CODES:
                return
            raise MediaServerUnavailable(f"media server refused the request ({exc.code})") from exc
        for track in info.tracks:
            if track.source == api.TrackSource.MICROPHONE and not track.muted:
                request = api.MuteRoomTrackRequest(room=room_name, identity=identity, track_sid=track.sid, muted=True)
                self._checked(lambda lk, r=request: lk.room.mute_published_track(r))

    def remove_participant(self, room_name: str, identity: str) -> None:
        request = api.RoomParticipantIdentity(room=room_name, identity=identity)
        self._tolerate_gone(lambda lk: lk.room.remove_participant(request))

    def stop_recording(self, egress_id: str) -> None:
        self._tolerate_gone(lambda lk: lk.egress.stop_egress(api.StopEgressRequest(egress_id=egress_id)))
