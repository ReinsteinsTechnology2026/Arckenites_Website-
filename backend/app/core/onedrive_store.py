"""Final storage of recordings in the company OneDrive, via Microsoft Graph.

Disabled by default and fail-closed: it is only "ready" when upload is enabled
AND a destination drive is configured AND Graph credentials exist. Nothing here
creates sharing links; recordings stay private and are only ever streamed
through authorized Arckenites endpoints.

Upload uses Graph's resumable upload session: the file is sent in chunks (a
multiple of 320 KiB), a failed chunk is resumed from the server's own record of
what it has, and the finished item is verified against the local file's size
AND QuickXorHash (OneDrive for Business' content hash) before it is trusted.

NOT VERIFIED AGAINST A REAL TENANT. The request flow follows Microsoft's
documented API and is exercised against a simulated Graph in tests; it has
never talked to the real service. The QuickXorHash implementation is checked
against an independent transcription of Microsoft's published reference
algorithm, not against Microsoft's output. Both are proven only by a real
upload in the company drive (a mismatch would fail closed, never publish).
"""
import base64
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

from app.config import settings

logger = logging.getLogger("onedrive_store")

GRAPH = "https://graph.microsoft.com/v1.0"
CHUNK_MULTIPLE = 320 * 1024
MAX_CHUNK_ATTEMPTS = 5
HASH_WAIT_ATTEMPTS = 6
HASH_WAIT_SECONDS = 5


class OneDriveError(Exception):
    """A failure to store or verify. Carries a short, secret-free message."""


class OneDriveNotReady(OneDriveError):
    pass


@dataclass(frozen=True)
class RemoteObject:
    item_id: str
    size: int
    quick_xor_hash: str


# --- QuickXorHash -----------------------------------------------------------

_WIDTH = 160
_BLOCK = 160  # bytes; the shift pattern repeats every 160 bytes
_MASK = (1 << _WIDTH) - 1


def quick_xor_hash_b64(path: Path, *, read_blocks: int = 65536) -> str:
    """Microsoft's QuickXorHash of a file, base64 encoded.

    Byte i is XORed into a 160-bit register, rotated left by (11*i) mod 160
    bits; the 64-bit file length is XORed into the top 64 bits at the end. The
    shift repeats every 160 bytes, so the file is first XOR-folded into 160
    byte positions (fast, C-level big-int XOR) and rotated once per position.
    """
    acc = 0
    length = 0
    chunk = _BLOCK * read_blocks
    with path.open("rb") as handle:
        while True:
            block = bytearray()
            while len(block) < chunk:  # keep every non-final block a multiple of 160 bytes
                more = handle.read(chunk - len(block))
                if not more:
                    break
                block += more
            if not block:
                break
            length += len(block)
            if len(block) % _BLOCK:
                block += bytes(_BLOCK - len(block) % _BLOCK)
            view = memoryview(block)
            for offset in range(0, len(block), _BLOCK):
                acc ^= int.from_bytes(view[offset:offset + _BLOCK], "little")
            if len(block) < chunk:
                break
    register = 0
    for position in range(_BLOCK):
        value = (acc >> (8 * position)) & 0xFF
        shifted = value << ((11 * position) % _WIDTH)
        register ^= (shifted | (shifted >> _WIDTH)) & _MASK  # circular rotate within 160 bits
    register ^= (length & ((1 << 64) - 1)) << (_WIDTH - 64)
    return base64.b64encode(register.to_bytes(_WIDTH // 8, "little")).decode("ascii")


# --- the store --------------------------------------------------------------

class OneDriveStore:
    provider = "ONEDRIVE"

    def __init__(self, *, drive_id: str | None = None, folder: str | None = None, chunk_bytes: int | None = None,
                 token_provider=None, http: httpx.Client | None = None, sleep=time.sleep, enabled: bool | None = None):
        self._enabled = enabled
        self.drive_id = settings.onedrive_drive_id if drive_id is None else drive_id
        self.folder = (settings.onedrive_folder_path if folder is None else folder).strip("/")
        self.chunk_bytes = chunk_bytes or settings.onedrive_chunk_bytes
        if self.chunk_bytes % CHUNK_MULTIPLE:
            raise ValueError("chunk size must be a multiple of 320 KiB")
        self._token_provider = token_provider
        self._http = http
        self._sleep = sleep

    def readiness(self) -> tuple[bool, str | None]:
        if not (settings.onedrive_upload_enabled if self._enabled is None else self._enabled):
            return False, "OneDrive upload is disabled until access and the destination are verified."
        if not self.drive_id:
            return False, "No OneDrive destination drive is configured."
        if self._token_provider is None and not settings.ms_graph_configured:
            return False, "Microsoft Graph isn't configured."
        return True, None

    # -- plumbing ----------------------------------------------------------
    def _client(self) -> httpx.Client:
        return self._http or httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0))

    def _bearer(self) -> dict[str, str]:
        if self._token_provider is not None:
            token = self._token_provider()
        else:
            from app.core.ms_graph_email import _acquire_token
            token = _acquire_token()
        if not token:
            raise OneDriveError("Could not obtain a Microsoft Graph token.")
        return {"Authorization": f"Bearer {token}"}

    def _item_path(self, remote_name: str) -> str:
        parts = [quote(p, safe="") for p in [*self.folder.split("/"), remote_name] if p]
        return f"{GRAPH}/drives/{quote(self.drive_id, safe='')}/root:/{'/'.join(parts)}:"

    # -- upload --------------------------------------------------------------
    def upload_file(self, local_path: Path, remote_name: str) -> RemoteObject:
        """Uploads and returns the verified remote object. Raises OneDriveError
        on any failure; never returns an object that was not verified."""
        ready, reason = self.readiness()
        if not ready:
            raise OneDriveNotReady(reason)
        total = local_path.stat().st_size
        if total <= 0:
            raise OneDriveError("Refusing to upload an empty file.")
        expected_hash = quick_xor_hash_b64(local_path)

        client = self._client()
        try:
            session = client.post(
                f"{self._item_path(remote_name)}/createUploadSession",
                headers={**self._bearer(), "Content-Type": "application/json"},
                json={"item": {"@microsoft.graph.conflictBehavior": "replace", "name": remote_name}},
            )
            if session.status_code != 200:
                raise OneDriveError(f"Could not start an upload session (HTTP {session.status_code}).")
            upload_url = session.json()["uploadUrl"]  # pre-authenticated: never logged, never persisted
            item = self._send_chunks(client, upload_url, local_path, total)
            return self._verify(client, item["id"], total, expected_hash)
        except httpx.HTTPError as exc:
            raise OneDriveError(f"Network error talking to OneDrive ({type(exc).__name__}).") from exc
        finally:
            if self._http is None:
                client.close()

    def _next_expected(self, client: httpx.Client, upload_url: str) -> int | None:
        """Asks the upload session how much it already has (resume point)."""
        try:
            response = client.get(upload_url)
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        ranges = response.json().get("nextExpectedRanges") or []
        return int(ranges[0].split("-")[0]) if ranges else None

    def _send_chunks(self, client: httpx.Client, upload_url: str, path: Path, total: int) -> dict:
        start, failures = 0, 0
        with path.open("rb") as handle:
            while True:
                end = min(start + self.chunk_bytes, total) - 1
                handle.seek(start)
                data = handle.read(end - start + 1)
                try:
                    response = client.put(upload_url, content=data, headers={
                        "Content-Length": str(len(data)), "Content-Range": f"bytes {start}-{end}/{total}"})
                except httpx.HTTPError:
                    response = None
                if response is not None and response.status_code in (200, 201):
                    return response.json()
                if response is not None and response.status_code == 202:
                    ranges = response.json().get("nextExpectedRanges") or []
                    start = int(ranges[0].split("-")[0]) if ranges else end + 1
                    failures = 0
                    continue
                if response is not None and response.status_code in (404, 410):
                    raise OneDriveError("The upload session expired.")
                failures += 1
                if failures >= MAX_CHUNK_ATTEMPTS:
                    code = response.status_code if response is not None else "network"
                    raise OneDriveError(f"Upload kept failing (last: {code}).")
                self._sleep(min(2 ** failures, 30))
                resume = self._next_expected(client, upload_url)  # resume from what the server really has
                if resume is not None:
                    start = resume

    # -- verification ----------------------------------------------------------
    def _verify(self, client: httpx.Client, item_id: str, expected_size: int, expected_hash: str) -> RemoteObject:
        url = f"{GRAPH}/drives/{quote(self.drive_id, safe='')}/items/{quote(item_id, safe='')}?$select=id,size,file"
        for attempt in range(HASH_WAIT_ATTEMPTS):
            response = client.get(url, headers=self._bearer())
            if response.status_code != 200:
                raise OneDriveError(f"Could not read back the uploaded file (HTTP {response.status_code}).")
            body = response.json()
            if int(body.get("size", -1)) != expected_size:
                raise OneDriveError("The uploaded file's size does not match.")
            remote_hash = ((body.get("file") or {}).get("hashes") or {}).get("quickXorHash")
            if remote_hash:
                if remote_hash != expected_hash:
                    raise OneDriveError("The uploaded file's content hash does not match.")
                return RemoteObject(item_id=body["id"], size=expected_size, quick_xor_hash=remote_hash)
            self._sleep(HASH_WAIT_SECONDS)  # OneDrive computes the hash shortly after upload completes
        raise OneDriveError("OneDrive has not reported a content hash to verify against.")
