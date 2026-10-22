"""REAL PostgreSQL check that deleting a user referenced by the batch access
audit columns nulls those columns instead of deleting or blocking the batch.

Covers the foreign keys added by c4e5f6a7b8c9:
batches.job_applications_access_updated_by_id and
batches.interview_schedule_access_updated_by_id, both ON DELETE SET NULL.
Skipped unless the marked disposable PostgreSQL is configured.
"""

import unittest
import uuid

from sqlalchemy import delete as sa_delete
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.database import engine
from app.models.batch import Batch, BatchStatusEnum, BatchTypeEnum
from app.models.user import RoleEnum, User
from postgres_safety import isolated_postgres_enabled


@unittest.skipUnless(isolated_postgres_enabled(), "Requires marked disposable PostgreSQL")
class BatchAuditForeignKeyTests(unittest.TestCase):
    def setUp(self):
        self.db = Session(bind=engine)
        suffix = uuid.uuid4().hex[:12]
        self.username = f"fkaudit.{suffix}"
        self.batch_name = f"fkaudit-batch-{suffix}"

    def tearDown(self):
        self.db.rollback()
        self.db.execute(sa_delete(Batch).where(Batch.name == self.batch_name))
        self.db.execute(sa_delete(User).where(User.username == self.username))
        self.db.commit()
        self.db.close()

    def test_deleting_audit_user_nulls_both_columns_and_keeps_batch(self):
        user_id = self.db.execute(insert(User).returning(User.id).values(
            username=self.username, password_hash="not-a-real-hash", full_name="FK Audit",
            role=RoleEnum.admin, is_active=True, must_change_password=False,
            failed_login_count=0,
        )).scalar_one()
        self.db.execute(insert(Batch).values(
            name=self.batch_name, batch_type=BatchTypeEnum.online,
            status=BatchStatusEnum.upcoming,
            job_applications_access_updated_by_id=user_id,
            interview_schedule_access_updated_by_id=user_id,
        ))
        self.db.commit()

        before = self.db.execute(select(
            Batch.job_applications_access_updated_by_id,
            Batch.interview_schedule_access_updated_by_id,
        ).where(Batch.name == self.batch_name)).one()
        self.assertEqual(tuple(before), (user_id, user_id))

        self.db.execute(sa_delete(User).where(User.id == user_id))
        self.db.commit()

        rows = self.db.execute(select(
            Batch.job_applications_access_updated_by_id,
            Batch.interview_schedule_access_updated_by_id,
        ).where(Batch.name == self.batch_name)).all()
        self.assertEqual(len(rows), 1, "batch row must survive deletion of the audit user")
        self.assertEqual(tuple(rows[0]), (None, None))


if __name__ == "__main__":
    unittest.main()
