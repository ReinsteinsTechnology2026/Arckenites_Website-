"""Approved catalog policy against isolated real ORM/HTTP fixtures.

No production catalog is modified or inferred. Fixture slugs and IDs are
deliberately unrelated to approved display names.
"""
import unittest

from sqlalchemy import func, select

from app.core.approved_programs import APPROVED_PROGRAM_NAMES
from app.models import Batch, BatchEnrollment, EnrollmentStatusEnum, Program, ProgramEnrollment, ProgramEnum, ProgramStatusEnum, StudyMaterial, User
from tests import test_student_creation as fixtures


class ApprovedProgramTests(unittest.TestCase):
    def setUp(self):
        self.portal = fixtures.StudentCreationTests()
        self.portal.setUp()
        self.addCleanup(self.portal.tearDown)
        self.client = self.portal.client
        self.headers = self.portal.headers
        self.engine = self.portal.engine
        with fixtures.ProvisioningTestSession(self.engine) as db:
            legacy = Program(id=507, slug="corporate_training", name="Corporate Training Program", status=ProgramStatusEnum.active)
            other = Program(id=619, slug="official_certification", name="Official Certification Program", status=ProgramStatusEnum.active)
            db.add_all([legacy, other])
            db.flush()
            user = db.get(User, self.portal.legacy_id)
            user.student_profile.program_id = legacy.id
            user.student_profile.program = ProgramEnum.corporate_training
            enrollment = ProgramEnrollment(program_id=legacy.id, student_id=user.id, status=EnrollmentStatusEnum.active)
            batch = Batch(name="Legacy Batch", program_id=legacy.id)
            db.add_all([enrollment, batch])
            db.flush()
            db.add(BatchEnrollment(batch_id=batch.id, student_id=user.id))
            material = StudyMaterial(batch_id=batch.id, title="Existing resource", file_url="https://example.com/legacy.pdf")
            db.add(material)
            db.commit()
            self.legacy_id, self.other_id = legacy.id, other.id
            self.enrollment_id, self.batch_id, self.material_id = enrollment.id, batch.id, material.id
            self.legacy_username, self.legacy_password_hash = user.username, user.password_hash

    def options(self, status=False, headers=None):
        suffix = "?include_status=true" if status else ""
        return self.client.get("/api/admin/students/program-options" + suffix, headers=self.headers if headers is None else headers)

    def assert_history_unchanged(self):
        with fixtures.ProvisioningTestSession(self.engine) as db:
            user = db.get(User, self.portal.legacy_id)
            self.assertEqual(user.username, self.legacy_username)
            self.assertEqual(user.password_hash, self.legacy_password_hash)
            self.assertEqual(user.student_profile.program_id, self.legacy_id)
            self.assertEqual(user.student_profile.program, ProgramEnum.corporate_training)
            self.assertEqual(db.get(ProgramEnrollment, self.enrollment_id).status, EnrollmentStatusEnum.active)
            self.assertEqual(db.get(Batch, self.batch_id).program_id, self.legacy_id)
            self.assertEqual(db.get(StudyMaterial, self.material_id).file_url, "https://example.com/legacy.pdf")
            self.assertEqual(db.scalar(select(func.count()).select_from(BatchEnrollment)), 1)

    def test_option_api_only_returns_three_names_with_real_catalog_ids_and_slugs(self):
        response = self.options()
        self.assertEqual(response.status_code, 200)
        self.assertEqual([p["name"] for p in response.json()], list(APPROVED_PROGRAM_NAMES))
        expected = {self.portal.institutional_id, self.portal.program_id, self.portal.certificational_id}
        self.assertEqual({p["id"] for p in response.json()}, expected)
        freshers = next(p for p in response.json() if p["name"] == "Freshers Job Hack Program")
        self.assertEqual(freshers["slug"], "python_full_stack")

    def test_default_options_contract_stays_a_list_and_metadata_is_opt_in(self):
        self.assertIsInstance(self.options().json(), list)
        metadata = self.options(status=True).json()
        self.assertEqual([p["name"] for p in metadata["options"]], list(APPROVED_PROGRAM_NAMES))
        self.assertEqual(metadata["missing_programs"], [])
        self.assertEqual(metadata["ambiguous_programs"], [])
        self.assertIsNone(metadata["detail"])

    def test_ids_are_resolved_from_records_not_fixed_numbers(self):
        with fixtures.ProvisioningTestSession(self.engine) as db:
            for offset, key in enumerate((self.portal.program_id, self.portal.institutional_id, self.portal.certificational_id)):
                db.get(Program, key).id = 901 + offset
            db.commit()
        self.assertEqual({p["id"] for p in self.options().json()}, {901, 902, 903})
        response = self.portal._create(program_id=903)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["program_id"], 903)

    def test_names_ignore_outer_spaces_and_case_but_labels_are_canonical(self):
        with fixtures.ProvisioningTestSession(self.engine) as db:
            db.get(Program, self.portal.program_id).name = "  FRESHERS JOB HACK PROGRAM  "
            db.commit()
        self.assertEqual([p["name"] for p in self.options().json()], list(APPROVED_PROGRAM_NAMES))

        response = self.client.patch(
            f"/api/admin/programs/{self.portal.program_id}",
            json={"name": "Freshers Job Hack Program", "description": "Updated details"}, headers=self.headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        with fixtures.ProvisioningTestSession(self.engine) as db:
            self.assertEqual(db.get(Program, self.portal.program_id).name, "  FRESHERS JOB HACK PROGRAM  ")

    def test_missing_program_is_reported_without_creation_or_alias_guessing(self):
        with fixtures.ProvisioningTestSession(self.engine) as db:
            db.get(Program, self.portal.certificational_id).name = "Different Existing Program"
            db.commit()
            count = db.scalar(select(func.count()).select_from(Program))
        data = self.options(status=True).json()
        self.assertEqual(data["missing_programs"], ["Certificational Program"])
        self.assertIn("Certificational Program", data["detail"])
        self.assertNotIn(self.other_id, {p["id"] for p in data["options"]})
        with fixtures.ProvisioningTestSession(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Program)), count)

    def test_ambiguous_name_is_reported_and_neither_record_is_selectable(self):
        with fixtures.ProvisioningTestSession(self.engine) as db:
            db.add(Program(id=901, slug="duplicate_label", name="institutional program"))
            db.commit()
        data = self.options(status=True).json()
        self.assertEqual(data["ambiguous_programs"], ["Institutional Program"])
        self.assertNotIn(self.portal.institutional_id, {p["id"] for p in data["options"]})
        self.assertNotIn(901, {p["id"] for p in data["options"]})
        self.assertEqual(self.portal._create(program_id=901).status_code, 400)
        self.assertEqual(self.portal._create(program_id=self.portal.institutional_id).status_code, 400)

    def test_inactive_approved_program_is_reported_and_cannot_be_assigned(self):
        with fixtures.ProvisioningTestSession(self.engine) as db:
            db.get(Program, self.portal.program_id).status = ProgramStatusEnum.inactive
            db.commit()
        data = self.options(status=True).json()
        self.assertEqual(data["inactive_programs"], ["Freshers Job Hack Program"])
        self.assertNotIn(self.portal.program_id, {p["id"] for p in data["options"]})
        self.assertEqual(self.portal._create().status_code, 400)

    def test_admin_catalog_and_stats_exclude_other_programs(self):
        response = self.client.get("/api/admin/programs", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([p["name"] for p in response.json()], list(APPROVED_PROGRAM_NAMES))
        stats = self.client.get("/api/admin/programs/stats", headers=self.headers).json()
        self.assertEqual(stats["total_programs"], 3)
        self.assertEqual(stats["active_programs"], 3)
        self.assertEqual(stats["total_enrolled_students"], 0)  # Legacy enrollment is retained, not counted here.
        self.assert_history_unchanged()

    def test_student_creation_rejects_unapproved_id_without_sending_email(self):
        response = self.portal._create(program_id=self.legacy_id)
        self.assertEqual(response.status_code, 400)
        self.assertIn("approved program", response.json()["detail"])
        self.portal.send.assert_not_called()
        with fixtures.ProvisioningTestSession(self.engine) as db:
            self.assertIsNone(db.scalar(select(User).where(User.username == "arun@example.com")))

    def test_student_reassignment_rejects_unapproved_id_and_legacy_slug_bypass(self):
        path = f"/api/admin/students/{self.portal.legacy_id}"
        for body in ({"program_id": self.other_id}, {"program": "official_certification"}):
            response = self.client.patch(path, json=body, headers=self.headers)
            self.assertEqual(response.status_code, 400, response.text)
        self.assert_history_unchanged()

    def test_unrelated_student_edit_and_echoed_historical_id_preserve_assignment(self):
        path = f"/api/admin/students/{self.portal.legacy_id}"
        for body in ({"full_name": "Updated legacy name"}, {"program_id": self.legacy_id}, {"program": "corporate_training"}):
            response = self.client.patch(path, json=body, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
        self.assert_history_unchanged()

    def test_historical_program_details_remain_readable_and_read_only(self):
        response = self.client.get(f"/api/admin/programs/{self.legacy_id}", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["id"], self.legacy_id)
        self.assertFalse(data["is_approved"])
        self.assertFalse(data["allows_new_enrollments"])
        self.assertEqual(len(data["enrollments"]), 1)
        self.assertEqual(len(data["batches"]), 1)
        self.assert_history_unchanged()

    def test_approved_detail_is_available_for_new_enrollments(self):
        response = self.client.get(f"/api/admin/programs/{self.portal.program_id}", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_approved"])
        self.assertTrue(response.json()["allows_new_enrollments"])

    def test_program_creation_cannot_add_a_fourth_or_duplicate_program(self):
        with fixtures.ProvisioningTestSession(self.engine) as db:
            count = db.scalar(select(func.count()).select_from(Program))
        for name in ("Institutional Program", "New Unapproved Program"):
            response = self.client.post("/api/admin/programs", json={"name": name}, headers=self.headers)
            self.assertEqual(response.status_code, 403)
        with fixtures.ProvisioningTestSession(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Program)), count)

    def test_approved_program_cannot_be_renamed_to_other_identity(self):
        for name in ("New Program", "Institutional Program"):
            response = self.client.patch(f"/api/admin/programs/{self.portal.program_id}", json={"name": name}, headers=self.headers)
            self.assertEqual(response.status_code, 400)
        with fixtures.ProvisioningTestSession(self.engine) as db:
            self.assertEqual(db.get(Program, self.portal.program_id).name, "Freshers Job Hack Program")

    def test_legacy_program_cannot_be_renamed_to_an_approved_name(self):
        response = self.client.patch(f"/api/admin/programs/{self.legacy_id}", json={"name": "Freshers Job Hack Program"}, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assert_history_unchanged()

    def test_approved_program_metadata_remains_editable(self):
        response = self.client.patch(f"/api/admin/programs/{self.portal.program_id}", json={"description": "Updated details"}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["description"], "Updated details")

    def test_direct_enrollment_into_historical_program_is_rejected(self):
        response = self.client.post(f"/api/admin/programs/{self.other_id}/enrollments", json={"student_ids": [self.portal.legacy_id]}, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assert_history_unchanged()

    def test_historical_enrollment_status_and_removal_are_blocked(self):
        path = f"/api/admin/programs/{self.legacy_id}/enrollments/{self.enrollment_id}"
        self.assertEqual(self.client.patch(path, json={"status": "withdrawn"}, headers=self.headers).status_code, 400)
        self.assertEqual(self.client.delete(path, headers=self.headers).status_code, 400)
        self.assert_history_unchanged()

    def test_direct_enrollment_into_approved_program_still_works(self):
        response = self.client.post(f"/api/admin/programs/{self.portal.program_id}/enrollments", json={"student_ids": [self.portal.legacy_id]}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["enrollments"]), 1)
        self.assert_history_unchanged()  # Adding an enrollment does not change primary assignment.

    def test_new_batch_rejects_unapproved_program_but_accepts_approved_id(self):
        bad = self.client.post("/api/admin/batches", json={"name": "New Batch", "program_id": self.legacy_id}, headers=self.headers)
        self.assertEqual(bad.status_code, 400)
        good = self.client.post("/api/admin/batches", json={"name": "New Batch", "program_id": self.portal.program_id}, headers=self.headers)
        self.assertEqual(good.status_code, 201, good.text)
        self.assertEqual(good.json()["program"]["id"], self.portal.program_id)

    def test_unrelated_batch_edit_and_echoed_legacy_id_do_not_clear_mapping(self):
        path = f"/api/admin/batches/{self.batch_id}"
        for body in ({"name": "Renamed batch"}, {"program_id": self.legacy_id}):
            response = self.client.patch(path, json=body, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["program"]["id"], self.legacy_id)
        self.assert_history_unchanged()

    def test_batch_cannot_be_reassigned_to_another_unapproved_program(self):
        response = self.client.patch(f"/api/admin/batches/{self.batch_id}", json={"program_id": self.other_id}, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assert_history_unchanged()

    def test_new_student_cannot_be_allocated_into_historical_program_batch(self):
        created = self.portal._create().json()
        path = f"/api/admin/batches/{self.batch_id}/students"
        response = self.client.post(path, json={"student_ids": [created["id"]]}, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assert_history_unchanged()
        # A repeated request for an existing membership stays a harmless no-op.
        repeat = self.client.post(path, json={"student_ids": [self.portal.legacy_id]}, headers=self.headers)
        self.assertEqual(repeat.status_code, 200, repeat.text)

    def test_history_remains_in_student_list_and_batch_filters(self):
        students = self.client.get("/api/admin/students", headers=self.headers).json()
        legacy = next(s for s in students if s["id"] == self.portal.legacy_id)
        self.assertEqual(legacy["program_name"], "Corporate Training Program")
        batches = self.client.get(f"/api/admin/batches?program_id={self.legacy_id}", headers=self.headers)
        self.assertEqual(batches.status_code, 200)
        self.assertEqual([b["id"] for b in batches.json()], [self.batch_id])
        self.assert_history_unchanged()

    def test_existing_role_and_auth_gates_still_apply_to_options(self):
        self.assertEqual(self.options(headers={}).status_code, 401)
        staff = self.portal._headers(self.portal.trainer_id, "staff")
        self.assertEqual(self.options(headers=staff).status_code, 403)


if __name__ == "__main__":
    unittest.main()
