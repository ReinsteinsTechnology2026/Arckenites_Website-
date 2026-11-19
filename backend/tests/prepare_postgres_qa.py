"""Mark a deliberately created local QA database before integration tests.

Run only after creating an isolated PostgreSQL cluster/database and applying
Alembic migrations. This script refuses all nonlocal/non-QA targets and needs
ARCK_QA_PREPARE=1 in addition to the integration-test opt-in variables.

Alembic creates tables only. The baseline RBAC data (permission catalog and
default role grants) comes from `seed.py --rbac-only`, the same production-safe
mechanism deploy.sh uses, so this script applies exactly that - and nothing
else (no bootstrap admin, no demo accounts) - to the marked database. Without
it, integration tests that exercise real admin permissions get 403s because the
plain Admin role has no grants: that is missing setup, not a permissions bug.
"""

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

from postgres_safety import qa_target_from_environment

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # backend/, for seed.py


def main() -> None:
    if os.getenv("ARCK_QA_PREPARE") != "1":
        raise SystemExit("Refusing QA marker creation without ARCK_QA_PREPARE=1")
    target = qa_target_from_environment()
    if target is None:
        raise SystemExit("Refusing QA marker creation for an unverified URL or environment")
    url, marker = target
    engine = create_engine(url, connect_args={"connect_timeout": 2})
    try:
        with engine.begin() as connection:
            database, port = connection.execute(text("SELECT current_database(), inet_server_port()")).one()
            if database != url.database or port != url.port:
                raise SystemExit("Connected database identity differs from the QA URL")
            connection.execute(text("CREATE TABLE IF NOT EXISTS arckenites_qa_marker (marker text PRIMARY KEY)"))
            existing = connection.execute(text("SELECT marker FROM arckenites_qa_marker")).scalars().all()
            if existing and existing != [marker]:
                raise SystemExit("QA database has a different marker; refusing to overwrite it")
            if not existing:
                connection.execute(text("INSERT INTO arckenites_qa_marker (marker) VALUES (:marker)"),
                                   {"marker": marker})
    finally:
        engine.dispose()
    print(f"QA marker prepared for {url.database} on localhost port {url.port}")

    # DATABASE_URL (already verified above as the marked QA target) is what the app's SessionLocal uses.
    import seed
    from app.database import SessionLocal
    with SessionLocal() as db:
        seed.seed_rbac(db)
        db.commit()
    print("RBAC baseline (permission catalog + default role grants) seeded")


if __name__ == "__main__":
    main()
