/* Run from repository root: node backend/tests/classroom_features.test.cjs
 * Phase 2: raise hand, trainer moderation UI, shared video stage, LiveKit general
 * meetings (no recording), and dashboard Join navigation. Fakes only, no network. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { ClassroomActions, newHands, raisedHands, describe } = require('../../arckenites-orange/js/classroom-actions.js');
const { ClassroomStage } = require('../../arckenites-orange/js/classroom-stage.js');
const { ClassroomMedia } = require('../../arckenites-orange/js/classroom-media.js');

const root = path.join(__dirname, '..', '..', 'arckenites-orange');
const read = (...p) => fs.readFileSync(path.join(root, ...p), 'utf8');
const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

/* ---------------- actions ---------------- */
function fakeApi(handler) {
  const calls = [];
  return { calls, async request(url, opts = {}) { calls.push({ url, method: opts.method || 'GET', body: opts.body }); return handler ? handler(url, opts) : {}; } };
}

test('raise hand posts only {raised} to the class hand endpoint', async () => {
  const api = fakeApi();
  const a = new ClassroomActions(api, 12);
  assert.deepEqual(await a.raiseHand(true), { ok: true, error: '' });
  await a.raiseHand(false);
  assert.deepEqual(api.calls.map((c) => [c.method, c.url, c.body]), [
    ['POST', '/class-runs/12/hand', { raised: true }], ['POST', '/class-runs/12/hand', { raised: false }]]);
});

test('moderation posts the target identity to the trainer-only endpoints, and nothing else', async () => {
  const api = fakeApi();
  const a = new ClassroomActions(api, 12);
  await a.mute('u5'); await a.kick('u5'); await a.lowerHand('u5');
  assert.deepEqual(api.calls.map((c) => c.url), ['/class-runs/12/moderation/mute', '/class-runs/12/moderation/kick', '/class-runs/12/moderation/lower-hand']);
  assert.ok(api.calls.every((c) => c.method === 'POST' && JSON.stringify(c.body) === '{"identity":"u5"}'));
});

test('failures become plain-language errors instead of exceptions', async () => {
  const cases = [[403, /not allowed/], [404, /no longer in this class/], [409, /not running/], [429, /Too many/], [503, /temporarily unavailable/]];
  for (const [status, rx] of cases) {
    const a = new ClassroomActions(fakeApi(() => { throw Object.assign(new Error('x'), { status }); }), 1);
    const r = await a.mute('u1');
    assert.equal(r.ok, false);
    assert.match(r.error, rx);
  }
  assert.equal(describe({ detail: 'Server said no.', status: 403 }), 'Server said no.');
  assert.match(describe(new Error('network')), /did not work/);
});

test('newHands reports only other people whose hand just went up', () => {
  const p = (identity, hand, isLocal = false) => ({ identity, name: identity, hand, isLocal });
  const before = { participants: [p('me', false, true), p('u1', true), p('u2', false)] };
  const after = { participants: [p('me', true, true), p('u1', true), p('u2', true), p('u3', true)] };
  assert.deepEqual(newHands(before, after).map((x) => x.identity), ['u2', 'u3']);
  assert.deepEqual(newHands(null, { participants: [p('u9', true)] }).map((x) => x.identity), ['u9']);
  assert.deepEqual(newHands(after, after), []);
  assert.deepEqual(raisedHands(after).map((x) => x.identity), ['me', 'u1', 'u2', 'u3']);
});

/* ---------------- media: hand attribute ---------------- */
test('the hand flag comes from the server-set participant attribute and changes notify listeners', async () => {
  const lk = { RoomEvent: { ParticipantAttributesChanged: 'attrs', Disconnected: 'dc' }, Track: { Source: { Microphone: 'm', Camera: 'c', ScreenShare: 's' } } };
  const handlers = {};
  const student = { identity: 'u2', name: 'S', metadata: '{"role":"student"}', attributes: { hand: '1' }, getTrackPublication: () => undefined };
  const room = { state: 'connected', remoteParticipants: new Map([['u2', student]]), async connect() {}, disconnect() {},
    on(e, f) { (handlers[e] ||= []).push(f); },
    localParticipant: { identity: 'u1', name: 'T', metadata: '{"role":"trainer"}', attributes: {}, getTrackPublication: () => undefined } };
  const m = new ClassroomMedia(lk, { room });
  await m.connect('wss://x', 't');
  assert.equal(m.state().participants.find((p) => p.identity === 'u2').hand, true);
  assert.equal(m.state().participants.find((p) => p.identity === 'u1').hand, false);
  let n = 0; m.subscribe(() => { n += 1; });
  student.attributes = { hand: '' };
  handlers.attrs.forEach((f) => f());
  assert.equal(n, 1);
  assert.equal(m.state().participants.find((p) => p.identity === 'u2').hand, false);
});

/* ---------------- stage with a tiny fake DOM ---------------- */
function makeEl(tag) {
  const el = { tag, children: [], parent: null, className: '', style: {}, textContent: '', innerHTML: '', classes: new Set(), id: 0,
    classList: { toggle(c, on) { (on ? el.classes.add(c) : el.classes.delete(c)); }, contains: (c) => el.classes.has(c) },
    detach() { if (el.parent) { el.parent.children = el.parent.children.filter((x) => x !== el); el.parent = null; } },
    appendChild(c) { c.detach(); c.parent = el; el.children.push(c); return c; },
    insertBefore(c, ref) { c.detach(); const i = el.children.indexOf(ref); el.children.splice(i < 0 ? el.children.length : i, 0, c); c.parent = el; return c; },
    remove() { el.detach(); },
    get firstChild() { return el.children[0] || null; } };
  return el;
}
const fakeDoc = () => ({ createElement: makeEl, body: makeEl('body') });
const SRC = { Camera: 'camera', Microphone: 'microphone', ScreenShare: 'screen_share' };
const fakeTrack = (label) => ({ label, attached: [], attach(el) { this.attached.push(el); }, detach(el) { this.attached = this.attached.filter((x) => x !== el); } });
const part = (over) => Object.assign({ identity: 'u1', name: 'Asha', isLocal: false, role: 'student', isSpeaking: false, hand: false, micOn: false, cameraOn: false, screenOn: false }, over);
function makeStage(trackMap) {
  const doc = fakeDoc(); const stageEl = makeEl('div');
  const media = { trackFor: (id, src) => trackMap[`${id}:${src}`] || null };
  return { doc, stageEl, stage: new ClassroomStage({ doc, stageEl, media, source: SRC }) };
}

test('stage creates a tile per participant, escapes names and shows trainer, hand and muted markers', () => {
  const { stage, stageEl } = makeStage({});
  stage.render({ participants: [part({ identity: 'me', isLocal: true, name: 'Me' }), part({ identity: 'u2', name: '<img src=x onerror=alert(1)>', role: 'trainer', hand: true })], screenSharer: null });
  assert.equal(stageEl.children.length, 2);
  const plate = stageEl.children[1].children[1];
  assert.doesNotMatch(plate.innerHTML, /<img/);
  assert.match(plate.innerHTML, /&lt;img/);
  assert.match(plate.innerHTML, /Trainer/);
  assert.match(plate.innerHTML, /cls-hand/);
  assert.match(plate.innerHTML, /microphone-slash/);
  assert.match(stageEl.children[0].children[1].innerHTML, /\(You\)/);
});

test('remote audio is attached once, never for yourself, and removed when muted or when the person leaves', () => {
  const mic = fakeTrack('mic');
  const { stage, doc } = makeStage({ 'u2:microphone': mic, 'me:microphone': fakeTrack('own') });
  const state = { participants: [part({ identity: 'me', isLocal: true, micOn: true }), part({ identity: 'u2', micOn: true })], screenSharer: null };
  stage.render(state); stage.render(state);
  assert.equal(doc.body.children.length, 1, 'one audio element for the remote person only');
  assert.equal(mic.attached.length, 1);
  stage.render({ participants: [part({ identity: 'me', isLocal: true }), part({ identity: 'u2', micOn: false })], screenSharer: null });
  assert.equal(doc.body.children.length, 0);
  assert.equal(mic.attached.length, 0);
  stage.render(state);
  stage.render({ participants: [part({ identity: 'me', isLocal: true })], screenSharer: null });
  assert.equal(doc.body.children.length, 0, 'leaving removes the audio element');
});

test('camera video attaches, hides the avatar, and detaches when the camera goes off; people who leave lose their tile', () => {
  const cam = fakeTrack('cam');
  const { stage, stageEl } = makeStage({ 'u2:camera': cam });
  stage.render({ participants: [part({ identity: 'u2', cameraOn: true })], screenSharer: null });
  const tile = stageEl.children[0];
  assert.equal(cam.attached.length, 1);
  assert.equal(tile.children[0].tag, 'video');
  assert.equal(cam.attached[0].muted, true, 'video elements never carry audio');
  stage.render({ participants: [part({ identity: 'u2', cameraOn: false })], screenSharer: null });
  assert.equal(cam.attached.length, 0);
  stage.render({ participants: [], screenSharer: null });
  assert.equal(stageEl.children.length, 0);
});

test('a screen share gets its own tile, placed first, and the stage switches to screen layout', () => {
  const scr = fakeTrack('screen');
  const { stage, stageEl } = makeStage({ 'u2:screen_share': scr });
  const state = { participants: [part({ identity: 'u1' }), part({ identity: 'u2', screenOn: true })], screenSharer: 'u2' };
  stage.render(state);
  assert.equal(stageEl.children.length, 3);
  assert.match(stageEl.children[0].className, /is-screen/);
  assert.equal(stageEl.classes.has('has-screen'), true);
  stage.render({ participants: [part({ identity: 'u1' }), part({ identity: 'u2' })], screenSharer: null });
  assert.equal(stageEl.children.length, 2);
  assert.equal(stageEl.classes.has('has-screen'), false);
  assert.equal(scr.attached.length, 0);
});

test('destroy clears tiles and audio', () => {
  const mic = fakeTrack('mic');
  const { stage, stageEl, doc } = makeStage({ 'u2:microphone': mic });
  stage.render({ participants: [part({ identity: 'u2', micOn: true })], screenSharer: null });
  stage.destroy();
  assert.equal(stageEl.children.length, 0);
  assert.equal(doc.body.children.length, 0);
});

/* ---------------- classroom page wiring and permissions ---------------- */
const classroomJs = read('js', 'classroom.js');
const classroomHtml = read('classroom.html');

test('the hand button is hidden by default and only shown to non-trainers; moderation buttons only render for the trainer', () => {
  assert.match(classroomHtml, /id="clsToolHand"[^>]*style="display:none;"/);
  assert.match(classroomJs, /if \(!isTrainer\) \$\('clsToolHand'\)\.style\.display = '';/);
  assert.match(classroomJs, /\$\{isTrainer && !p\.isLocal && p\.role !== 'trainer' \?/);
  assert.match(classroomJs, /if \(!btn \|\| !isTrainer\) return;/);
  assert.match(classroomJs, /Remove this student from the class\?/, 'removal asks for confirmation');
});

test('trainers are notified when a student raises a hand', () => {
  assert.match(classroomJs, /ArckClassroomActions\.newHands\(lastState, s\)/);
  assert.match(strip(classroomJs), /raised their hand/);
});

test('scripts load in dependency order and every id used exists', () => {
  const at = (s) => classroomHtml.indexOf(s);
  ['classroom-media.js', 'classroom-stage.js', 'classroom-actions.js', 'classroom-recording.js', 'classroom-chat.js', 'classroom-notes.js', 'classroom.js']
    .forEach((f) => assert.ok(at(`js/${f}`) > -1, f));
  assert.ok(at('js/classroom-stage.js') < at('js/classroom.js') && at('js/classroom-actions.js') < at('js/classroom.js'));
  const runtime = new Set(['clsJoinBtn', 'clsPrejoinError', 'clsChatReconnect']);
  const ids = [...classroomJs.matchAll(/\$\('(cls[A-Za-z]+)'\)/g)].map((m) => m[1]);
  assert.deepEqual(ids.filter((id) => !runtime.has(id) && !classroomHtml.includes(`id="${id}"`)), []);
});

test('the classroom never contacts the media server or admin routes directly', () => {
  const code = strip(classroomJs + read('js', 'classroom-actions.js') + read('js', 'classroom-stage.js'));
  assert.doesNotMatch(code, /\/admin\/|\/meetings\/|roomService|updateParticipant|removeParticipant/);
});

/* ---------------- navigation ---------------- */
test('student and trainer dashboards open batch classes in the classroom page, not the general-meeting page', () => {
  for (const f of ['student-dashboard.js', 'staff-batch-workspace.js']) {
    const code = read('js', f);
    assert.doesNotMatch(code, /meeting-room\.html\?batch=/, f);
    assert.match(code, /classroom\.html\?batch=/, f);
  }
  assert.match(read('js', 'student-dashboard.js'), /meeting-room\.html\?token=/, 'general meetings still use the meeting page');
  assert.match(read('js', 'staff-dashboard.js'), /meeting-room\.html\?token=/);
});

test('admin pages offer no way to join a batch class', () => {
  for (const f of ['admin-batch-detail.js', 'admin-class-schedule.js']) {
    const code = read('js', f);
    assert.doesNotMatch(code, /(meeting-room|classroom)\.html\?batch=/, f);
  }
  assert.match(read('js', 'admin-batch-detail.js'), /getElementById\('joinBatchVideoBtn'\)\.style\.display = 'none'/);
  assert.match(read('js', 'admin-meetings.js'), /meeting-room\.html\?token=/, 'admins still host general meetings');
});

/* ---------------- general meetings over LiveKit ---------------- */
test('the general-meeting page switches to LiveKit only when the server supplies a token, and keeps the legacy fallback', () => {
  const js = read('js', 'meeting-room.js');
  assert.match(js, /joinInfo\.livekit_token && window\.LivekitClient/);
  assert.match(js, /new ArckClassroomMedia\.ClassroomMedia\(lk\)/);
  assert.match(js, /new ArckClassroomStage\.ClassroomStage/);
  assert.match(js, /new window\.JitsiMeetExternalAPI/, 'fallback is intact');
  assert.match(js, /connect\(joinInfo\.livekit_url, joinInfo\.livekit_token\)/);
});

test('general meetings have no recording control, no class-run calls and no notes/chat from the classroom modules', () => {
  const code = strip(read('js', 'meeting-room.js'));
  const html = read('meeting-room.html');
  assert.doesNotMatch(code + html, /class-runs|classroom-recording|classroom-notes|classroom-chat|ArckClassroomActions/);
  assert.doesNotMatch(code + html, /meetToolRecord|meetRecordingBadge|toggleRecording|recordings\/start/);
  ['classroom-media.js', 'classroom-stage.js'].forEach((f) => assert.ok(html.includes(`js/${f}`), f));
  const at = (s) => html.indexOf(s);
  assert.ok(at('livekit-client') < at('classroom-media.js') && at('classroom-media.js') < at('js/meeting-room.js'));
});

/* ---------------- End class ---------------- */
test('endClass posts to the existing authenticated end endpoint with no user id and reports server refusals plainly', async () => {
  const api = fakeApi();
  const a = new ClassroomActions(api, 12);
  assert.deepEqual(await a.endClass(), { ok: true, error: '' });
  assert.deepEqual(api.calls.map((c) => [c.method, c.url, c.body]), [['POST', '/class-runs/12/end', {}]]);
  for (const [status, rx] of [[403, /not allowed/], [404, /no longer/], [409, /not running/]]) {
    const refused = new ClassroomActions(fakeApi(() => { throw Object.assign(new Error('x'), { status }); }), 12);
    const r = await refused.endClass();
    assert.equal(r.ok, false);
    assert.match(r.error, rx);
  }
});

test('the End class button is hidden by default and only the trainer branch reveals and wires it', () => {
  assert.match(classroomHtml, /id="clsToolEnd"[^>]*style="display:none;"/);
  const code = strip(classroomJs);
  const reveal = code.indexOf("$('clsToolEnd').style.display = '';");
  const trainerBranch = code.indexOf('if (isTrainer) {', code.indexOf('function startActionsUi'));
  assert.ok(trainerBranch > -1 && reveal > trainerBranch, 'revealed only inside the trainer branch');
  assert.equal((code.match(/clsToolEnd'\)\.style\.display = ''/g) || []).length, 1, 'no other code path shows it');
  assert.equal((code.match(/actions\.endClass\(\)/g) || []).length, 1);
});

test('ending the class asks for confirmation first, and nothing is sent if the trainer cancels', () => {
  const code = strip(classroomJs);
  const handler = code.slice(code.indexOf("$('clsToolEnd').addEventListener"));
  const confirmAt = handler.indexOf('window.confirm(');
  const callAt = handler.indexOf('actions.endClass()');
  assert.ok(confirmAt > -1 && callAt > confirmAt, 'confirm precedes the request');
  assert.match(handler.slice(confirmAt, callAt), /return;/, 'cancelling returns before any request');
  assert.match(handler, /End this class for everyone now\?/);
  assert.match(handler, /disabled = true/, 'double clicks are blocked while the request runs');
});

test('after a successful end the trainer leaves the room; a failure keeps them in the class and re-enables the button', () => {
  const handler = strip(classroomJs).slice(strip(classroomJs).indexOf("$('clsToolEnd').addEventListener"));
  assert.match(handler, /if \(ok\) leaveClassroom\(/);
  assert.match(handler, /else \$\('clsToolEnd'\)\.disabled = false;/);
  assert.match(strip(classroomJs), /function leaveClassroom\(message\)/);
  assert.match(strip(classroomJs), /\$\('clsToolLeave'\)\.addEventListener\('click', \(\) => \{\s*if \(!window\.confirm\('Leave this classroom\?'\)\) return;\s*leaveClassroom\(\);/);
});

test('everyone else is moved out when the server reports the class has ended, once, and polling stops', async () => {
  const { ClassroomRecording } = require('../../arckenites-orange/js/classroom-recording.js');
  const base = { run_id: 7, batch_id: 3, run_state: 'LIVE', recording_state: 'RECORDING', approval_status: 'APPROVED', can_control: false, is_recording: true, consent_version: 'v1', consent_text: 't' };
  const seq = [base, { ...base, run_state: 'ENDED', is_recording: false, recording_state: 'STOPPING' }, { ...base, run_state: 'ENDED', is_recording: false }];
  const api = { async request() { return seq.shift(); } };
  const timers = { handles: 0, cleared: 0, setInterval() { timers.handles += 1; return 1; }, clearInterval() { timers.cleared += 1; } };
  const r = new ClassroomRecording(api, 7, base, timers);
  let ended = 0; r.onClassEnded = () => { ended += 1; };
  await r.start();
  await r.refresh();   // still live
  assert.equal(ended, 0);
  await r.refresh();   // ended
  await r.refresh();   // ended again: no second notification
  assert.equal(ended, 1);
  assert.equal(timers.cleared, 1, 'polling stopped');
  assert.match(strip(classroomJs), /recorder\.onClassEnded = \(\) => leaveClassroom\(/);
});

test('a LiveKit meeting applies the current connection state immediately, not only after the next event', () => {
  // Found in a real two-browser run: a host alone in a quiet room stayed on "Connecting..." until someone else joined.
  const js = read('js', 'meeting-room.js');
  assert.match(js, /lkMedia\.subscribe\(applyState\);\s*applyState\(lkMedia\.state\(\)\);/);
});

test('device errors in a LiveKit meeting are reported after the button press, not on every event', () => {
  const js = read('js', 'meeting-room.js');
  assert.match(js, /lkMediaAction\(lkMedia\.toggleMicrophone\(\)\)/);
  assert.doesNotMatch(js, /if \(st\.error\) window\.alert/);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log('ok  -', name); } catch (e) { failed += 1; console.log('FAIL-', name, '\n   ', String(e.message).slice(0, 500)); }
  }
  console.log(`\n${tests.length - failed}/${tests.length} passed`);
  process.exit(failed ? 1 : 0);
})();
