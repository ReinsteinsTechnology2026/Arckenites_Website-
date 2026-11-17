"""Verified private-file persistence with bounded temporary staging."""
import logging
import tempfile
import uuid
from pathlib import Path
from fastapi import HTTPException
from app.core.portal_storage import document_store, ensure_folder
from app.core.uploads import ALLOWED_EXTENSIONS, MAX_FILE_SIZE_BYTES, _content_matches_extension
from app.models.portal_file import PortalFile

log = logging.getLogger(__name__)
MAX_VIDEO_BYTES = 250 * 1024 * 1024


def store_document(db, user, scope, target, file, data=None):
    store = document_store()
    if not store.readiness()[0]:
        raise HTTPException(503, 'File uploads are not enabled. Contact your administrator.')
    name = (file.filename or 'upload').replace(chr(92), '/').split('/')[-1][:255]
    ext = Path(name).suffix.lower()
    is_video = scope == 'videos'
    if is_video and ext != '.mp4':
        raise HTTPException(400, 'Class recordings must be MP4 files.')
    if not is_video and ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, 'Unsupported file type.')
    rid = uuid.uuid4()
    store.folder = store.folder.rstrip('/') + '/' + {
        'materials':'Materials', 'batch-chat':'BatchChat', 'chat':'Chats', 'support':'Support', 'videos':'Recordings'}[scope]
    row = None
    with tempfile.TemporaryDirectory(prefix='arck-upload-') as tmp:
        path = Path(tmp) / f'{rid.hex}{ext}'
        if is_video:
            total = 0
            with path.open('wb') as handle:
                while True:
                    chunk = file.file.read(min(1024 * 1024, MAX_VIDEO_BYTES - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_VIDEO_BYTES:
                        raise HTTPException(413, 'Maximum recording size is 250 MB.')
                    handle.write(chunk)
            with path.open('rb') as handle:
                header = handle.read(64)
            # MP4 container signature; actual decoding is left to the player.
            if total < 16 or header[4:8] != b'ftyp':
                raise HTTPException(400, 'File does not have a supported MP4 container header.')
            file_type = 'video/mp4'
        else:
            if data is None:
                data = file.file.read(MAX_FILE_SIZE_BYTES + 1)
            total = len(data)
            if total > MAX_FILE_SIZE_BYTES:
                raise HTTPException(413, 'Maximum file size is 10 MB.')
            if not data or not _content_matches_extension(ext, data):
                raise HTTPException(400, 'File is empty or does not match its extension.')
            path.write_bytes(data)
            file_type = ALLOWED_EXTENSIONS[ext]
        row = PortalFile(id=rid, scope=scope, target_id=target, uploader_id=user.id,
            drive_id=store.drive_id, folder=store.folder, remote_name=path.name,
            file_name=name, file_type=file_type, size_bytes=total, state='PENDING')
        db.add(row)
        db.commit()  # Durable ownership of this UUID path before remote upload.
        try:
            ensure_folder(store)
            remote = store.upload_file(path, row.remote_name)
            row.remote_item_id = remote.item_id
            row.state = 'READY'
            db.commit()
            db.refresh(row)
        except Exception as exc:
            db.rollback()
            row = db.get(PortalFile, rid)
            if row is not None:
                row.state = 'FAILED'
                db.commit()
            log.warning('Portal upload failed id=%s error_type=%s', rid, type(exc).__name__)
            raise HTTPException(503, 'File upload failed. Please try again. No file was published.')
    return row


def save_support_document(db, user, ticket_id, file, contents):
    row = store_document(db, user, 'support', ticket_id, file, data=contents)
    return f'portal:{row.id}', row.file_name, row.file_type, row.size_bytes


def support_document_response(db, attachment):
    from app.core.portal_storage import remote_response
    try:
        rid = uuid.UUID(attachment.storage_reference.removeprefix('portal:'))
    except ValueError:
        raise HTTPException(404, 'Attachment not found')
    row = db.get(PortalFile, rid)
    if row is None or row.scope != 'support' or row.target_id != attachment.ticket_id or row.state != 'READY':
        raise HTTPException(404, 'Attachment not found')
    return remote_response(document_store(row), row.remote_item_id, size=row.size_bytes,
                           media_type=row.file_type, filename=row.file_name)
