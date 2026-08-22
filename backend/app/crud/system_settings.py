from sqlalchemy.orm import Session

from app.models.system_settings import SETTINGS_ROW_ID, SystemSettings


def get_settings(db: Session) -> SystemSettings:
    """Fetch-or-create-default — defensive against the singleton row somehow
    being missing (e.g. a DB restored from a backup taken before this
    migration ran)."""
    row = db.get(SystemSettings, SETTINGS_ROW_ID)
    if row is None:
        row = SystemSettings(id=SETTINGS_ROW_ID)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def update_settings(db: Session, updated_by_id: int | None, **fields) -> SystemSettings:
    row = get_settings(db)
    for key, value in fields.items():
        if value is not None:
            setattr(row, key, value)
    row.updated_by_id = updated_by_id
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
