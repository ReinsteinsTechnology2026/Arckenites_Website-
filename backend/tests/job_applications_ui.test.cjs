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
const dashboardJs = fs.readFileSync(path.join(site, 'js/student-dashboard.js'), 'utf8');
const settings = fs.readFileSync(path.join(site, 'admin-settings.html'), 'utf8');
const settingsJs = fs.readFileSync(path.join(site, 'js/admin-settings.js'), 'utf8');

assert.match(student, /id="simpleJobsBtn" style="display:none;">Job Applications/);
assert.match(student, /data-panel="job-applications"/);
assert.match(student, /id="studentJobsList"/);
assert.match(student, /js\/student-job-applications\.js\?v=2/);
assert.match(student, /data-panel="job-applications" style="display:none;"/);
assert.match(dashboardJs, /\/student\/job-applications\/availability/);
assert.match(studentJs, /Job Applications is currently unavailable/);
assert.match(studentJs, /No job applications are available at the moment/);
assert.match(studentJs, /rel="noopener noreferrer"/);
assert.match(studentJs, /data-reaction/);
assert.doesNotMatch(studentJs, /comment|reply/i);

assert.match(admin, /id="jobForm"/);
assert.match(admin, /id="adminJobsList"/);
assert.match(admin, /js\/admin-job-applications\.js\?v=1/);
assert.match(authJs, /'admin-job-applications\.html': 'placement\.view'/);
assert.match(authJs, /'admin-settings\.html': \['settings\.view', 'job_applications\.section_manage'\]/);
assert.match(settings, /id="jobSectionEnabled"/);
assert.match(settingsJs, /\/admin\/settings\/job-applications-section/);
assert.match(settingsJs, /if \(!canViewSystemSettings\)/);
assert.match(adminJs, /placement\.create/);
assert.match(adminJs, /placement\.edit/);
for (const file of fs.readdirSync(site).filter((name) => /^admin-.*\.html$/.test(name) && name !== 'admin-login.html')) {
  const html = fs.readFileSync(path.join(site, file), 'utf8');
  assert.match(html, /href="admin-job-applications\.html"/, file);
  assert.doesNotMatch(html, /title="Job Opportunities/, file);
}
console.log('PASS: Student and Admin Job Applications navigation, section toggle, permission map, safe links, and no reply UI.');
