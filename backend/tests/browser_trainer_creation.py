"""Optional isolated Chromium smoke check of Admin Trainer creation and resend UI."""
from pathlib import Path
import re

from playwright.sync_api import sync_playwright


root = Path(__file__).resolve().parents[2]
html = (root / "arckenites-orange/admin-staff.html").read_text(encoding="utf-8")
html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.I | re.S)
html = re.sub(r"<link\b[^>]*>", "", html, flags=re.I | re.S)
script = (root / "arckenites-orange/js/admin-staff.js").read_text(encoding="utf-8")

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content(html, wait_until="domcontentloaded")
    page.evaluate("""() => {
      window.qa = {requests: []};
      window.ArckAuth = {
        requireRole: async () => ({full_name: 'Synthetic Admin', role: 'admin'}),
        hasPermission: () => true, logout() {}
      };
      window.ArckAPI = {request: async (path, options) => {
        qa.requests.push({path, options});
        if (path === '/admin/staff' && !options) return [];
        if (path === '/admin/staff' && options.method === 'POST') return {
          id: 7, username: options.body.email, email: options.body.email,
          full_name: options.body.full_name, is_active: true, must_change_password: true,
          created_at: new Date().toISOString(), permissions: {}, credential_email_sent: false,
          detail: 'Trainer created; credential email could not be sent.'
        };
        if (path === '/admin/staff/7/credentials-email') return {
          credential_email_sent: true, detail: 'Credentials were emailed to trainer@example.test.'
        };
        throw new Error('Unexpected API path: ' + path);
      }};
    }""")
    page.add_script_tag(content=script)
    page.evaluate("document.dispatchEvent(new Event('DOMContentLoaded'))")
    page.locator("#toggleAddStaffBtn").click()
    page.locator("#newStaffName").fill("Synthetic Trainer")
    page.locator("#newStaffEmail").fill("invalid-email")
    page.locator("#newStaffPassword").fill("Synthetic#123")
    page.locator("#addStaffSubmitBtn").click()
    page.get_by_text("Please enter a valid email address.").wait_for(state="visible")
    assert page.evaluate("qa.requests.length") == 1
    page.locator("#newStaffEmail").fill(" TRAINER@Example.TEST ")
    page.locator("#addStaffSubmitBtn").click()
    page.get_by_text("Credential email: Not sent.", exact=False).wait_for(state="visible")
    assert page.evaluate("qa.requests[1].options.body.email") == "trainer@example.test"
    assert page.evaluate("qa.requests[1].options.body.temp_password") == "Synthetic#123"
    assert "Synthetic#123" not in page.locator("#newStaffNotice").inner_text()
    page.locator("#staffTableBody .table-action-btn").click()
    page.locator('#rowActionMenu [data-action="resend"]').click()
    page.locator("#resendStaffPassword").fill("Synthetic#123")
    page.locator("#modalFooter [data-send]").click()
    page.get_by_text("Credentials were emailed to trainer@example.test.").wait_for(state="visible")
    assert page.evaluate("qa.requests[2].path") == "/admin/staff/7/credentials-email"
    browser.close()
print("Trainer browser smoke: required email, normalized login ID, delivery status, and resend action passed.")
