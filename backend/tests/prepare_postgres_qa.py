"""Mark a deliberately created local QA database before integration tests.

Run only after creating an isolated PostgreSQL cluster/database and applying
Alembic migrations. This script refuses all nonlocal/non-QA targets and needs
ARCK_QA_PREPARE=1 in addition to the integration-test opt-in variables.
"""

import os

from sqlalchemy import create_engine, text

from postgres_safety import qa_target_from_environment


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


if __name__ == "__main__":
    main()
