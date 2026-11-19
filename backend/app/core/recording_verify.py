"""Decides whether a recording file is genuinely a playable recording.

A database row, a file that exists, or even a non-empty file is NOT proof. A
recording is accepted only if it can be opened as media, has a video stream,
every packet in it can be read to the end (a truncated or half-written file
fails here), frames can be decoded at the start AND near the end, and its
duration is plausible for the time the class was actually recorded.

Uses PyAV (FFmpeg bindings) so it needs no external ffprobe/ffmpeg binary.
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path

import av

logger = logging.getLogger("recording_verify")

MIN_SECONDS = 1.0
WINDOW_TOLERANCE_SECONDS = 120.0
HEAD_FRAMES = 30


@dataclass
class VerifyResult:
    ok: bool
    error_code: str | None = None
    detail: str | None = None
    duration_seconds: float | None = None
    size_bytes: int = 0
    has_video: bool = False
    has_audio: bool = False
    video_codec: str | None = None
    width: int | None = None
    height: int | None = None
    packets: int = 0
    info: dict = field(default_factory=dict)


def _fail(code: str, detail: str, **kw) -> VerifyResult:
    return VerifyResult(ok=False, error_code=code, detail=detail[:300], **kw)


def verify_media_file(path: Path, *, expected_window_seconds: float | None = None) -> VerifyResult:
    try:
        size = path.stat().st_size
    except OSError:
        return _fail("missing", "The recording file does not exist.")
    if size <= 0:
        return _fail("empty", "The recording file is empty.", size_bytes=0)

    try:
        container = av.open(str(path), mode="r")
    except Exception as exc:  # noqa: BLE001 - any failure to open means it is not playable media
        return _fail("unreadable", f"Not a readable media file ({type(exc).__name__}).", size_bytes=size)

    try:
        video = next((s for s in container.streams if s.type == "video"), None)
        audio = next((s for s in container.streams if s.type == "audio"), None)
        base = dict(size_bytes=size, has_video=video is not None, has_audio=audio is not None)
        if video is None:
            return _fail("no_video_stream", "The file has no video track.", **base)
        base.update(video_codec=video.codec_context.name, width=video.codec_context.width,
                    height=video.codec_context.height)

        # Read every packet. A file cut short mid-write errors or stops early here.
        packets, last_seconds = 0, 0.0
        try:
            for packet in container.demux():
                if packet.size == 0 or packet.pts is None:
                    continue
                packets += 1
                stream = container.streams[packet.stream_index]
                seconds = float(packet.pts * stream.time_base) if stream.time_base else 0.0
                if packet.stream_index == video.index:
                    last_seconds = max(last_seconds, seconds + float(packet.duration or 0) * float(stream.time_base))
        except Exception as exc:  # noqa: BLE001
            return _fail("corrupt_packets", f"Reading the file failed partway ({type(exc).__name__}).", packets=packets, **base)
        if packets == 0:
            return _fail("no_packets", "The file contains no media data.", **base)

        duration = (container.duration / av.time_base) if container.duration else last_seconds
        duration = max(duration, last_seconds) if duration else last_seconds
        base["duration_seconds"] = round(duration, 3)
        if duration < MIN_SECONDS:
            return _fail("too_short", f"Recording is only {duration:.1f}s long.", packets=packets, **base)
        if expected_window_seconds is not None:
            if duration > expected_window_seconds + WINDOW_TOLERANCE_SECONDS:
                return _fail("duration_implausible", f"Duration {duration:.0f}s exceeds the recorded window "
                             f"({expected_window_seconds:.0f}s).", packets=packets, **base)
            if expected_window_seconds >= 2 * WINDOW_TOLERANCE_SECONDS and duration < expected_window_seconds - WINDOW_TOLERANCE_SECONDS:
                return _fail("duration_implausible", f"Duration {duration:.0f}s is much shorter than the recorded "
                             f"window ({expected_window_seconds:.0f}s).", packets=packets, **base)

        # Decode from the start, then from near the end.
        try:
            container.seek(0)
            decoded = 0
            for frame in container.decode(video=0):
                decoded += 1
                if decoded >= HEAD_FRAMES:
                    break
            if decoded == 0:
                return _fail("no_decodable_frames", "No video frame could be decoded.", packets=packets, **base)
            if duration > 4:
                container.seek(int(duration * 0.9 * av.time_base))
                tail = 0
                for frame in container.decode(video=0):
                    tail += 1
                    break
                if tail == 0:
                    return _fail("tail_not_decodable", "The end of the recording could not be decoded.", packets=packets, **base)
        except Exception as exc:  # noqa: BLE001
            return _fail("decode_error", f"Decoding failed ({type(exc).__name__}).", packets=packets, **base)

        return VerifyResult(ok=True, packets=packets, info={"frames_checked_head": decoded}, **base)
    finally:
        container.close()
