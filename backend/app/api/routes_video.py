from fastapi import APIRouter, Depends

from app.core.deps import get_current_user
from app.core.video import generate_adhoc_room_name
from app.models.user import User
from app.schemas.video import VideoRoomOut

router = APIRouter(prefix="/me/video", tags=["video"])


@router.post("/ad-hoc", response_model=VideoRoomOut, status_code=201)
def start_adhoc_meeting(user: User = Depends(get_current_user)):
    """Instant meeting, not tied to any scheduled class — any logged-in
    user (student/trainer/admin) can start one. No persistence: the
    generated room name is itself the invite, same trust model as sharing a
    Meet/Zoom link, and there's nothing here worth storing a history of."""
    return VideoRoomOut(
        room_name=generate_adhoc_room_name(),
        display_name=user.full_name,
        subject="Instant Meeting",
    )
