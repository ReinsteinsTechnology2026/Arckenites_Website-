from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.core.rate_limit import limiter
from app.database import get_db
from app.models.inauguration_feedback import InaugurationFeedback
from app.schemas.inauguration_feedback import InaugurationFeedbackRequest

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post("/inauguration", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def submit_inauguration_feedback(
    request: Request, payload: InaugurationFeedbackRequest, db: Session = Depends(get_db)
):
    db.add(InaugurationFeedback(**payload.model_dump()))
    db.commit()
    return {"detail": "Thank you for your valuable feedback!"}
