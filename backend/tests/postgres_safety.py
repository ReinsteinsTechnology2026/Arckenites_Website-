"""Fail-closed target verification for PostgreSQL tests that commit data."""

import os

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError


def qa_target_from_environment() -> tuple[URL, str] | None:
    """Check intent and URL before making any database connection."""
    if os.getenv("ARCK_ENABLE_POSTGRES_INTEGRATION") != "1":
        return None
    if os.getenv("ENV", "").strip().lower() != "test":
        return None
    marker = os.getenv("ARCK_QA_DATABASE_MARKER", "")
    if len(marker) < 16 or len(marker) > 128:
        return None
    try:
        url = make_url(os.environ["DATABASE_URL"])
    except (KeyError, ValueError, ArgumentError):
        return None
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in {"localhost", "127.0.0.1", "::1"}
        or not (url.database or "").startswith("arckenites_qa_")
        or url.port is None
        or url.query
    ):
        return None
    return url, marker


def isolated_postgres_enabled() -> bool:
    target = qa_target_from_environment()
    if target is None:
        return False
    url, marker = target
    engine = None
    try:
        engine = create_engine(url, connect_args={"connect_timeout": 2})
        with engine.connect() as connection:
            database, port = connection.execute(text("SELECT current_database(), inet_server_port()")).one()
            if database != url.database or port != url.port:
                return False
            markers = connection.execute(text("SELECT marker FROM arckenites_qa_marker")).scalars().all()
            return markers == [marker]
    except (SQLAlchemyError, OSError, ImportError, ValueError):
        return False
    finally:
        if engine is not None:
            engine.dispose()
