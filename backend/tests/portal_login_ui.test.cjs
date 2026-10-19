/* Run from repository root: node backend/tests/portal_login_ui.test.cjs */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const site = path.resolve(__dirname, '../../arckenites-orange');
const html = fs.readFileSync(path.join(site, 'login.html'), 'utf8');
const script = fs.readFileSync(path.join(site, 'js/login.js'), 'utf8');
const auth = fs.readFileSync(path.join(site, 'js/auth.js'), 'utf8');

assert.match(html, /id="portalChooser"/);
assert.match(html, /href="login\.html\?portal=student"[^>]*>[\s\S]*?Student Login/);
assert.match(html, /href="login\.html\?portal=staff"[^>]*>[\s\S]*?Staff Login/);
assert.match(html, /id="portalBack"/);
assert.match(html, /@media\(max-width:480px\)/);
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
