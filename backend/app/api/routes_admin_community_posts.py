from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.community_posts import delivery_summary, enqueue_deliveries_for_post
from app.core.deps import require_permission
from app.database import get_db
from app.models.community_post import (
    CommunityPost,
    CommunityPostEmailDelivery,
    CommunityPostStatus,
)
from app.models.user import User
from app.schemas.community_posts import AdminPostOut, CommunityPostCreate, CommunityPostUpdate, DeliveryOut

router = APIRouter(prefix="/admin/community/posts", tags=["admin-community-posts"])


def _out(post: CommunityPost, statuses: list) -> AdminPostOut:
    """Builds the admin view. Delivery statuses are passed in (one query for
    the whole list) rather than fetched per post."""
    is_published = post.status == CommunityPostStatus.published
    return AdminPostOut(
        id=post.id,
        kind=post.kind,
        title=post.title,
        body=post.body,
        visibility=post.visibility,
        status=post.status,
        published_at=post.published_at,
        created_at=post.created_at,
        updated_at=post.updated_at,
        email_status=delivery_summary(statuses) if is_published else None,
        queued_recipient_count=len(statuses),
    )


def _statuses_by_post(db: Session, post_ids: list[int]) -> dict[int, list]:
    grouped: dict[int, list] = defaultdict(list)
    if not post_ids:
        return grouped
    rows = db.execute(
        select(CommunityPostEmailDelivery.post_id, CommunityPostEmailDelivery.status)
        .where(CommunityPostEmailDelivery.post_id.in_(post_ids))
    ).all()
    for post_id, delivery_status in rows:
        grouped[post_id].append(delivery_status)
    return grouped


def _get_post_or_404(db: Session, post_id: int, lock: bool = False) -> CommunityPost:
    query = select(CommunityPost).where(CommunityPost.id == post_id)
    if lock:
        query = query.with_for_update()
    post = db.scalars(query).first()
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    return post


@router.get("", response_model=list[AdminPostOut])
def list_posts(
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("community.view")),
):
    posts = db.scalars(select(CommunityPost).order_by(CommunityPost.created_at.desc(), CommunityPost.id.desc())).all()
    grouped = _statuses_by_post(db, [p.id for p in posts])
    return [_out(p, grouped.get(p.id, [])) for p in posts]


@router.post("", response_model=AdminPostOut, status_code=status.HTTP_201_CREATED)
def create_post(
    payload: CommunityPostCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("community.publish")),
):
    """Creates a DRAFT. Drafts are never visible publicly and never email anyone."""
    post = CommunityPost(
        kind=payload.kind,
        title=payload.title,
        body=payload.body,
        visibility=payload.visibility,
        status=CommunityPostStatus.draft,
        created_by_id=actor.id,
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return _out(post, [])


@router.patch("/{post_id}", response_model=AdminPostOut)
def update_post(
    post_id: int,
    payload: CommunityPostUpdate,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("community.publish")),
):
    """Edits a draft or an already-published post. Editing NEVER sends email:
    the only email trigger is the first publish (see publish_post). A later,
    explicit 'send notification again' action is deliberately not built."""
    post = _get_post_or_404(db, post_id)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(post, field, value)
    db.add(post)
    db.commit()
    db.refresh(post)
    grouped = _statuses_by_post(db, [post.id])
    return _out(post, grouped.get(post.id, []))


@router.post("/{post_id}/publish", response_model=AdminPostOut)
def publish_post(
    post_id: int,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("community.publish")),
):
    """Publishes a draft and queues one email per eligible verified member.

    The row lock serializes concurrent publish requests, and the unique
    delivery constraint guarantees no recipient is queued twice, so a retry,
    a double-click, or a refreshed page can never resend. Publishing an
    already-published post changes nothing and queues nothing."""
    post = _get_post_or_404(db, post_id, lock=True)
    if post.status != CommunityPostStatus.published:
        post.status = CommunityPostStatus.published
        post.published_at = datetime.now(timezone.utc)
        db.add(post)
        # Same transaction as the status change: the post is never published
        # without its recipient queue, and the queue is never left behind.
        enqueue_deliveries_for_post(db, post)
    db.commit()
    db.refresh(post)
    grouped = _statuses_by_post(db, [post.id])
    return _out(post, grouped.get(post.id, []))


@router.get("/{post_id}/deliveries", response_model=list[DeliveryOut])
def list_deliveries(
    post_id: int,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("community.view")),
):
    _get_post_or_404(db, post_id)
    rows = db.scalars(
        select(CommunityPostEmailDelivery)
        .where(CommunityPostEmailDelivery.post_id == post_id)
        .order_by(CommunityPostEmailDelivery.id)
    ).all()
    return [
        DeliveryOut(user_id=r.user_id, status=r.status, attempts=r.attempts, sent_at=r.sent_at, error_code=r.error_code)
        for r in rows
    ]
