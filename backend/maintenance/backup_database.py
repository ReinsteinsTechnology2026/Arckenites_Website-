"""Create and verify a private PostgreSQL archive before production migrations.

Run from backend with its configured environment. Credentials are passed to
libpq through the child process environment, never command arguments or logs.
"""

import argparse
import os
from pathlib import Path
import subprocess
from urllib.parse import parse_qs, unquote, urlsplit


def _database_url() -> str:
    """Read DATABASE_URL without executing the production environment file."""
    value = os.environ.get("DATABASE_URL", "").strip()
    if value:
        return value
    env_file = Path(".env")
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == "DATABASE_URL":
                value = value.strip()
                if len(value) >= 2 and value[0] in ("'", '"') and value[-1] == value[0]:
                    value = value[1:-1]
                return value
    raise RuntimeError("DATABASE_URL is missing")


def backup(destination: Path) -> None:
    url = urlsplit(_database_url())
    database = unquote(url.path.lstrip("/"))
    if url.scheme not in ("postgresql", "postgresql+psycopg") or not database or not url.username:
        raise RuntimeError("A PostgreSQL DATABASE_URL is required")
    if destination.exists():
        raise RuntimeError("Backup destination already exists")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "PGHOST": url.hostname or "localhost",
        "PGPORT": str(url.port or 5432),
        "PGUSER": unquote(url.username),
        "PGPASSWORD": unquote(url.password or ""),
        "PGDATABASE": database,
    })
    query = parse_qs(url.query)
    if "sslmode" in query:
        env["PGSSLMODE"] = query["sslmode"][-1]
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
