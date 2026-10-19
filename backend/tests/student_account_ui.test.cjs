/* Run from repository root: node backend/tests/student_account_ui.test.cjs */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../../arckenites-orange/js/student-account.js'), 'utf8');

async function render(fail = false) {
  const elements = new Map();
  const el = (id) => {
    if (!elements.has(id)) elements.set(id, {
      innerHTML: '', textContent: '', style: {}, disabled: false,
      addEventListener() {}, querySelector() { return {addEventListener() {}}; },
    });
    return elements.get(id);
  };
  const callbacks = {};
  const context = {
    document: {getElementById: el, addEventListener(name, fn) { callbacks[name] = fn; }},
    window: {addEventListener() {}},
    ArckAuth: {requireRole: async () => ({full_name: 'Student', email: null, phone: null, student_id: null})},
    ArckAPI: {
      avatarHtml: () => '',
      errorMessage: (_err, fallback) => fallback,
      request: async (url) => {
        if (fail) throw Error('network');
        if (url.endsWith('/enrollments')) return {programs: [], batches: []};
        if (url.endsWith('/sessions')) return [];
        if (url.includes('login-history')) return {items: [], has_more: false};
        throw Error(`Unexpected request: ${url}`);
      },
    },
  };
  vm.runInNewContext(source, context);
  await callbacks.DOMContentLoaded();
  return el;
}

(async () => {
  const empty = await render();
  assert.match(empty('studentIdCardBody').textContent, /not been assigned/);
  assert.match(empty('accountEnrollmentsBody').innerHTML, /No program enrollments recorded/);
  assert.match(empty('accountEnrollmentsBody').innerHTML, /No batch enrollments recorded/);
  assert.match(empty('accountSessionsBody').innerHTML, /No active sessions found/);
  assert.match(empty('accountLoginHistoryBody').innerHTML, /No login events recorded/);
  const failed = await render(true);
  assert.equal(failed('accountEnrollmentsBody').textContent, 'Could not load enrollments.');
  assert.equal(failed('accountSessionsBody').textContent, 'Could not load sessions.');
  assert.equal(failed('accountLoginHistoryBody').textContent, 'Could not load login history.');
  console.log('PASS: student account empty and API failure states.');
})().catch((error) => { console.error(error); process.exitCode = 1; });
