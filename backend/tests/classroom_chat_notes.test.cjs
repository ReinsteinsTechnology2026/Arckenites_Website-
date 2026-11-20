/* Run from repository root: node backend/tests/classroom_chat_notes.test.cjs
 * Phase 2 milestone 3: classroom chat (reusing batch chat + WebSocket) and a
 * student's private notes with autosave. Fake API/socket/timers, no network. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { ClassroomChat, MAX_CHARS, BACKOFF } = require('../../arckenites-orange/js/classroom-chat.js');
const { ClassroomNotes, DEBOUNCE_MS, RETRY_MS, MAX_CHARS: NOTE_MAX } = require('../../arckenites-orange/js/classroom-notes.js');

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

const msg = (id, text, over = {}) => Object.assign({ id, batch_id: 5, sender_id: 1, sender_name: 'Asha', message: text, created_at: `2026-11-18T10:00:${String(id).padStart(2, '0')}Z` }, over);

function fakeApi(handlers) {
  const calls = [];
  return { calls, async request(url, opts = {}) {
    const key = `${opts.method || 'GET'} ${url}`;
    calls.push([key, opts.body]);
    const h = handlers[key];
    if (h === undefined) throw Object.assign(new Error('no handler ' + key), { detail: 'no handler' });
    if (h instanceof Error) throw h;
    return typeof h === 'function' ? h(opts.body) : h;
  } };
}
function fakeTimers() {
  const t = { queue: [], cleared: [], setTimeout(f, ms) { t.queue.push({ f, ms }); return t.queue.length - 1; }, clearTimeout(h) { t.cleared.push(h); if (t.queue[h]) t.queue[h].dead = true; },
    async runNext() { const e = t.queue.find((x) => !x.dead && !x.ran); if (!e) return false; e.ran = true; await e.f(); return true; },
    pending() { return t.queue.filter((x) => !x.dead && !x.ran); } };
  return t;
}
function fakeSocket() {
  const s = { sockets: [], open(onMessage, onOpen, onClose) { const sock = { onMessage, onOpen, onClose, closed: false, close() { sock.closed = true; } }; s.sockets.push(sock); return sock; },
    get last() { return s.sockets[s.sockets.length - 1]; } };
  return s;
}
const flush = () => new Promise((r) => setImmediate(r));

/* ===================== chat ===================== */
const chatPath = (role) => `/${role === 'student' ? 'students' : 'staff'}/me/batches/5/chat/messages`;

test('chat uses the role-scoped batch chat endpoint for students and for the trainer', async () => {
  for (const role of ['student', 'staff']) {
    const api = fakeApi({ [`GET ${chatPath(role)}`]: [msg(1, 'hi')] });
    const sock = fakeSocket();
    const c = new ClassroomChat(api, { batchId: 5, role, openSocket: sock.open, timers: fakeTimers() });
    await c.start();
    assert.equal(api.calls[0][0], `GET ${chatPath(role)}`);
    assert.equal(c.state().messages.length, 1);
  }
});

test('admins and other roles cannot even construct a chat', () => {
  for (const role of ['admin', 'community', undefined]) {
    assert.throws(() => new ClassroomChat(fakeApi({}), { batchId: 5, role, openSocket() {} }), /only available/);
  }
});

test('sending posts the trimmed text; the WebSocket echo of the same message is not duplicated', async () => {
  const api = fakeApi({ [`GET ${chatPath('student')}`]: [], [`POST ${chatPath('student')}`]: (b) => msg(7, b.message) });
  const sock = fakeSocket();
  const c = new ClassroomChat(api, { batchId: 5, role: 'student', openSocket: sock.open, timers: fakeTimers() });
  await c.start();
  const res = await c.send('  hello class  ');
  assert.deepEqual(res, { ok: true, error: '' });
  assert.deepEqual(api.calls.find((x) => x[0].startsWith('POST'))[1], { message: 'hello class' });
  sock.last.onMessage({ type: 'batch_message', batch_id: 5, message: msg(7, 'hello class') });   // server fan-out includes the sender
  assert.equal(c.state().messages.length, 1);
});

test('live messages from the socket appear and notify; other batches and other event types are ignored', async () => {
  const api = fakeApi({ [`GET ${chatPath('student')}`]: [] });
  const sock = fakeSocket();
  const c = new ClassroomChat(api, { batchId: 5, role: 'student', openSocket: sock.open, timers: fakeTimers() });
  const seen = []; c.onIncoming = (m) => seen.push(m.id);
  await c.start();
  sock.last.onMessage({ type: 'batch_message', batch_id: 5, message: msg(2, 'live') });
  sock.last.onMessage({ type: 'batch_message', batch_id: 6, message: msg(3, 'other batch', { batch_id: 6 }) });
  sock.last.onMessage({ type: 'batch_message', batch_id: 5, message: msg(4, 'forged batch id', { batch_id: 6 }) });
  sock.last.onMessage({ type: 'meeting_message', batch_id: 5, message: msg(5, 'wrong type') });
  sock.last.onMessage(null);
  assert.deepEqual(c.state().messages.map((m) => m.id), [2]);
  assert.deepEqual(seen, [2]);
});

test('messages are ordered by time even when they arrive out of order', async () => {
  const api = fakeApi({ [`GET ${chatPath('staff')}`]: [msg(3, 'c'), msg(1, 'a')] });
  const sock = fakeSocket();
  const c = new ClassroomChat(api, { batchId: 5, role: 'staff', openSocket: sock.open, timers: fakeTimers() });
  await c.start();
  sock.last.onMessage({ type: 'batch_message', batch_id: 5, message: msg(2, 'b') });
  assert.deepEqual(c.state().messages.map((m) => m.message), ['a', 'b', 'c']);
});

test('a failed send keeps the error visible, reports not-ok so the text can be kept, and can be retried', async () => {
  let fail = true;
  const api = fakeApi({ [`GET ${chatPath('student')}`]: [], [`POST ${chatPath('student')}`]: (b) => { if (fail) throw Object.assign(new Error('x'), { detail: 'You are not in this batch.' }); return msg(9, b.message); } });
  const c = new ClassroomChat(api, { batchId: 5, role: 'student', openSocket: fakeSocket().open, timers: fakeTimers() });
  await c.start();
  const bad = await c.send('hello');
  assert.deepEqual(bad, { ok: false, error: 'You are not in this batch.' });
  assert.equal(c.state().error, 'You are not in this batch.');
  assert.equal(c.state().messages.length, 0);
  assert.equal(c.state().sending, false);
  fail = false;
  assert.equal((await c.send('hello')).ok, true);
  assert.equal(c.state().error, '');
});

test('empty, whitespace-only and over-long messages are refused before any request', async () => {
  const api = fakeApi({ [`GET ${chatPath('student')}`]: [] });
  const c = new ClassroomChat(api, { batchId: 5, role: 'student', openSocket: fakeSocket().open, timers: fakeTimers() });
  await c.start();
  assert.equal((await c.send('   ')).ok, false);
  const long = await c.send('x'.repeat(MAX_CHARS + 1));
  assert.equal(long.ok, false);
  assert.match(long.error, /at most 2000/);
  assert.equal(api.calls.filter((x) => x[0].startsWith('POST')).length, 0);
});

test('a dropped connection is reported, retried with backoff, and missed messages are reloaded on reconnect', async () => {
  let history = [msg(1, 'first')];
  const api = fakeApi({ [`GET ${chatPath('student')}`]: () => history });
  const sock = fakeSocket(); const timers = fakeTimers();
  const c = new ClassroomChat(api, { batchId: 5, role: 'student', openSocket: sock.open, timers });
  await c.start();
  sock.last.onOpen();
  assert.equal(c.state().status, 'connected');
  sock.last.onClose();
  assert.equal(c.state().status, 'reconnecting');
  assert.equal(timers.pending()[0].ms, BACKOFF[0]);
  history = [msg(1, 'first'), msg(2, 'missed while offline')];
  await timers.runNext();
  assert.equal(sock.sockets.length, 2);
  sock.last.onOpen();
  await flush();
  assert.equal(c.state().status, 'connected');
  assert.deepEqual(c.state().messages.map((m) => m.id), [1, 2]);
});

test('repeated failures back off, then show offline with a manual reconnect', async () => {
  const api = fakeApi({ [`GET ${chatPath('student')}`]: [] });
  const sock = fakeSocket(); const timers = fakeTimers();
  const c = new ClassroomChat(api, { batchId: 5, role: 'student', openSocket: sock.open, timers });
  await c.start();
  const delays = [];
  for (let i = 0; i < BACKOFF.length + 1; i += 1) { sock.last.onClose(); delays.push(timers.pending()[0].ms); await timers.runNext(); }
  sock.last.onClose();
  assert.deepEqual(delays.slice(0, BACKOFF.length), BACKOFF);
  assert.equal(c.state().status, 'offline');
  const before = sock.sockets.length;
  c.reconnectNow();
  assert.equal(sock.sockets.length, before + 1);
});

test('a failed history load is reported and recovered on reconnect; stop() closes the socket and cancels retries', async () => {
  let fail = true;
  const api = fakeApi({ [`GET ${chatPath('student')}`]: () => { if (fail) throw Object.assign(new Error('x'), { detail: 'Could not load earlier messages.' }); return [msg(1, 'a')]; } });
  const sock = fakeSocket(); const timers = fakeTimers();
  const c = new ClassroomChat(api, { batchId: 5, role: 'student', openSocket: sock.open, timers });
  await c.start();
  assert.equal(c.state().error, 'Could not load earlier messages.');
  assert.equal(c.state().loaded, false);
  fail = false;
  sock.last.onOpen(); await flush();
  assert.equal(c.state().loaded, true);
  assert.equal(c.state().error, '');
  sock.last.onClose();
  c.stop();
  assert.equal(sock.last.closed, true);
  assert.equal(timers.pending().length, 0);
});

/* ===================== notes ===================== */
const notePath = '/class-runs/12/notes/me';

test('notes load from the owner-only endpoint and editing stays disabled until they have loaded', async () => {
  const api = fakeApi({ [`GET ${notePath}`]: { run_id: 12, content: 'old notes' } });
  const n = new ClassroomNotes(api, 12, fakeTimers());
  assert.equal(n.state().canEdit, false);
  assert.equal(n.edit('typed too early'), false);
  await n.load();
  assert.equal(n.state().canEdit, true);
  assert.equal(n.state().content, 'old notes');
  assert.equal(n.state().status, 'saved');
  assert.deepEqual(api.calls.map((c) => c[0]), [`GET ${notePath}`]);
});

test('a failed load never lets a blank page overwrite the saved notes', async () => {
  let fail = true;
  const api = fakeApi({ [`GET ${notePath}`]: () => { if (fail) throw Object.assign(new Error('x'), { detail: 'Class not found' }); return { content: 'precious' }; }, [`POST ${notePath}`]: {} });
  const timers = fakeTimers();
  const n = new ClassroomNotes(api, 12, timers);
  await n.load();
  assert.equal(n.state().status, 'load-failed');
  assert.equal(n.state().canEdit, false);
  assert.equal(n.edit('x'), false);
  assert.equal(await n.save(), true, 'nothing dirty, nothing sent');
  assert.equal(api.calls.some((c) => c[0].startsWith('POST')), false);
  fail = false;
  await n.retryNow();
  assert.equal(n.state().content, 'precious');
  assert.equal(n.state().canEdit, true);
});

test('typing autosaves after a pause, once, with the latest text; the body has no user id', async () => {
  const api = fakeApi({ [`GET ${notePath}`]: { content: '' }, [`POST ${notePath}`]: {} });
  const timers = fakeTimers();
  const n = new ClassroomNotes(api, 12, timers);
  await n.load();
  n.edit('a'); n.edit('ab'); n.edit('abc');
  assert.equal(n.state().status, 'dirty');
  assert.equal(timers.pending().length, 1, 'edits are debounced into one timer');
  assert.equal(timers.pending()[0].ms, DEBOUNCE_MS);
  await timers.runNext();
  const puts = api.calls.filter((c) => c[0].startsWith('POST'));
  assert.equal(puts.length, 1);
  assert.deepEqual(puts[0][1], { content: 'abc' });
  assert.equal(n.state().status, 'saved');
  assert.equal(n.hasUnsaved(), false);
});

test('text typed while a save is in flight is saved next and nothing is lost', async () => {
  let release; const gate = new Promise((r) => { release = r; });
  const bodies = [];
  const api = fakeApi({ [`GET ${notePath}`]: { content: '' }, [`POST ${notePath}`]: async (b) => { bodies.push(b.content); if (bodies.length === 1) await gate; return {}; } });
  const n = new ClassroomNotes(api, 12, fakeTimers());
  await n.load();
  n.edit('first');
  const saving = n.save();
  await flush();
  assert.equal(n.state().status, 'saving');
  n.edit('first and more');
  release();
  await saving;
  assert.deepEqual(bodies, ['first', 'first and more']);
  assert.equal(n.state().status, 'saved');
  assert.equal(n.hasUnsaved(), false);
});

test('a failed save keeps the text, shows the error and retries with backoff until it works', async () => {
  let fail = 2;
  const api = fakeApi({ [`GET ${notePath}`]: { content: '' }, [`POST ${notePath}`]: () => { if (fail-- > 0) throw Object.assign(new Error('x'), { detail: 'Could not reach the server.' }); return {}; } });
  const timers = fakeTimers();
  const n = new ClassroomNotes(api, 12, timers);
  await n.load();
  n.edit('keep me');
  await timers.runNext();
  assert.equal(n.state().status, 'error');
  assert.equal(n.state().error, 'Could not reach the server.');
  assert.equal(n.state().content, 'keep me');
  assert.equal(n.hasUnsaved(), true);
  assert.equal(timers.pending()[0].ms, RETRY_MS[0]);
  await timers.runNext();
  assert.equal(timers.pending()[0].ms, RETRY_MS[1]);
  await timers.runNext();
  assert.equal(n.state().status, 'saved');
  assert.equal(n.state().error, '');
  assert.equal(n.hasUnsaved(), false);
});

test('leaving flushes pending notes immediately; stop() cancels the timer', async () => {
  const api = fakeApi({ [`GET ${notePath}`]: { content: '' }, [`POST ${notePath}`]: {} });
  const timers = fakeTimers();
  const n = new ClassroomNotes(api, 12, timers);
  await n.load();
  n.edit('before leaving');
  assert.equal(await n.save(), true);
  assert.equal(api.calls.filter((c) => c[0].startsWith('POST')).length, 1);
  assert.equal(timers.pending().length, 0);
  n.edit('x'); n.stop();
  assert.equal(timers.pending().length, 0);
});

test('notes over the size limit are not sent and the student is told why', async () => {
  const api = fakeApi({ [`GET ${notePath}`]: { content: '' }, [`POST ${notePath}`]: {} });
  const timers = fakeTimers();
  const n = new ClassroomNotes(api, 12, timers);
  await n.load();
  n.edit('x'.repeat(NOTE_MAX + 1));
  assert.equal(n.state().status, 'error');
  assert.match(n.state().error, /at most 20000/);
  assert.equal(await n.save(), false);
  assert.equal(api.calls.some((c) => c[0].startsWith('POST')), false);
  n.edit('x'.repeat(NOTE_MAX));
  assert.equal(n.state().status, 'dirty');
});

/* ===================== page wiring and permissions ===================== */
const root = path.join(__dirname, '..', '..', 'arckenites-orange');
const html = fs.readFileSync(path.join(root, 'classroom.html'), 'utf8');
const js = fs.readFileSync(path.join(root, 'js', 'classroom.js'), 'utf8');
const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

test('page loads the chat and notes modules before classroom.js and every id it uses exists', () => {
  const at = (s) => html.indexOf(s);
  assert.ok(at('js/classroom-chat.js') > -1 && at('js/classroom-notes.js') > -1);
  assert.ok(at('js/classroom-chat.js') < at('js/classroom.js') && at('js/classroom-notes.js') < at('js/classroom.js'));
  const runtime = new Set(['clsJoinBtn', 'clsPrejoinError', 'clsChatReconnect']);
  const ids = [...js.matchAll(/\$\('(cls[A-Za-z]+)'\)/g)].map((m) => m[1]);
  assert.deepEqual(ids.filter((id) => !runtime.has(id) && !html.includes(`id="${id}"`)), []);
});

test('the notes tab is hidden by default and only a student (not the trainer) ever reveals it', () => {
  assert.match(html, /id="clsNotesTab"[^>]*style="display:none;"/);
  assert.match(js, /if \(info\.is_trainer \|\| user\.role !== 'student'\) return;/);
  assert.match(js, /if \(!\['student', 'staff'\]\.includes\(user\.role\)\) return;/, 'admins get no chat');
});

test('chat and notes only call the existing/own endpoints and never take a user id', () => {
  const chat = strip(fs.readFileSync(path.join(root, 'js', 'classroom-chat.js'), 'utf8'));
  const notes = strip(fs.readFileSync(path.join(root, 'js', 'classroom-notes.js'), 'utf8'));
  assert.doesNotMatch(notes, /user_?id|userId|student_?id/i);
  assert.doesNotMatch(chat + notes + strip(js), /\/meetings\/|\/admin\//);
  assert.match(notes, /\/class-runs\/\$\{Number\(runId\)\}\/notes\/me/);
});

test('chat text, sender names and note status are rendered escaped or via textContent', () => {
  const code = strip(js);
  const raw = code.split('\n').filter((l) => /\$\{m\.(message|sender_name)\}|\$\{s\.error\}/.test(l));
  assert.deepEqual(raw, [], 'user text interpolated into HTML without clsEscape');
  assert.match(code, /clsEscape\(m\.message\)/);
  assert.match(code, /clsNotesStatus'\)\.textContent/);
  assert.match(code, /err\.textContent = s\.error/);
});

test('the Milestone 1 and 2 pieces are still wired in', () => {
  assert.match(js, /startRecordingUi\(info\)/);
  assert.match(js, /media\.subscribe\(render\)/);
  assert.ok(['classroom-media.js', 'classroom-recording.js'].every((f) => html.includes(f)));
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log('ok  -', name); } catch (e) { failed += 1; console.log('FAIL-', name, '\n   ', String(e.message).slice(0, 400)); }
  }
  console.log(`\n${tests.length - failed}/${tests.length} passed`);
  process.exit(failed ? 1 : 0);
})();
