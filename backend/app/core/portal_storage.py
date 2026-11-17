"""Private, verified document storage. No public share links or token-bearing redirects."""
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from fastapi import HTTPException
from fastapi.responses import StreamingResponse, Response
from sqlalchemy import select, exists, or_, and_, cast, String, func
from app.config import settings
from app.core.onedrive_store import GRAPH, OneDriveStore, OneDriveError
from app.database import SessionLocal
from app.models.portal_file import PortalFile

log = logging.getLogger(__name__)


def document_store(row: PortalFile | None = None) -> OneDriveStore:
    return OneDriveStore(
        drive_id=row.drive_id if row else settings.onedrive_drive_id,
        folder=row.folder if row else settings.portal_onedrive_folder_path,
        enabled=settings.portal_onedrive_enabled,
    )


def ensure_folder(store: OneDriveStore) -> None:
    """Create only the configured hierarchy; existing names must be folders."""
    client = store._client()
    try:
        parent = f'{GRAPH}/drives/{quote(store.drive_id, safe="")}/root'
        for part in store.folder.split('/'):
            if not part or part in ('.', '..'):
                raise OneDriveError('Invalid portal storage folder.')
            found = client.get(f'{parent}:/{quote(part, safe="")}', headers=store._bearer())
            if found.status_code == 404:
                found = client.post(f'{parent}/children', headers=store._bearer(),
                    json={'name': part, 'folder': {}, '@microsoft.graph.conflictBehavior': 'fail'})
                if found.status_code == 409:
                    found = client.get(f'{parent}:/{quote(part, safe="")}', headers=store._bearer())
            if found.status_code not in (200, 201) or 'folder' not in found.json():
                raise OneDriveError('Could not access the portal storage folder.')
            parent = f'{GRAPH}/drives/{quote(store.drive_id, safe="")}/items/{quote(found.json()["id"], safe="")}'
    finally:
        if store._http is None:
            client.close()


def delete_remote(row: PortalFile) -> None:
    store = document_store(row)
    client = store._client()
    try:
        if row.remote_item_id:
            url = f'{GRAPH}/drives/{quote(row.drive_id, safe="")}/items/{quote(row.remote_item_id, safe="")}'
        else:
            # A crashed/failed upload has a deterministic UUID filename stored
            # durably before its upload began. Never delete unrelated objects.
            url = store._item_path(row.remote_name).removesuffix(":")
        response = client.delete(url, headers=store._bearer())
        if response.status_code not in (204, 404):
            raise OneDriveError('Remote file cleanup failed.')
    finally:
        if store._http is None:
            client.close()


def process_portal_cleanup() -> int:
    """Only durable deletion requests/expired failed uploads are removed.

    Remote failures retain their SQL metadata and are retried by the worker.
    Pending uploads get a 24-hour lease so active requests are not collected.
    """
    now = datetime.now(timezone.utc)
    from app.models.batch import Batch
    from app.models.chat import Conversation
    from app.models.support import SupportAttachment
    missing_owner = or_(
        and_(PortalFile.scope.in_(['materials', 'batch-chat', 'videos']),
             ~exists(select(Batch.id).where(Batch.id == PortalFile.target_id))),
        and_(PortalFile.scope == 'chat',
             ~exists(select(Conversation.id).where(Conversation.id == PortalFile.target_id))),
        and_(PortalFile.scope == 'support', ~exists(select(SupportAttachment.id).where(
             func.replace(SupportAttachment.storage_reference, '-', '') ==
             'portal:' + func.replace(cast(PortalFile.id, String), '-', '')))))
    with SessionLocal() as db:
        rows = db.scalars(select(PortalFile).where(
            (PortalFile.state.in_(['DELETING', 'FAILED'])) |
            ((PortalFile.state == 'PENDING') & (PortalFile.created_at < now - timedelta(hours=24))) |
            ((PortalFile.state == 'READY') & (PortalFile.created_at < now - timedelta(hours=24)) & missing_owner)
        ).order_by(PortalFile.created_at).limit(25).with_for_update(skip_locked=True)).all()
        done = 0
        for row in rows:
            try:
                delete_remote(row)
                db.delete(row)
                done += 1
            except Exception as exc:
                log.warning('Portal file cleanup pending id=%s error_type=%s', row.id, type(exc).__name__)
        db.commit()
        return done


def remote_response(store: OneDriveStore, item_id: str, *, size: int,
                    media_type: str, filename: str | None = None,
                    byte_range: tuple[int, int] | None = None, head: bool = False):
    """Open cloud content before returning headers, and close on disconnect.
    Callers MUST authorize the user against the owning resource first.
    """
    client = store._client()
    response = None
    try:
        url = f'{GRAPH}/drives/{quote(store.drive_id, safe="")}/items/{quote(item_id, safe="")}/content'
        headers = store._bearer()
        if byte_range is not None:
            headers['Range'] = f'bytes={byte_range[0]}-{byte_range[1]}'
        response = client.send(client.build_request('GET', url, headers=headers), stream=True, follow_redirects=True)
        expected_status = 206 if byte_range is not None else 200
        expected_length = byte_range[1] - byte_range[0] + 1 if byte_range is not None else size
        if response.status_code != expected_status:
            raise OneDriveError('Remote content unavailable.')
        length = response.headers.get('content-length')
        if length is not None and int(length) != expected_length:
            raise OneDriveError('Remote file size changed.')
        output = {'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
                  'Referrer-Policy': 'no-referrer', 'Content-Length': str(expected_length)}
        if filename is not None:
            output['Content-Disposition'] = "attachment; filename*=UTF-8''" + quote(filename, safe='')
        else:
            output.update({'Content-Disposition': 'inline', 'Accept-Ranges': 'bytes'})
        if byte_range is not None:
            output['Content-Range'] = f'bytes {byte_range[0]}-{byte_range[1]}/{size}'
            if response.headers.get('content-range') != output['Content-Range']:
                raise OneDriveError('Unexpected remote byte range.')
        if head:
            response.close()
            if store._http is None:
                client.close()
            return Response(status_code=expected_status, media_type=media_type, headers=output)
    except Exception:
        if response is not None:
            response.close()
        if store._http is None:
            client.close()
        raise HTTPException(503, 'File storage is temporarily unavailable. Please try again.')

    def body():
        try:
            remaining = expected_length
            for chunk in response.iter_bytes(256 * 1024):
                if len(chunk) > remaining:
                    raise OneDriveError('Remote response exceeded verified size.')
                remaining -= len(chunk)
                yield chunk
            if remaining:
                raise OneDriveError('Remote response ended before verified size.')
        finally:
            response.close()
            if store._http is None:
                client.close()
    return StreamingResponse(body(), status_code=expected_status, media_type=media_type, headers=output)
