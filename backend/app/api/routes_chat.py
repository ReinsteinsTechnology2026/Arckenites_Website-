from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_ws_user
from app.core.ws_manager import manager
from app.database import SessionLocal, get_db
from app.models.chat import Conversation, DirectMessage
from app.models.user import RoleEnum, User
from app.schemas.chat import ConversationOut, MessageOut, SendMessageBody, UserSearchResult

router = APIRouter(prefix="/chat", tags=["chat"])


def _get_or_create_conversation(db: Session, id1: int, id2: int) -> Conversation:
    a, b = (id1, id2) if id1 < id2 else (id2, id1)
    convo = db.scalar(
        select(Conversation).where(Conversation.user_a_id == a, Conversation.user_b_id == b)
    )
    if convo is None:
        convo = Conversation(user_a_id=a, user_b_id=b)
        db.add(convo)
        db.commit()
        db.refresh(convo)
    return convo


def _last_read_at(convo: Conversation, user_id: int) -> datetime | None:
    return convo.user_a_last_read_at if user_id == convo.user_a_id else convo.user_b_last_read_at


def _mark_read(convo: Conversation, user_id: int) -> None:
    now = datetime.now(timezone.utc)
    if user_id == convo.user_a_id:
        convo.user_a_last_read_at = now
    else:
        convo.user_b_last_read_at = now


def _other_user_id(convo: Conversation, user_id: int) -> int:
    return convo.user_b_id if user_id == convo.user_a_id else convo.user_a_id


def _message_out(msg: DirectMessage, sender_name: str, requester_id: int) -> MessageOut:
    return MessageOut(
        id=msg.id, sender_id=msg.sender_id, sender_name=sender_name,
        is_me=msg.sender_id == requester_id, body=msg.body, created_at=msg.created_at,
    )


@router.get("/users/search", response_model=list[UserSearchResult])
def search_users(
    q: str = Query(min_length=1, max_length=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    like = f"%{q}%"
    rows = db.scalars(
        select(User)
        .where(
            User.id != user.id,
            User.is_active.is_(True),
            or_(User.username.ilike(like), User.full_name.ilike(like)),
        )
        .order_by(User.full_name)
        .limit(20)
    ).all()
    return [UserSearchResult(id=u.id, username=u.username, full_name=u.full_name, role=u.role.value) for u in rows]


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    convos = db.scalars(
        select(Conversation)
        .where(or_(Conversation.user_a_id == user.id, Conversation.user_b_id == user.id))
        .order_by(Conversation.updated_at.desc())
    ).all()

    out: list[ConversationOut] = []
    seen_other_ids: set[int] = set()

    for convo in convos:
        other_id = _other_user_id(convo, user.id)
        other = db.get(User, other_id)
        if other is None:
            continue
        seen_other_ids.add(other_id)

        last_msg = db.scalar(
            select(DirectMessage)
            .where(DirectMessage.conversation_id == convo.id)
            .order_by(DirectMessage.created_at.desc())
            .limit(1)
        )

        last_read = _last_read_at(convo, user.id)
        unread_filters = [DirectMessage.conversation_id == convo.id, DirectMessage.sender_id != user.id]
        if last_read is not None:
            unread_filters.append(DirectMessage.created_at > last_read)
        unread_count = db.scalar(select(func.count()).select_from(DirectMessage).where(*unread_filters)) or 0

        out.append(ConversationOut(
            other_user_id=other.id, other_username=other.username, other_name=other.full_name,
            other_role=other.role.value,
            last_message=last_msg.body if last_msg else None,
            last_message_at=last_msg.created_at if last_msg else None,
            unread_count=unread_count,
            is_pinned_admin=False,
        ))

    # Every admin is available by default to non-admin users, even before any
    # message has been sent — search is for finding peers, not for reaching admin.
    if user.role != RoleEnum.admin:
        admins = db.scalars(
            select(User).where(User.role == RoleEnum.admin, User.is_active.is_(True), User.id != user.id)
        ).all()
        for admin in admins:
            if admin.id in seen_other_ids:
                continue
            out.insert(0, ConversationOut(
                other_user_id=admin.id, other_username=admin.username, other_name=admin.full_name,
                other_role=admin.role.value, last_message=None, last_message_at=None,
                unread_count=0, is_pinned_admin=True,
            ))

    return out


@router.get("/messages/{other_user_id}", response_model=list[MessageOut])
def get_messages(
    other_user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    other = db.get(User, other_user_id)
    if other is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    convo = _get_or_create_conversation(db, user.id, other_user_id)

    rows = db.execute(
        select(DirectMessage, User.full_name)
        .join(User, DirectMessage.sender_id == User.id)
        .where(DirectMessage.conversation_id == convo.id)
        .order_by(DirectMessage.created_at)
    ).all()

    _mark_read(convo, user.id)
    db.add(convo)
    db.commit()

    return [_message_out(msg, name, user.id) for msg, name in rows]


async def _handle_send(db: Session, sender: User, recipient_id: int, body_text: str) -> dict | None:
    if recipient_id == sender.id:
        return None
    recipient = db.get(User, recipient_id)
    if recipient is None:
        return None

    convo = _get_or_create_conversation(db, sender.id, recipient_id)
    msg = DirectMessage(conversation_id=convo.id, sender_id=sender.id, body=body_text)
    db.add(msg)
    convo.updated_at = datetime.now(timezone.utc)
    db.add(convo)
    db.commit()
    db.refresh(msg)

    base = _message_out(msg, sender.full_name, sender.id).model_dump(mode="json")
    payload_for_sender = {"type": "message", "peer_id": recipient_id, "message": base}
    payload_for_recipient = {
        "type": "message", "peer_id": sender.id,
        "message": {**base, "is_me": False},
    }
    await manager.send_to_user(sender.id, payload_for_sender)
    await manager.send_to_user(recipient_id, payload_for_recipient)
    return payload_for_sender


@router.websocket("/ws")
async def chat_ws(websocket: WebSocket):
    db = SessionLocal()
    try:
        user = await get_ws_user(websocket, db, required_role=None)
        if user is None:
            return

        await websocket.accept()
        manager.connect(user.id, websocket)

        try:
            while True:
                data = await websocket.receive_json()
                try:
                    recipient_id = int(data.get("recipient_id"))
                    body = SendMessageBody(body=str(data.get("body", "")).strip())
                except (TypeError, ValueError, ValidationError):
                    continue

                await _handle_send(db, user, recipient_id, body.body)
        except WebSocketDisconnect:
            pass
        finally:
            manager.disconnect(user.id, websocket)
    finally:
        db.close()
