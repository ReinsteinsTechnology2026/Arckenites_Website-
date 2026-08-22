from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.core.rate_limit import limiter
from app.database import get_db
from app.models.contact_enquiry import ContactEnquiry
from app.schemas.contact_enquiry import CreateContactEnquiryRequest

router = APIRouter(prefix="/contact", tags=["contact"])


@router.post("/enquiries", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
def submit_enquiry(request: Request, payload: CreateContactEnquiryRequest, db: Session = Depends(get_db)):
    enquiry = ContactEnquiry(
        full_name=payload.full_name,
        phone=payload.phone,
        email=payload.email,
        subject=payload.subject,
        message=payload.message,
    )
    db.add(enquiry)
    db.commit()
    return {"detail": "Thanks for reaching out — our team will get back to you shortly."}
