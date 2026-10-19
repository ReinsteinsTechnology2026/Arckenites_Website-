/* Run from repository root: node backend/tests/job_applications_ui.test.cjs */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const site = path.resolve(__dirname, '../../arckenites-orange');
const student = fs.readFileSync(path.join(site, 'student-dashboard.html'), 'utf8');
const admin = fs.readFileSync(path.join(site, 'admin-job-applications.html'), 'utf8');
const studentJs = fs.readFileSync(path.join(site, 'js/student-job-applications.js'), 'utf8');
const adminJs = fs.readFileSync(path.join(site, 'js/admin-job-applications.js'), 'utf8');
const authJs = fs.readFileSync(path.join(site, 'js/auth.js'), 'utf8');

assert.match(student, /id="simpleJobsBtn">Job Applications/);
assert.match(student, /data-panel="job-applications"/);
assert.match(student, /id="studentJobsList"/);
assert.match(student, /js\/student-job-applications\.js\?v=1/);
assert.match(studentJs, /No job applications are available at the moment/);
assert.match(studentJs, /rel="noopener noreferrer"/);
assert.match(studentJs, /data-reaction/);
assert.doesNotMatch(studentJs, /comment|reply/i);

assert.match(admin, /id="jobForm"/);
assert.match(admin, /id="adminJobsList"/);
assert.match(admin, /js\/admin-job-applications\.js\?v=1/);
assert.match(authJs, /'admin-job-applications\.html': 'placement\.view'/);
assert.match(adminJs, /placement\.create/);
assert.match(adminJs, /placement\.edit/);
for (const file of fs.readdirSync(site).filter((name) => /^admin-.*\.html$/.test(name) && name !== 'admin-login.html')) {
  const html = fs.readFileSync(path.join(site, file), 'utf8');
  assert.match(html, /href="admin-job-applications\.html"/, file);
  assert.doesNotMatch(html, /title="Job Opportunities/, file);
}
console.log('PASS: Student and Admin Job Applications navigation, controls, permission map, safe links, and no reply UI.');
