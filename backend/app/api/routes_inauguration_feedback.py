import logging

from fastapi import APIRouter, HTTPException, Request, status

from app.config import settings
from app.core.feedback_sheets import SheetsError, build_payload, post_to_sheet
from app.core.rate_limit import limiter
from app.schemas.inauguration_feedback import InaugurationFeedbackRequest

router = APIRouter(prefix="/feedback", tags=["feedback"])
logger = logging.getLogger("feedback_sheets")

RETRY_MESSAGE = "We could not save your feedback right now. Please try again in a moment."


@router.post("/inauguration", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def submit_inauguration_feedback(request: Request, payload: InaugurationFeedbackRequest):
    """Stateless: the answers go straight to the Google Sheet and nothing is kept here. Success
    is returned only after the sheet confirms the row; the browser may safely retry with the same
    submission_id, which the Apps Script de-duplicates."""
    if not settings.feedback_sheets_configured:
        logger.error("feedback sheet is not configured; submission rejected submission_id=%s", payload.submission_id)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, RETRY_MESSAGE)
    try:
        post_to_sheet(build_payload(payload))
    except SheetsError as exc:
        # Short code only: never the URL, the secret, or anything the visitor typed.
        logger.warning("feedback sheet write failed submission_id=%s error=%s", payload.submission_id, exc.code)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, RETRY_MESSAGE) from None
    except Exception as exc:
        logger.error("feedback sheet write crashed submission_id=%s error_type=%s", payload.submission_id, type(exc).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, RETRY_MESSAGE) from None
    return {"detail": "Thank you for your valuable feedback!"}
