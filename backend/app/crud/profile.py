from app.models.user import User
from app.schemas.profile import PublicProfileOut


def to_public_profile(user: User) -> PublicProfileOut:
    """The one place a User row is narrowed down to what another student or
    trainer is allowed to see of it. Every route that shows one user's
    identity to another (batch members, chat, etc.) should build its
    response through this, never by hand-picking fields from `user`."""
    return PublicProfileOut(id=user.id, full_name=user.full_name, photo_url=user.photo_url)
