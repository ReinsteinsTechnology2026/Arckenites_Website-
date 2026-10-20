/* Run from repository root: node backend/tests/portal_login_ui.test.cjs */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const site = path.resolve(__dirname, '../../arckenites-orange');
const html = fs.readFileSync(path.join(site, 'login.html'), 'utf8');
const script = fs.readFileSync(path.join(site, 'js/login.js'), 'utf8');
const auth = fs.readFileSync(path.join(site, 'js/auth.js'), 'utf8');
const styles = fs.readFileSync(path.join(site, 'css/style.css'), 'utf8');
const portalStyles = fs.readFileSync(path.join(site, 'css/portal.css'), 'utf8');
const campusImage = fs.statSync(path.join(site, 'assets/portal-campus.webp'));
const wordmarkImage = fs.statSync(path.join(site, 'assets/portal-wordmark.webp'));

assert.match(html, /id="portalChooser"/);
assert.match(html, /href="login\.html\?portal=student"[^>]*>[\s\S]*?Student Login/);
assert.match(html, /href="login\.html\?portal=staff"[^>]*>[\s\S]*?Staff Login/);
assert.match(html, /Staff &amp; Admin Portal/);
assert.match(html, /href="community-register\.html"/);
assert.match(html, /id="portalBack"/);
for (const id of ['loginForm', 'loginUsername', 'loginPassword', 'mfaLoginPanel', 'mfaLoginForm']) {
  assert.ok(html.includes(`id="${id}"`), id);
}
assert.match(styles, /\.portal-chooser\{ display:grid/);
assert.match(styles, /\.portal-chooser\{ grid-template-columns:1fr/);
assert.match(portalStyles, /\.portal-stage\{[^}]*grid-template-columns:/);
assert.match(portalStyles, /@media\(max-width:760px\)/);
assert.match(portalStyles, /@media\(prefers-reduced-motion:reduce\)/);
assert.match(portalStyles, /:focus-visible/);
assert.ok(campusImage.size > 0 && campusImage.size < 300_000, 'web-optimized campus image');
assert.ok(wordmarkImage.size > 0 && wordmarkImage.size < 100_000, 'web-optimized project wordmark');
assert.match(script, /portal === 'student' \|\| portal === 'staff'/);
assert.match(script, /body: \{ ticket, portal \}/);
assert.match(script, /ArckAuth\.login\(usernameInput\.value\.trim\(\), passwordInput\.value, portal\)/);
assert.match(auth, /admin: 'admin-dashboard\.html'/);
assert.match(auth, /staff: 'staff-dashboard\.html'/);
assert.match(auth, /student: 'student-dashboard\.html'/);

for (const [file, portal] of [['student-login.html', 'student'], ['staff-login.html', 'staff'], ['admin-login.html', 'staff']]) {
  const legacy = fs.readFileSync(path.join(site, file), 'utf8');
  assert.ok(legacy.includes(`login.html?portal=${portal}`), file);
}
