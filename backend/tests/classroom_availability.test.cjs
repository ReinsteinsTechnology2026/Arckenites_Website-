/* Run from repository root: node backend/tests/classroom_availability.test.cjs
 * "Join class" must keep using the existing Jitsi meeting room unless the server says the LiveKit classroom is on.
 * Fake API, fake storage, fake DOM: no network. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const A = require('../../arckenites-orange/js/classroom-availability.js');

const root = path.join(__dirname, '..', '..', 'arckenites-orange');
const read = (...p) => fs.readFileSync(path.join(root, ...p), 'utf8');
const strip = (src) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

const apiReturning = (value) => { const a = { calls: 0, async request(url) { a.calls += 1; a.url = url; if (value instanceof Error) throw value; return value; } }; return a; };
const memoryStorage = () => { const m = new Map(); return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), _m: m }; };
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

/* ---------------- availability decision ---------------- */
test('only an explicit true from the server turns the LiveKit classroom on', async () => {
  assert.equal(await A.isClassroomEnabled(apiReturning({ classroom_enabled: true }), { storage: null }), true);
  for (const bad of [{ classroom_enabled: false }, {}, null, undefined, 'true', { classroom_enabled: 'true' }, { classroom_enabled: 1 }, [], { enabled: true }]) {
    assert.equal(await A.isClassroomEnabled(apiReturning(bad), { storage: null }), false, JSON.stringify(bad));
  }
});

test('every failure keeps people on the existing meeting room', async () => {
  const codes = [0, 401, 403, 404, 429, 500, 502, 503];
  for (const status of codes) {
    const err = Object.assign(new Error('x'), { status, detail: 'nope' });
    assert.equal(await A.isClassroomEnabled(apiReturning(err), { storage: null }), false, `status ${status}`);
  }
  assert.equal(await A.isClassroomEnabled(apiReturning(new TypeError('network')), { storage: null }), false);
});

test('a hanging availability request times out and falls back instead of stalling the click', async () => {
  const hung = { request: () => new Promise(() => {}) };
  const t0 = Date.now();
  assert.equal(await A.isClassroomEnabled(hung, { storage: null, timeoutMs: 30 }), false);
  assert.ok(Date.now() - t0 < 1000);
});

test('it asks the documented endpoint and nothing else', async () => {
  const api = apiReturning({ classroom_enabled: true });
  await A.isClassroomEnabled(api, { storage: null });
  assert.equal(api.url, '/classroom/availability');
});

/* ---------------- caching ---------------- */
test('a positive answer is cached briefly and then re-checked', async () => {
  const storage = memoryStorage(); let now = 1000;
  const api = apiReturning({ classroom_enabled: true });
  assert.equal(await A.isClassroomEnabled(api, { storage, now: () => now }), true);
  now += 10000; assert.equal(await A.isClassroomEnabled(api, { storage, now: () => now }), true);
  assert.equal(api.calls, 1);
  now += A.CACHE_MS + 1; await A.isClassroomEnabled(api, { storage, now: () => now });
  assert.equal(api.calls, 2);
});

test('a failure is never cached, so a recovered server is used on the next click', async () => {
  const storage = memoryStorage();
  assert.equal(await A.isClassroomEnabled(apiReturning(new Error('down')), { storage }), false);
  assert.equal(storage._m.size, 0);
  assert.equal(await A.isClassroomEnabled(apiReturning({ classroom_enabled: true }), { storage }), true);
});

test('a corrupt or unavailable cache is ignored', async () => {
  const bad = { getItem: () => '{not json', setItem: () => { throw new Error('quota'); } };
  assert.equal(await A.isClassroomEnabled(apiReturning({ classroom_enabled: true }), { storage: bad }), true);
  const throwing = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } };
  assert.equal(await A.isClassroomEnabled(apiReturning({ classroom_enabled: false }), { storage: throwing }), false);
});

/* ---------------- navigation ---------------- */
test('join goes to the Jitsi meeting room by default and to the classroom only when enabled', async () => {
  assert.equal(await A.joinUrl(apiReturning({ classroom_enabled: false }), 12, { storage: null }), 'meeting-room.html?batch=12');
  assert.equal(await A.joinUrl(apiReturning({ classroom_enabled: true }), 12, { storage: null }), 'classroom.html?batch=12');
  assert.equal(await A.joinUrl(apiReturning(new Error('x')), 12, { storage: null }), 'meeting-room.html?batch=12');
});

test('goToClass navigates exactly once and encodes the batch id', async () => {
  const seen = [];
  await A.goToClass(apiReturning({ classroom_enabled: false }), '7&x=1', (u) => seen.push(u), { storage: null });
  assert.deepEqual(seen, ['meeting-room.html?batch=7%26x%3D1']);
});

test('classUrl and jitsiUrl only ever produce the two known pages', () => {
  assert.equal(A.jitsiUrl(3), 'meeting-room.html?batch=3');
  assert.equal(A.classUrl(3, true), 'classroom.html?batch=3');
  assert.equal(A.classUrl(3, false), 'meeting-room.html?batch=3');
});

/* ---------------- admin Join control ---------------- */
function fakeScope() {
  const made = [];
  const doc = { createElement: (tag) => { const e = { tag, className: '', title: '', textContent: '' }; made.push(e); return e; } };
  const anchor = { tagName: 'A', ownerDocument: doc, replaced: null, replaceWith(n) { this.replaced = n; } };
  const button = { tagName: 'BUTTON', ownerDocument: doc, style: {} };
  return { anchor, button, made, scope: { querySelectorAll: (sel) => (sel === '[data-admin-class-join]' ? [anchor, button] : []) } };
}
test('admins keep their Jitsi Join control while the classroom is off', async () => {
  const f = fakeScope();
  assert.equal(await A.hideAdminJoinWhenClassroomOn(apiReturning({ classroom_enabled: false }), f.scope, { storage: null }), false);
  assert.equal(f.anchor.replaced, null);
  assert.equal(f.button.style.display, undefined);
});
test('once the classroom is on, admins (who have no seat) lose the Join control', async () => {
  const f = fakeScope();
  assert.equal(await A.hideAdminJoinWhenClassroomOn(apiReturning({ classroom_enabled: true }), f.scope, { storage: null }), true);
  assert.equal(f.button.style.display, 'none');
  assert.equal(f.anchor.replaced.textContent, 'Trainer & students');
});
test('an unreachable server never hides the admin Join control', async () => {
  const f = fakeScope();
  await A.hideAdminJoinWhenClassroomOn(apiReturning(new Error('down')), f.scope, { storage: null });
  assert.equal(f.anchor.replaced, null);
});

/* ---------------- page wiring (static) ---------------- */
const studentJs = strip(read('js', 'student-dashboard.js'));
const staffJs = strip(read('js', 'staff-batch-workspace.js'));
const adminDetailJs = strip(read('js', 'admin-batch-detail.js'));
const adminScheduleJs = strip(read('js', 'admin-class-schedule.js'));
const classroomJs = strip(read('js', 'classroom.js'));

test('student and trainer dashboards never hard-code the LiveKit classroom page', () => {
  assert.doesNotMatch(studentJs, /classroom\.html/);
  assert.doesNotMatch(staffJs, /classroom\.html/);
  assert.equal((studentJs.match(/arckOpenBatchClass\(/g) || []).length, 4, 'one definition + all three student Join entry points');
  assert.equal((staffJs.match(/arckOpenBatchClass\(/g) || []).length, 2, 'one definition + the trainer Join entry point');
  for (const code of [studentJs, staffJs]) {
    assert.match(code, /typeof ArckClassroom !== 'undefined'/);                                  // still joins if the script is missing
    assert.match(code, /meeting-room\.html\?batch=\$\{encodeURIComponent\(batchId\)\}/);
  }
});

test('no page except the classroom module and the classroom page itself knows classroom.html', () => {
  const offenders = [];
  for (const f of fs.readdirSync(path.join(root, 'js')).filter((n) => n.endsWith('.js'))) {
    if (f === 'classroom-availability.js') continue;
    if (/classroom\.html/.test(strip(read('js', f)))) offenders.push(f);
  }
  assert.deepEqual(offenders, []);
});

test('admins keep the original Jitsi Join links and button, marked so they can be hidden when the classroom is on', () => {
  assert.match(adminScheduleJs, /href="meeting-room\.html\?batch=\$\{s\.batch_id\}" data-admin-class-join/);
  assert.match(adminDetailJs, /href="meeting-room\.html\?batch=\$\{batchId\}" data-admin-class-join/);
  assert.match(adminDetailJs, /window\.location\.href = `meeting-room\.html\?batch=\$\{batchId\}`/);       // the original handler, verbatim
  for (const code of [adminDetailJs, adminScheduleJs]) {
    assert.match(code, /typeof ArckClassroom !== 'undefined'\) ArckClassroom\.hideAdminJoinWhenClassroomOn/);   // optional, never required
  }
});

test('every page that uses the module loads it after api.js and before its own script', () => {
  for (const [html, script] of [['student-dashboard.html', 'student-dashboard.js'], ['staff-batch-workspace.html', 'staff-batch-workspace.js'],
    ['admin-batch-detail.html', 'admin-batch-detail.js'], ['admin-class-schedule.html', 'admin-class-schedule.js'], ['classroom.html', 'classroom.js']]) {
    const src = read(html);
    const api = src.indexOf('js/api.js'), mod = src.indexOf('js/classroom-availability.js'), page = src.indexOf(`js/${script}`);
    assert.ok(api > -1 && mod > api && page > mod, `${html}: api.js < classroom-availability.js < ${script}`);
  }
});

test('the Join helper in both dashboards really runs: it delegates when the module is present and falls back to Jitsi when it is not', () => {
  const vm = require('node:vm');
  for (const f of ['student-dashboard.js', 'staff-batch-workspace.js']) {
    const src = read('js', f);
    const fn = src.match(/function arckOpenBatchClass\(batchId\) \{[\s\S]*?\n\}\n/);
    assert.ok(fn, f);
    const calls = [];
    const withModule = { ArckClassroom: { goToClass: (api, id) => calls.push([api, id]) }, ArckAPI: { tag: 'api' }, window: { location: {} } };
    vm.runInNewContext(`${fn[0]}; arckOpenBatchClass(41);`, withModule);
    assert.deepEqual(calls, [[withModule.ArckAPI, 41]], `${f}: delegates exactly once (no recursion)`);
    const without = { window: { location: {} }, ArckAPI: {} };
    vm.runInNewContext(`${fn[0]}; arckOpenBatchClass('9&x');`, without);
    assert.equal(without.window.location.href, 'meeting-room.html?batch=9%26x', `${f}: Jitsi fallback`);
  }
});

test('the classroom page sends a link to a switched-off or unavailable classroom to the existing meeting room', () => {
  assert.match(classroomJs, /typeof ArckClassroom === 'undefined' \|\| !\(await ArckClassroom\.isClassroomEnabled\(ArckAPI\)\)/);
  assert.match(classroomJs, /location\.replace\(`meeting-room\.html\?batch=\$\{encodeURIComponent\(batchId\)\}`\)/);
  assert.match(classroomJs, /\[404, 502, 503\]\.includes\(err\.status\)/);
  assert.match(classroomJs, /Join the classic meeting room instead/);
  assert.doesNotMatch(classroomJs, /\.innerHTML\s*=\s*[^;]*batchId/);       // the batch id from the URL is never injected as HTML
});

test('the module touches only the availability endpoint (no recording, no class-run calls)', () => {
  const code = strip(fs.readFileSync(path.join(root, 'js', 'classroom-availability.js'), 'utf8'));
  assert.deepEqual([...code.matchAll(/request\('([^']+)'/g)].map((m) => m[1]), ['/classroom/availability']);
  assert.doesNotMatch(code, /recording|class-runs|\/meetings\//i);
});

test('the meeting room page still works for general meetings with Jitsi when no LiveKit token is issued', () => {
  const meet = strip(read('js', 'meeting-room.js'));
  assert.match(meet, /if \(joinInfo\.livekit_token && window\.LivekitClient\) \{ await enterLiveKitRoom\(joinInfo\); return; \}/);
  assert.match(meet, /loadJitsiScript\(jitsiDomain\)/);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log('ok  -', name); } catch (e) { failed += 1; console.log('FAIL-', name, '\n   ', e.message); }
  }
  console.log(`\n${tests.length - failed}/${tests.length} passed`);
  process.exit(failed ? 1 : 0);
})();
