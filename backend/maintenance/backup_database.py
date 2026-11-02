"""Create and verify a private PostgreSQL archive before production migrations.

Run from backend with its configured environment. Credentials are passed to
libpq through the child process environment, never command arguments or logs.
"""

import argparse
import os
from pathlib import Path
import subprocess

from sqlalchemy.engine import make_url

from app.config import settings


def backup(destination: Path) -> None:
    url = make_url(settings.database_url)
    if not url.drivername.startswith("postgresql") or not url.database:
        raise RuntimeError("A PostgreSQL DATABASE_URL is required")
    if destination.exists():
        raise RuntimeError("Backup destination already exists")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "PGHOST": url.host or "localhost",
        "PGPORT": str(url.port or 5432),
        "PGUSER": url.username or "",
        "PGPASSWORD": url.password or "",
        "PGDATABASE": url.database,
    })
    if "sslmode" in url.query:
        env["PGSSLMODE"] = str(url.query["sslmode"])
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        result = subprocess.run(
            ["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", str(destination)],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if result.returncode or destination.stat().st_size == 0:
            raise RuntimeError("pg_dump failed or produced an empty archive")
        result = subprocess.run(
            ["pg_restore", "--list", str(destination)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if result.returncode:
            raise RuntimeError("pg_restore could not read the archive")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    print(f"Verified PostgreSQL backup: {destination} ({destination.stat().st_size} bytes)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    backup(parser.parse_args().destination)
