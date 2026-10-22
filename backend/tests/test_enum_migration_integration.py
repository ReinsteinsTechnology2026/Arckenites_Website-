"""Exercise real Alembic transaction boundaries in an isolated PostgreSQL schema."""

from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, pool, text

from tests.postgres_safety import isolated_postgres_enabled, qa_target_from_environment


@unittest.skipUnless(isolated_postgres_enabled(), "Requires marked disposable PostgreSQL")
class EnumMigrationTransactionTests(unittest.TestCase):
    def test_enum_value_commits_before_later_revision_uses_it(self):
        url, _ = qa_target_from_environment()
        schema = "enum_tx_" + uuid4().hex
        admin_engine = create_engine(url, poolclass=pool.NullPool)
        schema_engine = create_engine(
            url, poolclass=pool.NullPool,
            connect_args={"options": f"-csearch_path={schema}"},
        )
        try:
            with admin_engine.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            try:
                with tempfile.TemporaryDirectory(prefix="arckenites-enum-migration-") as directory:
                    scripts = Path(directory)
                    (scripts / "versions").mkdir()
                    # Execute the repository environment, not a test-only transaction setup.
                    shutil.copyfile(Path(__file__).resolve().parents[1] / "alembic/env.py",
                                    scripts / "env.py")
                    revisions = (
                        ("base_enum", None, """
    op.execute("CREATE TYPE test_role AS ENUM ('student')")
    op.execute("CREATE TABLE test_users (role test_role NOT NULL, username text NOT NULL)")
"""),
                        ("add_community", "base_enum", """
    op.execute("ALTER TYPE test_role ADD VALUE 'community'")
"""),
                        ("use_community", "add_community", """
    op.execute("CREATE UNIQUE INDEX test_scope ON test_users "
               "((CASE WHEN role = 'community' THEN 'community' ELSE 'portal' END), username)")
"""),
                    )
                    for revision, parent, body in revisions:
                        (scripts / "versions" / f"{revision}.py").write_text(
                            f"from alembic import op\nrevision = {revision!r}\n"
                            f"down_revision = {parent!r}\nbranch_labels = None\ndepends_on = None\n"
                            f"def upgrade():\n{body}\ndef downgrade():\n    pass\n",
                            encoding="utf-8",
                        )
                    config = Config()
                    config.set_main_option("script_location", str(scripts))
                    # Only redirect the connection to our schema; env.py controls commits.
                    with patch("sqlalchemy.engine_from_config", return_value=schema_engine):
                        command.upgrade(config, "head")
                    # A separate connection must see the committed final revision and index.
                    with schema_engine.begin() as connection:
                        self.assertEqual(connection.scalar(text("SELECT version_num FROM alembic_version")),
                                         "use_community")
                        self.assertIsNotNone(connection.scalar(text("SELECT to_regclass('test_scope')")))
                        connection.execute(text("INSERT INTO test_users VALUES ('community', 'same'), ('student', 'same')"))
            finally:
                with admin_engine.begin() as connection:
                    connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        finally:
            schema_engine.dispose()
            admin_engine.dispose()


if __name__ == "__main__":
    unittest.main()
