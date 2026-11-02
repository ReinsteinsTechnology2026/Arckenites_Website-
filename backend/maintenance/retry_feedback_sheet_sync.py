"""Re-queue inauguration feedback rows whose Google Sheet sync gave up (FAILED), then run
a retry pass. Run from backend after fixing FEEDBACK_SHEETS_WEBAPP_URL / _SHARED_SECRET:

    python -m maintenance.retry_feedback_sheet_sync          # FAILED rows only
    python -m maintenance.retry_feedback_sheet_sync --all    # also re-send SYNCED rows (rebuild a lost/cleared sheet)

Safe to repeat: the Apps Script ignores a Submission ID that is already in the sheet.
"""
import argparse

from app.config import settings
from app.core.feedback_sheets import reset_failed, run_due_syncs
from app.database import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true", help="also re-queue rows already marked SYNCED")
    args = parser.parse_args()
    if not settings.feedback_sheets_configured:
        raise SystemExit("FEEDBACK_SHEETS_WEBAPP_URL and FEEDBACK_SHEETS_SHARED_SECRET are not set.")
    with SessionLocal() as db:
        requeued = reset_failed(db, include_synced=args.all)
        attempted = 0
        while True:  # a pass is capped at 20 rows / 90 s; keep going until nothing is left to send
            done = run_due_syncs(db, limit=20)
            attempted += done
            if not done:
                break
    print(f"requeued={requeued} attempted={attempted}")


if __name__ == "__main__":
    main()
