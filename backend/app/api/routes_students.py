from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import require_role
from app.database import get_db
from app.models.student import CurrentRoleEnum, ProgramEnum
from app.models.user import User
from app.schemas.admin_students import CompleteProfileRequest
from app.schemas.auth import MeResponse

router = APIRouter(prefix="/students", tags=["students"])


@router.post("/me/profile", response_model=MeResponse)
def complete_profile(
    payload: CompleteProfileRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    user.full_name = payload.full_name

    profile = user.student_profile
    profile.phone = payload.mobile_number
    profile.email = payload.email
    profile.current_role = CurrentRoleEnum(payload.current_role)
    profile.program = ProgramEnum(payload.program)

    db.add(user)
    db.add(profile)
    db.commit()
    db.refresh(user)

    return MeResponse.model_validate(user)
