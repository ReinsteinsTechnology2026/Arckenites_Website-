from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import require_role
from app.database import get_db
from app.models.contact_enquiry import ContactEnquiry
from app.models.user import User
from app.schemas.contact_enquiry import ContactEnquiryOut, UpdateContactEnquiryRequest

router = APIRouter(prefix="/admin/contact-enquiries", tags=["admin-contact-enquiries"])


def _out(enquiry: ContactEnquiry) -> ContactEnquiryOut:
    return ContactEnquiryOut(
        id=enquiry.id, full_name=enquiry.full_name, phone=enquiry.phone, email=enquiry.email,
        subject=enquiry.subject, message=enquiry.message, is_read=enquiry.is_read, created_at=enquiry.created_at,
    )


@router.get("", response_model=list[ContactEnquiryOut])
def list_enquiries(db: Session = Depends(get_db), _admin: User = Depends(require_role("admin"))):
    rows = db.scalars(select(ContactEnquiry).order_by(ContactEnquiry.created_at.desc())).all()
    return [_out(e) for e in rows]


def _get_enquiry_or_404(db: Session, enquiry_id: int) -> ContactEnquiry:
    enquiry = db.get(ContactEnquiry, enquiry_id)
    if enquiry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Enquiry not found")
    return enquiry


@router.patch("/{enquiry_id}", response_model=ContactEnquiryOut)
def update_enquiry(
    enquiry_id: int,
    payload: UpdateContactEnquiryRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    enquiry = _get_enquiry_or_404(db, enquiry_id)
    enquiry.is_read = payload.is_read
    db.add(enquiry)
    db.commit()
    db.refresh(enquiry)
    return _out(enquiry)


@router.delete("/{enquiry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_enquiry(
    enquiry_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    enquiry = _get_enquiry_or_404(db, enquiry_id)
    db.delete(enquiry)
    db.commit()
    return None
