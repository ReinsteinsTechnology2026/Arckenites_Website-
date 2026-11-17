"""Authenticated study documents and per-conversation shared files."""
import uuid
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.deps import get_current_user
from app.core.rate_limit import limiter
from app.core.portal_storage import document_store, remote_response
from app.core.uploads import ALLOWED_EXTENSIONS, MAX_FILE_SIZE_BYTES
from app.crud.permissions import user_has_permission
from app.database import get_db
from app.models.batch import Batch, BatchEnrollment
from app.models.chat import Conversation
from app.models.portal_file import PortalFile
from app.models.user import RoleEnum, User
from app.api.routes_chat import _can_chat, _get_or_create_conversation

router = APIRouter(prefix='/portal-files', tags=['portal-files'])


def batch_access(db, user, batch, write=False):
    if batch is None or not user.is_active:
        return False
    if user.role == RoleEnum.admin:
        return user_has_permission(db, user, 'batches.edit' if write else 'batches.view')
    if user.role == RoleEnum.staff:
        return batch.trainer_id == user.id
    return not write and user.role == RoleEnum.student and db.scalar(select(BatchEnrollment.id).where(
        BatchEnrollment.batch_id == batch.id, BatchEnrollment.student_id == user.id)) is not None


def conversation_access(db, user, convo):
    if not user.is_active or convo is None or user.id not in (convo.user_a_id, convo.user_b_id):
        return False
    other_id = convo.user_b_id if convo.user_a_id == user.id else convo.user_a_id
    other = db.get(User, other_id) if other_id else None
    return other is None or _can_chat(user, other)


def _scope(db, user, scope, target, write=False):
    if scope in ('materials', 'batch-chat', 'videos'):
        batch = db.scalar(select(Batch).where(Batch.id == target).with_for_update()) if write else db.get(Batch, target)
        if not batch_access(db, user, batch, write=write and scope in ('materials', 'videos')):
            raise HTTPException(404, 'Batch not found')
        return target
    if scope == 'conversation' and not write:
        convo = db.get(Conversation, target)
        if not conversation_access(db, user, convo):
            raise HTTPException(404, 'Conversation not found')
        return target
    if scope == 'chat':
        other = db.get(User, target)
        if other is None or target == user.id or not _can_chat(user, other) or (write and not other.is_active):
            raise HTTPException(404, 'Conversation not found')
        a, b = sorted((user.id, target))
        if write:
            # Match the message sender's account-lock order so deletion and
            # reciprocal sends/uploads cannot race each other.
            db.scalars(select(User).where(User.id.in_([a,b])).order_by(User.id).with_for_update()).all()
            return _get_or_create_conversation(db, user.id, target).id
        return db.scalar(select(Conversation.id).where(Conversation.user_a_id == a, Conversation.user_b_id == b))
    raise HTTPException(404, 'File collection not found')


def _allowed(db, user, row):
    if row.scope in ('materials', 'batch-chat', 'videos'):
        return batch_access(db, user, db.get(Batch, row.target_id))
    return row.scope == 'chat' and conversation_access(db, user, db.get(Conversation, row.target_id))


def _out(row, user):
    return {'id': str(row.id), 'file_name': row.file_name, 'file_type': row.file_type,
            'size_bytes': row.size_bytes, 'created_at': row.created_at,
            'can_delete': row.uploader_id == user.id}


@router.get('/capabilities')
def capabilities(user: User = Depends(get_current_user)):
    return {'uploads_enabled': document_store().readiness()[0], 'max_bytes': MAX_FILE_SIZE_BYTES,
            'extensions': sorted(ALLOWED_EXTENSIONS), 'video_max_bytes': 250 * 1024 * 1024}


@router.get('/scopes/{scope}/{target_id}')
def list_files(scope: str, target_id: int, offset: int = Query(0, ge=0), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    target = _scope(db, user, scope, target_id)
    rows = [] if target is None else db.scalars(select(PortalFile).where(
        PortalFile.scope == ('chat' if scope == 'conversation' else scope), PortalFile.target_id == target, PortalFile.state == 'READY'
    ).order_by(PortalFile.created_at.desc(), PortalFile.id).offset(offset).limit(101)).all()
    return {'files': [_out(row, user) for row in rows[:100]],
            'next_offset': offset + 100 if len(rows) > 100 else None,
            'can_upload': scope != 'conversation' and (scope not in ('materials', 'videos') or batch_access(db, user, db.get(Batch, target_id), write=True))}


@router.post('/scopes/{scope}/{target_id}', status_code=201)
@limiter.limit('10/minute')
def upload_file(scope: str, target_id: int, request: Request, file: UploadFile = File(...),
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    target = _scope(db, user, scope, target_id, write=True)
    from app.core.portal_documents import store_document
    row = store_document(db, user, scope, target, file)
    return _out(row, user)


@router.get('/{file_id}/download')
def download_file(file_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(PortalFile, file_id)
    if row is None or row.state != 'READY' or not _allowed(db, user, row):
        raise HTTPException(404, 'File not found')
    return remote_response(document_store(row), row.remote_item_id, size=row.size_bytes,
                           media_type=row.file_type, filename=row.file_name)


@router.delete('/{file_id}', status_code=204)
def delete_file(file_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(PortalFile, file_id)
    if row is None or row.state != 'READY' or not _allowed(db, user, row):
        raise HTTPException(404, 'File not found')
    admin_material = row.scope in ('materials', 'videos') and user.role == RoleEnum.admin and batch_access(
        db, user, db.get(Batch, row.target_id), write=True)
    if row.uploader_id != user.id and not admin_material:
        raise HTTPException(403, 'Only the uploader can remove this file.')
    row.state = 'DELETING'
    db.commit()  # The worker deletes remotely only AFTER this is durable.


@router.post('/{file_id}/playback-token')
def playback_token(file_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    import jwt
    from datetime import datetime, timedelta, timezone
    from app.config import settings
    row = db.get(PortalFile, file_id)
    if row is None or row.scope != 'videos' or row.state != 'READY' or not _allowed(db, user, row):
        raise HTTPException(404, 'Recording not found')
    now = datetime.now(timezone.utc)
    token = jwt.encode({'purpose':'portal-file-playback','fid':str(row.id),'sub':str(user.id),
                        'iat':now,'exp':now+timedelta(minutes=30)}, settings.jwt_secret, algorithm='HS256')
    return {'stream_url': f'/api/portal-files/{row.id}/stream?t={token}', 'expires_in':1800}


def _byte_range(value, size):
    import re
    if not value:
        return None
    match = re.fullmatch(r'bytes=(\d{0,20})-(\d{0,20})', value.strip())
    if match is None:
        return None
    first, last = match.groups()
    if not first:
        if not last or int(last) == 0:
            return 'invalid'
        return max(0,size-int(last)),size-1
    start=int(first);end=min(int(last),size-1) if last else size-1
    return 'invalid' if start >= size or start > end else (start,end)


@router.api_route('/{file_id}/stream', methods=['GET','HEAD'])
def stream_video(file_id: uuid.UUID, request: Request, t: str = Query(min_length=10,max_length=2000),
                 db: Session = Depends(get_db)):
    import jwt
    from fastapi.responses import Response
    from app.config import settings
    try:
        claim=jwt.decode(t,settings.jwt_secret,algorithms=['HS256'],options={'require':['exp','sub','fid']})
        if claim.get('purpose')!='portal-file-playback' or claim['fid']!=str(file_id):
            raise ValueError('Wrong playback scope')
        user=db.get(User,int(claim['sub']))
    except (jwt.PyJWTError,ValueError,KeyError):
        raise HTTPException(401,'Playback link expired or invalid')
    row=db.get(PortalFile,file_id)
    if user is None or row is None or row.scope!='videos' or row.state!='READY' or not _allowed(db,user,row):
        raise HTTPException(404,'Recording not found')
    window=_byte_range(request.headers.get('range'),row.size_bytes)
    if window=='invalid':
        return Response(status_code=416,headers={'Content-Range':f'bytes */{row.size_bytes}',
                                                'Cache-Control':'private, no-store'})
    return remote_response(document_store(row),row.remote_item_id,size=row.size_bytes,media_type='video/mp4',
                           byte_range=window,head=request.method=='HEAD')
