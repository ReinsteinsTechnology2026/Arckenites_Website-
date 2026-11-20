"""Operator report/cleanup for LOCAL recording copies. Safe by default.

    python -m maintenance.recording_retention            # report only (dry run)
    python -m maintenance.recording_retention --apply    # may remove local copies

--apply still does nothing unless RECORDING_RETENTION_ENABLED=true, and removes
nothing unless RECORDING_RETENTION_DRY_RUN=false as well. Every file is
re-verified against OneDrive immediately before removal (see
app/core/recording_retention.py). Never deletes remote files or database rows.
Run only after the policy has been explicitly approved.
"""
import argparse
import json

from app.config import settings
from app.core import recording_retention
from app.database import SessionLocal


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="allow removal (still gated by the two settings)")
    args = parser.parse_args(argv)
    dry = True if not args.apply else settings.recording_retention_dry_run
    enabled = settings.recording_retention_enabled
    print(f"retention enabled={enabled} dry_run={dry} keep_days={recording_retention.effective_days()}")
    with SessionLocal() as db:
        report = recording_retention.run_retention(db, enabled=True if not args.apply else enabled, dry_run=dry)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
