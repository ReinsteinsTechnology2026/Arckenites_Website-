// Runs integrations/google_sheets/FeedbackSheet.gs against in-memory mocks of the Apps Script
// services: authentication, header check, append, de-duplication, formula safety, locking.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const SECRET = 'unit-test-secret';
const ID = '123e4567-e89b-42d3-a456-426614174000';
const src = fs.readFileSync(path.join(__dirname, '..', 'integrations', 'google_sheets', 'FeedbackSheet.gs'), 'utf8');

function load({ secret = SECRET, previous = null, rows, lockFails = false, throwOnOpen = false } = {}) {
  const grid = rows ? rows.map(r => r.slice()) : [];
  const writes = [];
  const sheet = {
    getSheetId: () => 0,
    getLastRow: () => grid.length,
    getRange(r, c, nr, nc) {
      return {
        getValues: () => Array.from({ length: nr }, (_, i) => Array.from({ length: nc }, (_, j) => (grid[r - 1 + i] || [])[c - 1 + j] ?? '')),
        setValues(v) { v.forEach((row, i) => { grid[r - 1 + i] = row.slice(); }); writes.push({ r, c, v }); return this; },
        setNumberFormat(f) { writes.push({ format: f, r }); return this; },
        setFontWeight() { return this; },
      };
    },
    setFrozenRows() {},
  };
  const ctx = {
    PropertiesService: { getScriptProperties: () => ({ getProperty: k => (k === 'SHARED_SECRET' ? secret : k === 'SHARED_SECRET_PREVIOUS' ? previous : null) }) },
    LockService: { getScriptLock: () => ({ waitLock() { if (lockFails) throw new Error('lock'); }, releaseLock() { ctx.released = (ctx.released || 0) + 1; } }) },
    SpreadsheetApp: { openById(id) { ctx.openedId = id; if (throwOnOpen) throw new Error('boom'); return { getSheets: () => [sheet] }; }, flush() {} },
    ContentService: { MimeType: { JSON: 'json' }, createTextOutput: t => ({ text: t, setMimeType() { return this; } }) },
  };
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  const call = body => JSON.parse(ctx.doPost({ postData: { contents: typeof body === 'string' ? body : JSON.stringify(body) } }).text);
  return { ctx, grid, writes, call };
}

const HEADERS = ['Submission ID', 'Submitted At', 'Name', 'Domain', 'Other Domain', 'Overall Session Rating', 'Session Usefulness',
  'Learned Something New', 'Favourite Topic', 'Other Topic', 'Future Session Attendance', 'Suggestions',
  'Interested in Course Contact', 'Contact Number', 'Google Sheets Sync Status'];

const sample = (over = {}) => Object.assign({
  secret: SECRET, submission_id: ID, submitted_at: '2026-10-31 10:00:00 UTC', name: 'Asha', domain: 'Cloud', domain_other: '',
  overall_session: 'Excellent', usefulness: 'Very Useful', learned_something: 'Yes', liked_topic: 'AI', liked_topic_other: '',
  attend_future: 'Yes', suggestion: '', contact_preference: 'Yes', contact_number: '9876543210',
}, over);

// Empty sheet: headers are created in the documented order, then the row is appended in column order.
let t = load();
let res = t.call(sample());
assert.deepEqual(res, { ok: true, result: 'appended' });
assert.deepEqual(t.grid[0], HEADERS);
assert.deepEqual(t.grid[1], [ID, '2026-10-31 10:00:00 UTC', 'Asha', 'Cloud', '', 'Excellent', 'Very Useful', 'Yes', 'AI', '', 'Yes', '', 'Yes', '9876543210', 'Synced']);
assert.ok(t.writes.some(w => w.format === '@'), 'row is written as plain text');
assert.strictEqual(t.ctx.openedId, '1lNLPpvMJxFjC9KTrZyvG7GJlCTTjPJSC6LFlXDRN0fY');
assert.strictEqual(t.ctx.released, 1, 'lock released');

// Duplicate Submission ID is not appended again.
res = t.call(sample());
assert.deepEqual(res, { ok: true, result: 'duplicate' });
assert.strictEqual(t.grid.length, 2);
res = t.call(sample({ submission_id: '223e4567-e89b-42d3-a456-426614174000' }));
assert.strictEqual(res.result, 'appended');
assert.strictEqual(t.grid.length, 3);

// Authentication.
for (const bad of [undefined, '', 'wrong', SECRET + 'x', SECRET.slice(0, -1)]) {
  t = load();
  assert.deepEqual(t.call(sample({ secret: bad })), { ok: false, error: 'unauthorized' }, String(bad));
  assert.strictEqual(t.grid.length, 0, 'nothing written for a bad secret');
}
t = load({ secret: null });
assert.deepEqual(t.call(sample()), { ok: false, error: 'not_configured' });

// A caller cannot pick the spreadsheet or sheet.
t = load();
t.call(sample({ spreadsheet_id: 'EVIL', sheet_id: 5, spreadsheetId: 'EVIL' }));
assert.strictEqual(t.ctx.openedId, '1lNLPpvMJxFjC9KTrZyvG7GJlCTTjPJSC6LFlXDRN0fY');
assert.ok(!JSON.stringify(t.grid).includes('EVIL'));

// Malformed input.
t = load();
assert.deepEqual(t.call('not json'), { ok: false, error: 'bad_request' });
assert.deepEqual(t.call('null'), { ok: false, error: 'bad_request' });
assert.deepEqual(t.call(sample({ submission_id: 'abc' })), { ok: false, error: 'bad_submission_id' });
assert.deepEqual(t.call(sample({ submission_id: "x'; DROP" })), { ok: false, error: 'bad_submission_id' });
assert.strictEqual(t.grid.length, 0);

// Formula injection (also if a caller forgot to escape), tabs/CR, and no double-escaping.
t = load();
t.call(sample({ name: '=1+1', domain_other: '@SUM(A1)', suggestion: '-5', liked_topic_other: '+x', contact_number: '\t1' }));
const row = t.grid[1];
assert.strictEqual(row[2], "'=1+1");
assert.strictEqual(row[4], "'@SUM(A1)");
assert.strictEqual(row[11], "'-5");
assert.strictEqual(row[9], "'+x");
assert.strictEqual(row[13], "'\t1");
t = load();
t.call(sample({ name: "'=already escaped" }));
assert.strictEqual(t.grid[1][2], "'=already escaped");

// Very long text is capped.
t = load();
t.call(sample({ suggestion: 'a'.repeat(9000) }));
assert.strictEqual(t.grid[1][11].length, 5000);

// A sheet whose headers differ is never written to.
t = load({ rows: [['Wrong', 'Layout']] });
assert.deepEqual(t.call(sample()), { ok: false, error: 'header_mismatch' });
assert.strictEqual(t.grid.length, 1);

// Lock contention and unexpected errors give coded, data-free errors and release the lock.
t = load({ lockFails: true });
assert.deepEqual(t.call(sample()), { ok: false, error: 'busy' });
t = load({ throwOnOpen: true });
res = t.call(sample());
assert.deepEqual(res, { ok: false, error: 'server_error' });
assert.ok(!JSON.stringify(res).includes('Asha'));
assert.strictEqual(t.ctx.released, 1);

// Payload limits and types.
t = load();
assert.deepEqual(t.call(JSON.stringify(sample({ suggestion: 'a'.repeat(25000) }))), { ok: false, error: 'payload_too_large' });
assert.deepEqual(t.call(sample({ name: { nested: 'object' } })), { ok: false, error: 'bad_request' });
assert.deepEqual(t.call(sample({ suggestion: ['x'] })), { ok: false, error: 'bad_request' });
assert.deepEqual(t.call(sample({ secret: { a: 1 } })), { ok: false, error: 'unauthorized' });
assert.deepEqual(t.call(sample({ submission_id: [ID] })), { ok: false, error: 'bad_submission_id' });
assert.deepEqual(t.call('[1,2]'), { ok: false, error: 'bad_request' });
assert.deepEqual(JSON.parse(t.ctx.doPost({}).text), { ok: false, error: 'bad_request' });
assert.strictEqual(t.grid.length, 0, 'nothing written for rejected requests');
// Unknown extra fields are ignored, never written.
t.call(sample({ extra_col: 'LEAK', another: 'x' }));
assert.ok(!JSON.stringify(t.grid).includes('LEAK'));

// Secret rotation: old and new both work while SHARED_SECRET_PREVIOUS is set; the old one stops once removed.
t = load({ secret: 'new-secret', previous: 'old-secret' });
assert.equal(t.call(sample({ secret: 'new-secret' })).result, 'appended');
assert.equal(t.call(sample({ secret: 'old-secret', submission_id: '323e4567-e89b-42d3-a456-426614174000' })).result, 'appended');
assert.equal(t.call(sample({ secret: 'neither' })).error, 'unauthorized');
t = load({ secret: 'new-secret', previous: null });
assert.equal(t.call(sample({ secret: 'old-secret' })).error, 'unauthorized');
assert.equal(t.call(sample({ secret: '' })).error, 'unauthorized');

// The script never logs: there is no console/Logger call that could carry personal data.
assert.ok(!/(console|Logger)[.][A-Za-z_]/.test(src), 'FeedbackSheet.gs must not log');

// GET is not an API.
t = load();
assert.deepEqual(JSON.parse(t.ctx.doGet().text), { ok: false, error: 'method_not_allowed' });

console.log('Feedback Apps Script checks passed.');
