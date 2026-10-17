"""Real HTTP/ORM deletion regressions using temporary SQLite databases only.

FK enforcement is enabled by the shared provisioning fixture. No lifespan,
production workers, external mail, production database or student is used.
"""
import importlib.util
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select, text

from app.api import routes_admin_students as routes
from app.core import upload_cleanup, uploads
from app.models import (
    AdminRole, AdminRolePermission, AuthAuditLog, AuthEventType, AuthSession,
    Batch, BatchChatReadState, BatchEnrollment, BatchMessage, Conversation,
    DirectMessage, FileCleanupJob, GoogleLoginTicket, InterviewSchedule,
    LabAccessAuditLog, LabAccessOverride, LabSlotBooking, LabVm, LabVmAccess,
    LabVmAccessAuditLog, Meeting, MeetingMessage, MeetingNote, MeetingParticipant,
    MeetingRecording, ParticipantStatus, Permission, Program, ProgramEnrollment,
    RoleEnum, StudentProfile, StudyMaterial, SupportAttachment, SupportMessage,
    SupportTicket, User, UserGoogleIdentity,
)
from app.models.support import SenderTypeEnum, TicketCategoryEnum
from tests import test_student_creation as fixtures


class PermanentStudentDeletionTests(unittest.TestCase):
    def setUp(self):
        self.portal = fixtures.StudentCreationTests()
        self.portal.setUp()
        self.addCleanup(self.portal.tearDown)
        self.engine, self.client = self.portal.engine, self.portal.client
        self.uid, self.actor = self.portal.legacy_id, self.portal.admin_id
        self.headers = self.portal.headers
        self.student_headers = self.portal._headers(self.uid, "student")
        root = Path(self.portal.temp.name)
        self.support_root, self.photo_root = root / "support", root / "photos"
        for storage in (self.support_root, self.photo_root):
            storage.mkdir()
        self.owned_file = self.support_root / "student.pdf"
        self.other_file = self.support_root / "admin.pdf"
        self.photo = self.photo_root / "student.png"
        for file in (self.owned_file, self.other_file, self.photo):
            file.write_bytes(b"test-only")
        for name, value in (("UPLOAD_ROOT", self.support_root), ("PROFILE_PHOTO_ROOT", self.photo_root)):
            p = patch.object(uploads, name, value)
            p.start()
            self.addCleanup(p.stop)
        now = datetime.now(timezone.utc)
        with fixtures.ProvisioningTestSession(self.engine) as db:
            db.get(User, self.uid).photo_path = self.photo.name
            batch = Batch(name="Shared batch", trainer_id=self.portal.trainer_id, program_id=self.portal.institutional_id)
            ticket = SupportTicket(requester_id=self.uid, ticket_number="PERM-1", subject="Retained support",
                                   category=TicketCategoryEnum.other)
            conversation = Conversation(user_a_id=self.actor, user_b_id=self.uid)
            meeting = Meeting(title="Shared meeting", host_id=self.actor, scheduled_at=now)
            vm = LabVm(name="Shared lab inventory", hostname="test.invalid", student_rdp_username="student",
                       agent_token_hash="a" * 64, current_agent_password_encrypted="old-encrypted-value")
            db.add_all([batch, ticket, conversation, meeting, vm])
            db.flush()
            student_msg = SupportMessage(ticket_id=ticket.id, sender_id=self.uid,
                                         sender_type=SenderTypeEnum.student, message="Student contribution")
            admin_msg = SupportMessage(ticket_id=ticket.id, sender_id=self.actor,
                                       sender_type=SenderTypeEnum.admin, message="Other user contribution")
            db.add_all([student_msg, admin_msg])
            db.flush()
            db.add_all([
                SupportAttachment(ticket_id=ticket.id, message_id=student_msg.id, file_name="student.pdf",
                                  file_type="application/pdf", file_size=9, storage_reference=self.owned_file.name),
                SupportAttachment(ticket_id=ticket.id, message_id=admin_msg.id, file_name="admin.pdf",
                                  file_type="application/pdf", file_size=9, storage_reference=self.other_file.name),
                BatchEnrollment(batch_id=batch.id, student_id=self.uid),
                BatchEnrollment(batch_id=batch.id, student_id=self.portal.trainer_id),
                ProgramEnrollment(student_id=self.uid, program_id=self.portal.institutional_id),
                StudyMaterial(batch_id=batch.id, title="Shared resource", file_url="https://example.com/file.pdf"),
                BatchChatReadState(batch_id=batch.id, user_id=self.uid),
                BatchMessage(batch_id=batch.id, sender_id=self.uid, message="Keep student message"),
                BatchMessage(batch_id=batch.id, sender_id=self.portal.trainer_id, message="Keep trainer message"),
                DirectMessage(conversation_id=conversation.id, sender_id=self.uid, body="Keep student chat"),
                DirectMessage(conversation_id=conversation.id, sender_id=self.actor, body="Keep admin chat"),
                MeetingParticipant(meeting_id=meeting.id, user_id=self.uid, status=ParticipantStatus.JOINED),
                MeetingMessage(meeting_id=meeting.id, user_id=self.uid, message="Keep meeting chat"),
                MeetingNote(meeting_id=meeting.id, updated_by=self.uid, content="Shared notes"),
                MeetingRecording(meeting_id=meeting.id, file_path="shared-recording.mp4"),
                LabSlotBooking(student_id=self.uid, slot_date=date.today(), start_time=time(9), end_time=time(11)),
                LabAccessOverride(student_id=self.uid, updated_by=self.actor),
                LabVmAccess(student_id=self.uid, vm_id=vm.id, granted_by=self.actor,
                            granted_at=now, expires_at=now + timedelta(hours=1), rdp_password_encrypted="old"),
                LabAccessAuditLog(student_id=self.uid, admin_id=self.actor, action="UNLOCK",
                                  previous_status="LOCKED", new_status="UNLOCKED"),
                LabVmAccessAuditLog(student_id=self.uid, vm_id=vm.id, action="GRANTED", performed_by=self.actor),
                InterviewSchedule(student_id=self.uid, company_name="Example", interview_date=date.today()),
                UserGoogleIdentity(user_id=self.uid, google_sub="test-subject", linked_email="legacy@example.com"),
                GoogleLoginTicket(user_id=self.uid, token_hash="b" * 64, expires_at=now + timedelta(minutes=1)),
                AuthAuditLog(user_id=self.uid, event_type=AuthEventType.login_success, detail="Retained security event"),
            ])
            db.commit()
            self.batch, self.ticket, self.conversation, self.meeting, self.vm = batch.id, ticket.id, conversation.id, meeting.id, vm.id
            self.student_message, self.admin_message = student_msg.id, admin_msg.id
        self.url = f"/api/admin/students/{self.uid}/permanent-delete"
        self.body = {"confirm_username": "legacy.student", "confirmation": "PERMANENTLY DELETE"}

    def delete(self, headers=None, body=None, uid=None):
        url = self.url if uid is None else f"/api/admin/students/{uid}/permanent-delete"
        return self.client.post(url, headers=self.headers if headers is None else headers,
                                json=self.body if body is None else body)

    def assert_intact(self):
        with fixtures.ProvisioningTestSession(self.engine) as db:
            self.assertTrue(db.get(User, self.uid).is_active)
            self.assertEqual(db.get(SupportTicket, self.ticket).requester_id, self.uid)
            self.assertEqual(db.get(Conversation, self.conversation).user_b_id, self.uid)
            self.assertEqual(db.scalar(select(func.count()).select_from(SupportAttachment)), 2)
            self.assertEqual(db.scalar(select(func.count()).select_from(ProgramEnrollment)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(FileCleanupJob)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(AuthAuditLog).where(
                AuthAuditLog.event_type == AuthEventType.student_deleted)), 0)
            self.assertIsNone(db.scalar(select(AuthSession).where(AuthSession.user_id == self.uid)).revoked_at)
            self.assertEqual(db.get(LabVm, self.vm).current_agent_password_encrypted, "old-encrypted-value")
            self.assertEqual(db.execute(text("PRAGMA foreign_key_check")).all(), [])
        self.assertTrue(self.owned_file.exists())
        self.assertTrue(self.photo.exists())

    def test_super_admin_deletes_related_account_preserves_shared_history_and_audit(self):
        response = self.delete()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["action"], "permanently_deleted")
        self.assertNotIn("deactivated", response.json()["action"])
        with fixtures.ProvisioningTestSession(self.engine) as db:
            self.assertIsNone(db.get(User, self.uid))
            for model, column in [(StudentProfile, "user_id"), (AuthSession, "user_id"),
                                  (ProgramEnrollment, "student_id"), (LabVmAccess, "student_id"),
                                  (LabSlotBooking, "student_id"), (LabAccessOverride, "student_id"),
                                  (BatchChatReadState, "user_id"), (GoogleLoginTicket, "user_id"),
                                  (UserGoogleIdentity, "user_id"), (InterviewSchedule, "student_id")]:
                self.assertEqual(db.scalar(select(func.count()).select_from(model).where(getattr(model, column) == self.uid)), 0)
            self.assertIsNone(db.get(SupportTicket, self.ticket).requester_id)
            self.assertIsNone(db.get(SupportMessage, self.student_message).sender_id)
            self.assertEqual(db.get(SupportMessage, self.admin_message).sender_id, self.actor)
            self.assertEqual(db.scalar(select(func.count()).select_from(SupportMessage)), 2)
            self.assertEqual(db.scalar(select(func.count()).select_from(DirectMessage)), 2)
            self.assertIsNone(db.get(Conversation, self.conversation).user_b_id)
            self.assertEqual(db.get(Conversation, self.conversation).user_a_id, self.actor)
            self.assertEqual(db.scalar(select(func.count()).select_from(BatchMessage)), 2)
            self.assertEqual(db.scalar(select(func.count()).select_from(BatchEnrollment)), 1)
            self.assertIsNotNone(db.get(Batch, self.batch))
            self.assertIsNotNone(db.get(Program, self.portal.institutional_id))
            self.assertEqual(db.scalar(select(func.count()).select_from(StudyMaterial)), 1)
            self.assertIsNotNone(db.get(Meeting, self.meeting))
            participant = db.scalar(select(MeetingParticipant))
            self.assertIsNone(participant.user_id)
            self.assertEqual(participant.status, ParticipantStatus.REMOVED)
            self.assertIsNone(db.scalar(select(MeetingMessage)).user_id)
            self.assertIsNone(db.scalar(select(MeetingNote)).updated_by)
            self.assertEqual(db.scalar(select(MeetingRecording)).file_path, "shared-recording.mp4")
            self.assertIsNone(db.scalar(select(LabAccessAuditLog)).student_id)
            self.assertIsNone(db.scalar(select(LabVmAccessAuditLog)).student_id)
            self.assertNotEqual(db.get(LabVm, self.vm).current_agent_password_encrypted, "old-encrypted-value")
            audit = db.scalar(select(AuthAuditLog).where(AuthAuditLog.event_type == AuthEventType.student_deleted))
            self.assertEqual(audit.user_id, self.actor)
            self.assertEqual(audit.actor_role, "super_admin")
            self.assertEqual(audit.target, f"student:{self.uid}")
            self.assertIn("legacy.student", audit.detail)
            self.assertIsNotNone(audit.created_at)
            self.assertIsNone(db.scalar(select(AuthAuditLog).where(AuthAuditLog.detail == "Retained security event")).user_id)
            self.assertEqual(db.scalar(select(func.count()).select_from(SupportAttachment)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(FileCleanupJob)), 2)
            self.assertEqual(db.execute(text("PRAGMA foreign_key_check")).all(), [])
        self.assertTrue(self.owned_file.exists())  # No unlink inside the DB transaction.
        self.assertEqual(self.client.get("/api/auth/me", headers=self.student_headers).status_code, 401)

    def test_archived_history_is_readable_only_by_remaining_participant(self):
        self.assertEqual(self.delete().status_code, 200)
        url = f"/api/chat/conversations/{self.conversation}/messages"
        history = self.client.get(url, headers=self.headers)
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(len(history.json()), 2)
        self.assertEqual(history.json()[0]["sender_name"], "Deleted student")
        stranger = self.portal._headers(self.portal.trainer_id, "staff")
        self.assertEqual(self.client.get(url, headers=stranger).status_code, 404)
        support = self.client.get(f"/api/admin/support/tickets/{self.ticket}", headers=self.headers)
        self.assertEqual(support.status_code, 200, support.text)
        self.assertEqual(support.json()["requester_name"], "Deleted student")

    def test_regular_admin_with_delete_grant_cannot_call_permanent_endpoint_or_spoof_role(self):
        with fixtures.ProvisioningTestSession(self.engine) as db:
            role = AdminRole(name="Ordinary Admin", slug="ordinary_admin", is_system=False)
            db.add(role)
            db.flush()
            db.add(Permission(key="students.delete", module="Students", action="delete", description="Delete"))
            db.flush()
            db.add(AdminRolePermission(admin_role_id=role.id, permission_key="students.delete"))
            db.get(User, self.actor).admin_role_id = role.id
            db.commit()
        response = self.delete(body={**self.body, "is_super_admin": True, "admin_role": {"slug": "super_admin"}})
        self.assertEqual(response.status_code, 403, response.text)
        self.assert_intact()
        old = self.client.delete(f"/api/admin/students/{self.uid}", headers=self.headers)
        self.assertEqual(old.status_code, 200, old.text)
        self.assertEqual(old.json()["action"], "deactivated")

    def test_student_and_trainer_are_denied(self):
        for headers in (self.student_headers, self.portal._headers(self.portal.trainer_id, "staff")):
            self.assertEqual(self.delete(headers=headers).status_code, 403)
        self.assert_intact()

    def test_missing_authentication_denied(self):
        self.assertEqual(self.delete(headers={}).status_code, 401)
        self.assert_intact()

    def test_revoked_super_admin_session_denied(self):
        with fixtures.ProvisioningTestSession(self.engine) as db:
            session = db.scalar(select(AuthSession).where(AuthSession.user_id == self.actor))
            session.revoked_at = datetime.now(timezone.utc)
            db.commit()
        self.assertEqual(self.delete().status_code, 401)
        self.assert_intact()

    def test_password_reset_revokes_existing_student_sessions(self):
        response = self.client.patch(f"/api/admin/students/{self.uid}", headers=self.headers,
                                     json={"reset_temp_password": "New@Test12345"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get("/api/auth/me", headers=self.student_headers).status_code, 401)
        with fixtures.ProvisioningTestSession(self.engine) as db:
            self.assertTrue(db.get(User, self.uid).must_change_password)
            self.assertIsNotNone(db.scalar(select(AuthSession).where(AuthSession.user_id == self.uid)).revoked_at)

    def test_super_admin_can_revoke_sessions_without_changing_account(self):
        with fixtures.ProvisioningTestSession(self.engine) as db:
            password_hash = db.get(User, self.uid).password_hash
        response = self.client.post(f"/api/admin/students/{self.uid}/revoke-sessions", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        with fixtures.ProvisioningTestSession(self.engine) as db:
            user = db.get(User, self.uid)
            self.assertEqual(user.password_hash, password_hash)
            self.assertTrue(user.is_active)
        self.assertEqual(self.client.get("/api/auth/me", headers=self.student_headers).status_code, 401)

    def test_confirmation_exact_and_required(self):
        self.assertEqual(self.delete(body={**self.body, "confirm_username": "WRONG"}).status_code, 400)
        self.assertEqual(self.delete(body={"confirm_username": "legacy.student"}).status_code, 422)
        self.assertEqual(self.delete(body={**self.body, "confirmation": "DELETE"}).status_code, 422)
        self.assert_intact()

    def test_missing_and_nonstudent_targets_rejected(self):
        self.assertEqual(self.delete(uid=999999).status_code, 404)
        self.assertEqual(self.delete(uid=self.portal.trainer_id).status_code, 404)
        self.assert_intact()

    def test_audit_failure_rolls_back_every_staged_change(self):
        with patch.object(routes, "write_audit_event", side_effect=RuntimeError("test-only audit failure")):
            self.assertEqual(self.delete().status_code, 500)
        self.assert_intact()

    def test_commit_failure_rolls_back_every_staged_change(self):
        with patch.object(fixtures.ProvisioningTestSession, "commit", side_effect=RuntimeError("test-only commit failure")):
            self.assertEqual(self.delete().status_code, 500)
        self.assert_intact()

    def test_foreign_key_failure_rolls_back_without_fallback_deactivation(self):
        # An actual enforced FK fails at account deletion, after dependencies
        # have been staged. This is not a mocked delete/deactivate result.
        with self.engine.begin() as db:
            db.execute(text("CREATE TABLE external_dependency (user_id INTEGER REFERENCES users(id))"))
            db.execute(text("INSERT INTO external_dependency VALUES (:uid)"), {"uid": self.uid})
        self.assertEqual(self.delete().status_code, 409)
        self.assert_intact()

    def test_unapproved_shared_ownership_is_blocked_and_rolled_back(self):
        with fixtures.ProvisioningTestSession(self.engine) as db:
            db.get(Meeting, self.meeting).host_id = self.uid
            db.commit()
        result = self.delete()
        self.assertEqual(result.status_code, 409)
        self.assertIn("meetings.host_id", result.json()["detail"])
        self.assert_intact()

    def test_cleanup_retries_without_removing_shared_or_other_user_files(self):
        self.assertEqual(self.delete().status_code, 200)
        factory = lambda: fixtures.ProvisioningTestSession(self.engine)
        with patch.object(upload_cleanup, "SessionLocal", factory):
            with patch.object(Path, "unlink", side_effect=PermissionError("test-only storage failure")):
                upload_cleanup.process_file_cleanup()
            self.assertTrue(self.owned_file.exists())
            with fixtures.ProvisioningTestSession(self.engine) as db:
                self.assertEqual(db.scalar(select(func.count()).select_from(FileCleanupJob)), 2)
            upload_cleanup.process_file_cleanup()
            upload_cleanup.process_file_cleanup()  # Idempotent retry after success.
        self.assertFalse(self.owned_file.exists())
        self.assertFalse(self.photo.exists())
        self.assertTrue(self.other_file.exists())
        with fixtures.ProvisioningTestSession(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(FileCleanupJob)), 0)

    def test_duplicate_shared_file_reference_is_not_queued_or_unlinked(self):
        with fixtures.ProvisioningTestSession(self.engine) as db:
            db.add(SupportAttachment(ticket_id=self.ticket, message_id=self.admin_message, file_name="shared.pdf",
                                    file_type="application/pdf", file_size=9, storage_reference=self.owned_file.name))
            db.commit()
        self.assertEqual(self.delete().status_code, 200)
        with patch.object(upload_cleanup, "SessionLocal", lambda: fixtures.ProvisioningTestSession(self.engine)):
            upload_cleanup.process_file_cleanup()
        self.assertTrue(self.owned_file.exists())
        self.assertTrue(self.other_file.exists())

    def test_repeated_permanent_request_returns_404(self):
        self.assertEqual(self.delete().status_code, 200)
        self.assertEqual(self.delete().status_code, 404)
        with fixtures.ProvisioningTestSession(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(AuthAuditLog).where(
                AuthAuditLog.event_type == AuthEventType.student_deleted)), 1)

    def test_migration_retains_constraints_and_refuses_destructive_downgrade(self):
        path = Path(__file__).resolve().parents[1] / "alembic/versions/d7f9b3c5e2a1_student_permanent_deletion.py"
        spec = importlib.util.spec_from_file_location("deletion_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual(migration.down_revision, "c6e8a2b4d9f1")
        with patch.object(migration, "op") as op:
            migration.upgrade()
            self.assertEqual(op.alter_column.call_count, 8)
            for call in op.alter_column.call_args_list:
                self.assertTrue(call.kwargs["nullable"])
            op.drop_constraint.assert_not_called()
            op.drop_table.assert_not_called()
        with patch.object(migration, "op") as op:
            op.get_bind.return_value.scalar.return_value = 1
            with self.assertRaises(RuntimeError):
                migration.downgrade()
            op.drop_table.assert_not_called()
