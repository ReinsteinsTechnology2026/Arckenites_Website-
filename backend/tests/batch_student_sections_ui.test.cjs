const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const site = path.resolve(__dirname, '../../arckenites-orange');
const read = (name) => fs.readFileSync(path.join(site, name), 'utf8');
const batch = read('admin-batch-detail.html');
const batchJs = read('js/admin-batch-detail.js');
const student = read('student-dashboard.html');
const studentJs = read('js/student-dashboard.js');

for (const id of ['jobAccessToggle', 'interviewAccessToggle', 'jobAccessMeta', 'interviewAccessMeta']) {
  assert.match(batch, new RegExp(`id="${id}"`));
}
assert.match(batchJs, /batches\.student_sections_manage/);
assert.match(batchJs, /\/admin\/batches\/\$\{batchId\}\/student-sections\/\$\{section\}/);
assert.match(student, /data-panel="interviews" style="display:none;"/);
assert.match(student, /data-panel="job-applications" style="display:none;"/);
assert.match(studentJs, /\/students\/me\/interviews\/availability/);
assert.match(studentJs, /\/student\/job-applications\/availability/);
assert.match(studentJs, /if \(name === 'interviews'\) loadInterviews\(\)/);
assert.match(studentJs, /No interviews are scheduled for you yet\./);
console.log('PASS: Batch controls and protected student navigation are wired to availability APIs.');
