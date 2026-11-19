from fastapi import APIRouter, Depends

from app.config import settings
from app.core.deps import get_current_user
from app.models.user import User

router = APIRouter(prefix="/classroom", tags=["classroom"])


@router.get("/availability")
def classroom_availability(_user: User = Depends(get_current_user)):
    """Whether the LiveKit classroom is switched on, registered and configured. The answer is
    identical for every role and reveals nothing else about the media server. Anything other
    than `true` means the browser keeps using the existing Jitsi meeting room."""
    return {"classroom_enabled": bool(settings.classroom_available)}
