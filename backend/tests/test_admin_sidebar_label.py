"""The Admin sidebar's profile/settings accordion is labelled "Organizational Settings"."""
import re
import unittest
from pathlib import Path

SITE = Path(__file__).resolve().parents[2] / "arckenites-orange"
TRIGGER = re.compile(
    r'<button type="button" class="admin-profile-trigger" id="adminProfileTrigger">\s*'
    r'<span class="label">([^<]*)</span>\s*<i class="fa-solid fa-chevron-up"></i>\s*</button>')


class AdminSidebarLabelTests(unittest.TestCase):
    def pages(self):
        return [p for p in sorted(SITE.glob("admin-*.html")) if p.name != "admin-login.html"]

    def test_every_admin_page_uses_the_new_heading_with_the_same_structure(self):
        pages = self.pages()
        self.assertGreaterEqual(len(pages), 19)
        for page in pages:
            match = TRIGGER.search(page.read_text(encoding="utf-8"))
            self.assertIsNotNone(match, page.name)
            self.assertEqual(match.group(1), "Organizational Settings", page.name)

    def test_old_heading_is_gone_but_account_actions_remain(self):
        for page in self.pages():
            text = page.read_text(encoding="utf-8")
            self.assertNotIn('<span class="label">Account</span>', text, page.name)
            self.assertIn("Change Password", text, page.name)
            self.assertIn('id="adminProfileLogout"', text, page.name)
        # The student sidebar now uses Settings instead of an Account accordion.
        self.assertIn("Disable Account", (SITE / "admin-staff.html").read_text(encoding="utf-8"))
        student = (SITE / "student-dashboard.html").read_text(encoding="utf-8")
        self.assertNotIn('id="studentProfileTrigger"', student)
        self.assertNotIn('class="admin-sidebar-account"', student)
        self.assertIn('data-panel="settings"', student)
        for category in ("profile", "security", "contact"):
            self.assertIn(f'data-settings-category="{category}"', student)
        self.assertIn('data-settings-action="logout"', student)
        self.assertIn('<div class="community-nav-label">Account</div>', (SITE / "community-dashboard.html").read_text(encoding="utf-8"))

    def test_accordion_groups_and_menu_items_are_unchanged(self):
        html = (SITE / "admin-dashboard.html").read_text(encoding="utf-8")
        for group in ("Placement", "Website", "System"):
            self.assertIn(f"<span>{group}</span><i class=\"fa-solid fa-chevron-right\"></i>", html)
        for item in ("Admin Users", "Roles &amp; Permissions", "Activity Logs", "Settings"):
            self.assertIn(f'<span class="label">{item}</span>', html)


if __name__ == "__main__":
    unittest.main()
