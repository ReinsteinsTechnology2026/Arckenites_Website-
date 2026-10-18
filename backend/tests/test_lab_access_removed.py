"""Lab Access is absent from the Student and Admin portals; data and the agent stay.

Static source checks plus route-table checks. No database is needed or touched.
"""
import re
import unittest
from pathlib import Path

from app.main import app as portal_app
from app.database import Base
from app import models as _models  # noqa: F401  registers every table

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "arckenites-orange"
LAB_PATHS = ("/admin/lab-access", "/admin/lab-vm", "/admin/lab-vms", "/students/me/lab")


class LabAccessRemovedTests(unittest.TestCase):
    def test_no_admin_page_links_to_lab_pages(self):
        pages = sorted(SITE.glob("admin-*.html"))
        self.assertGreater(len(pages), 15)
        for page in pages:
            text = page.read_text(encoding="utf-8")
            self.assertNotIn("admin-lab-access", text, page.name)
            self.assertNotIn("admin-lab-vm", text.replace("admin-lab-vm.html), which", ""), page.name)
            self.assertNotRegex(text, r'class="label">(Lab Access|VM Lab)<', page.name)

    def test_lab_pages_and_scripts_are_gone(self):
        for name in ("admin-lab-access.html", "admin-lab-vm.html", "js/admin-lab-access.js", "js/admin-lab-vm.js"):
            self.assertFalse((SITE / name).exists(), name)
        auth = (SITE / "js" / "auth.js").read_text(encoding="utf-8")
        self.assertNotIn("lab_access.view", auth)
        self.assertNotIn("lab_vm.view", auth)

    def test_student_dashboard_has_no_lab_entry_points(self):
        html = (SITE / "student-dashboard.html").read_text(encoding="utf-8")
        script = (SITE / "js" / "student-dashboard.js").read_text(encoding="utf-8")
        self.assertNotIn('data-panel="lab"', html)
        self.assertNotIn("Lab Access", html)
        self.assertNotRegex(script, r"panel:\s*'lab'")
        self.assertNotIn("Lab Access", script)
        self.assertNotRegex(script, r"/students/me/lab")
        for token in ("labAccessStatusBanner", "labSlotsGrid", "labEnvironmentBody"):
            self.assertNotIn(token, html + script)
        # Unrelated panels are still present.
        for panel in ("overview", "settings", "schedule", "videos", "materials", "meetings", "interviews", "support"):
            self.assertIn(f'data-panel="{panel}"', html)

    def test_lab_http_routes_are_not_registered(self):
        paths = {route.path for route in portal_app.routes}
        for path in paths:
            self.assertFalse(any(path.startswith("/api" + prefix) for prefix in LAB_PATHS), path)

    def test_vm_agent_endpoints_and_unrelated_routes_remain(self):
        paths = {route.path for route in portal_app.routes}
        self.assertIn("/api/lab-vm-agent/heartbeat", paths)
        self.assertIn("/api/lab-vm-agent/events", paths)
        for path in ("/api/students/me/schedule", "/api/students/me/materials", "/api/students/me/tickets",
                     "/api/admin/programs", "/api/admin/students"):
            self.assertIn(path, paths)

    def test_lab_tables_are_retained_in_the_schema(self):
        tables = set(Base.metadata.tables)
        for table in ("lab_access_overrides", "lab_access_audit_log", "lab_slot_bookings",
                      "lab_vms", "lab_vm_access", "lab_vm_access_audit_log"):
            self.assertIn(table, tables)


if __name__ == "__main__":
    unittest.main()
