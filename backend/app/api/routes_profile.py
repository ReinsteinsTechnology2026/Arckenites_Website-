from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_flexible, require_role
from app.core.uploads import delete_profile_photo, get_profile_photo_path, save_profile_photo
from app.crud.profile import to_public_profile
from app.database import get_db
from app.models.user import User
from app.schemas.profile import PublicProfileOut

router = APIRouter(prefix="/users", tags=["profile"])

# Photo upload/removal is a Student/Trainer self-service feature per the
# visibility spec — Admin identity isn't part of this feature.
PHOTO_ROLES = ("student", "staff")


@router.post("/me/photo", response_model=PublicProfileOut)
async def upload_my_photo(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*PHOTO_ROLES)),
):
    contents = await file.read()
    storage_reference = save_profile_photo(file, contents)
    old_reference = user.photo_path

    user.photo_path = storage_reference
    db.add(user)
    db.commit()
    db.refresh(user)

    if old_reference:
        delete_profile_photo(old_reference)

    return to_public_profile(user)


@router.delete("/me/photo", status_code=status.HTTP_204_NO_CONTENT)
def remove_my_photo(
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*PHOTO_ROLES)),
):
    if user.photo_path:
        old_reference = user.photo_path
        user.photo_path = None
        db.add(user)
        db.commit()
        delete_profile_photo(old_reference)
    return None


@router.get("/{user_id}/photo")
def get_user_photo(
    user_id: int,
    db: Session = Depends(get_db),
    _viewer: User = Depends(get_current_user_flexible),
):
    """Serves any user's profile photo to any authenticated caller — the
    photo is the public-identity piece, so no ownership/role check beyond
    "is logged in" applies here. Returns 404 (no other detail) whether the
    user doesn't exist or simply hasn't uploaded a photo."""
    target = db.get(User, user_id)
    if target is None or not target.photo_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No profile photo")

    path = get_profile_photo_path(target.photo_path)
    return FileResponse(path)
