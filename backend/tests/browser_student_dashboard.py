"""Optional browser smoke check: python backend/tests/browser_student_dashboard.py.

Uses synthetic users and a local HTML snapshot. It never contacts the API.
Requires a separately installed Playwright Chromium test browser.
"""
from pathlib import Path
import re

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
html = (ROOT / "arckenites-orange/student-dashboard.html").read_text(encoding="utf-8")
html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.I | re.S)
html = re.sub(r"<link\b[^>]*>", "", html, flags=re.I | re.S)
script = (ROOT / "arckenites-orange/js/student-dashboard.js").read_text(encoding="utf-8")

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for program_name, expected in (
        ("Freshers Job Hack Program", "flex"),
        ("Certificational Program", "flex"),
        (None, "none"),
    ):
        page = browser.new_page()
        page.set_content(html, wait_until="domcontentloaded")
        page.evaluate("""name => {
          window.ArckAuth = {
            requireRole: async () => ({full_name:'Test Student', role:'student', program:null, program_name:name}),
            logout() {}
          };
          window.ArckAPI = {avatarHtml:()=>'', request:async()=>[], getToken:()=>'', setSession(){}};
        }""", program_name)
        page.add_script_tag(content=script)
        page.evaluate("document.dispatchEvent(new Event('DOMContentLoaded'))")
        page.wait_for_timeout(300)
        actual = page.locator("#sidebarLayout").evaluate("element => element.style.display")
        assert actual == expected, f"{program_name}: sidebar was {actual!r}, expected {expected!r}"
        page.close()
    browser.close()
print("Assigned Freshers/Certificational students receive the portal dashboard; unassigned users get the welcome layout.")
