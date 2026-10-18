"""Optional isolated Chromium smoke check for the MFA login page; no API calls."""
from pathlib import Path
import re

from playwright.sync_api import sync_playwright


root = Path(__file__).resolve().parents[2]
html = (root / "arckenites-orange/login.html").read_text(encoding="utf-8")
html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.I | re.S)
html = re.sub(r"<link\b[^>]*>", "", html, flags=re.I | re.S)
script = (root / "arckenites-orange/js/login.js").read_text(encoding="utf-8")

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content(html, wait_until="domcontentloaded")
    page.evaluate("""() => {
      window.qa = {sessions: 0, redirects: 0, requests: []};
      window.ArckAuth = {
        login: async () => ({mfa_required: true, challenge: 'synthetic-test-challenge', enrollment_required: false}),
        redirectToRoleEntryPoint: () => { qa.redirects += 1; }
      };
      window.ArckAPI = {
        request: async (path, options) => {
          qa.requests.push(path);
          if (path === '/auth/mfa/challenge/verify') {
            if (options.body.code !== '123456') throw {detail: 'Invalid code'};
            return {access_token: 'synthetic-token', user: {role: 'student'}};
          }
          throw {detail: 'Unexpected API path'};
        },
        setSession: () => { qa.sessions += 1; }
      };
    }""")
    page.add_script_tag(content=script)
    page.evaluate("document.dispatchEvent(new Event('DOMContentLoaded'))")
    page.locator("#loginUsername").fill("synthetic@example.test")
    page.locator("#loginPassword").fill("synthetic-password")
    page.locator("#loginForm button[type=submit]").click()
    page.locator("#mfaLoginPanel").wait_for(state="visible")
    assert page.evaluate("qa.sessions") == 0
    assert page.evaluate("qa.redirects") == 0
    page.locator("#mfaLoginCode").fill("000000")
    page.locator("#mfaLoginForm button[type=submit]").click()
    page.get_by_text("Invalid code").wait_for(state="visible")
    assert page.evaluate("qa.sessions") == 0
    page.locator("#mfaLoginCode").fill("123456")
    page.locator("#mfaLoginForm button[type=submit]").click()
    page.wait_for_function("qa.sessions === 1 && qa.redirects === 1")
    assert page.evaluate("qa.requests") == ["/auth/mfa/challenge/verify", "/auth/mfa/challenge/verify"]
    browser.close()
print("MFA browser smoke: no session before a valid challenge; invalid code is rejected.")
