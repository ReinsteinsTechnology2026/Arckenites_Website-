from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import require_role
from app.database import get_db
from app.models.community_post import CommunityPost, CommunityPostStatus, CommunityPostVisibility
from app.models.user import User
from app.schemas.community_posts import MemberPostOut, PublicPostOut

router = APIRouter(prefix="/community", tags=["community-posts"])

# Upper bound on how many items one list returns. Pagination is not needed
# until there are more posts than this; it keeps responses bounded now.
LIST_LIMIT = 100


@router.get("/posts", response_model=list[PublicPostOut])
def list_public_posts(db: Session = Depends(get_db)):
    """Published PUBLIC posts only — no authentication. The visibility filter
    lives in the query itself, so a members-only post can never reach this
    response no matter how the rest of the code changes."""
    return db.scalars(
        select(CommunityPost)
        .where(
            CommunityPost.status == CommunityPostStatus.published,
            CommunityPost.visibility == CommunityPostVisibility.public,
        )
        .order_by(CommunityPost.published_at.desc(), CommunityPost.id.desc())
        .limit(LIST_LIMIT)
    ).all()


@router.get("/member/posts", response_model=list[MemberPostOut])
def list_member_posts(
    db: Session = Depends(get_db),
    _member: User = Depends(require_role("community")),
):
    """Published posts for an authenticated Community member: public AND
    members-only. require_role runs get_current_user, which also rejects
    inactive accounts, so a disabled member loses access immediately."""
    return db.scalars(
        select(CommunityPost)
        .where(CommunityPost.status == CommunityPostStatus.published)
        .order_by(CommunityPost.published_at.desc(), CommunityPost.id.desc())
        .limit(LIST_LIMIT)
    ).all()
