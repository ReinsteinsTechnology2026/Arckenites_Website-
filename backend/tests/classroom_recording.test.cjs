/* Run from repository root: node backend/tests/classroom_recording.test.cjs
 * Phase 2 milestone 2: recording indicator + trainer-only approval flow.
 * Fake API, fake timers, no network. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { ClassroomRecording, POLL_MS } = require('../../arckenites-orange/js/classroom-recording.js');

const base = {
  run_id: 7, batch_id: 3, run_state: 'ROOM_OPEN', recording_state: 'AWAITING_APPROVAL', approval_status: 'PENDING',
  approval_source: null, approval_decided_at: null, can_control: false, is_recording: false,
  consent_version: 'v1', consent_text: 'This class may be recorded.',
};
const status = (over) => Object.assign({}, base, over);

function fakeApi(responses = {}) {
  const calls = [];
  return {
    calls,
    async request(url, opts = {}) {
      calls.push([opts.method || 'GET', url, opts.body]);
      const r = responses[`${opts.method || 'GET'} ${url}`];
      if (r instanceof Error) throw r;
      return typeof r === 'function' ? r(opts.body) : r;
    },
  };
}
function fakeTimers() {
  const t = { fns: [], cleared: 0, setInterval(f, ms) { t.ms = ms; t.fns.push(f); return t.fns.length; }, clearInterval() { t.cleared += 1; } };
  return t;
}
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test('the assigned trainer is prompted once, and the dialog opens with the server wording', async () => {
  const api = fakeApi({ 'POST /class-runs/7/recording/prompt': { show: true, consent_version: 'v2', consent_text: 'New wording' } });
  const r = new ClassroomRecording(api, 7, status({ can_control: true }), fakeTimers());
  await r.start();
  assert.deepEqual(api.calls[0].slice(0, 2), ['POST', '/class-runs/7/recording/prompt']);
  const v = r.view();
  assert.equal(v.canDecide, true);
  assert.equal(v.showDialog, true);
  assert.equal(v.consentText, 'New wording');
  assert.equal(v.indicator, 'none');
});

test('if the server says the prompt was already shown, the dialog stays closed but the trainer can reopen it', async () => {
  const api = fakeApi({ 'POST /class-runs/7/recording/prompt': { show: false, consent_version: 'v1', consent_text: 'x' } });
  const r = new ClassroomRecording(api, 7, status({ can_control: true }), fakeTimers());
  await r.start();
  assert.equal(r.view().showDialog, false);
  assert.equal(r.view().canDecide, true);
  r.openDialog();
  assert.equal(r.view().showDialog, true);
  r.closeDialog();
  assert.equal(r.view().showDialog, false);
  assert.equal(r.view().canDecide, true, 'closing the window is not a decision');
  assert.equal(api.calls.filter((c) => c[1].endsWith('/approval')).length, 0);
});

test('approving posts the approval with the consent version the trainer was shown and closes the dialog', async () => {
  const api = fakeApi({
    'POST /class-runs/7/recording/prompt': { show: true, consent_version: 'v2', consent_text: 'T' },
    'POST /class-runs/7/recording/approval': (body) => status({ can_control: true, approval_status: 'APPROVED', recording_state: 'STARTING', consent_version: body.consent_version }),
  });
  const r = new ClassroomRecording(api, 7, status({ can_control: true }), fakeTimers());
  await r.start();
  assert.equal(await r.decide('approve'), true);
  const post = api.calls.find((c) => c[1].endsWith('/approval'));
  assert.deepEqual(post[2], { decision: 'approve', consent_version: 'v2' });
  const v = r.view();
  assert.equal(v.canDecide, false);
  assert.equal(v.showDialog, false);
  assert.equal(v.indicator, 'starting', 'no start call is made: the server starts recording after approval');
  assert.equal(api.calls.some((c) => /recordings?\/start|\/start/.test(c[1])), false);
});

test('declining posts decline, never shows a recording indicator, and tells the trainer', async () => {
  const api = fakeApi({
    'POST /class-runs/7/recording/prompt': { show: true, consent_version: 'v1', consent_text: 'T' },
    'POST /class-runs/7/recording/approval': status({ can_control: true, approval_status: 'DECLINED', recording_state: 'DECLINED' }),
  });
  const r = new ClassroomRecording(api, 7, status({ can_control: true }), fakeTimers());
  await r.start();
  await r.decide('decline');
  const v = r.view();
  assert.equal(api.calls.find((c) => c[1].endsWith('/approval'))[2].decision, 'decline');
  assert.equal(v.indicator, 'none');
  assert.equal(v.canDecide, false);
  assert.match(v.notice, /will not be recorded/);
});

test('a failed decision keeps the dialog undecided, shows the server error and allows a retry', async () => {
  let fail = true;
  const api = fakeApi({
    'POST /class-runs/7/recording/prompt': { show: true, consent_version: 'v1', consent_text: 'T' },
    'POST /class-runs/7/recording/approval': () => { if (fail) throw Object.assign(new Error('x'), { detail: 'The recording notice has changed.' }); return status({ can_control: true, approval_status: 'APPROVED', recording_state: 'STARTING' }); },
  });
  const r = new ClassroomRecording(api, 7, status({ can_control: true }), fakeTimers());
  await r.start();
  assert.equal(await r.decide('approve'), false);
  assert.equal(r.view().error, 'The recording notice has changed.');
  assert.equal(r.view().canDecide, true);
  assert.equal(r.view().busy, false);
  fail = false;
  assert.equal(await r.decide('approve'), true);
  assert.equal(r.view().error, '');
});

test('students never get controls and the prompt/approval endpoints are never called for them', async () => {
  const api = fakeApi({ 'GET /class-runs/7/recording': status({ is_recording: true, recording_state: 'RECORDING', approval_status: 'APPROVED' }) });
  const r = new ClassroomRecording(api, 7, status({ can_control: false }), fakeTimers());
  await r.start();
  assert.equal(r.view().canDecide, false);
  assert.equal(await r.decide('approve'), false);
  assert.equal(await r.decide('decline'), false);
  r.openDialog();
  assert.equal(r.view().showDialog, false);
  assert.deepEqual(api.calls, [], 'no recording-control request was sent');
});

test('admins (read-only, can_control=false) are treated exactly like students', async () => {
  const api = fakeApi({});
  const r = new ClassroomRecording(api, 7, status({ can_control: false, approval_status: 'PENDING' }), fakeTimers());
  await r.start();
  assert.equal(r.view().canDecide, false);
  assert.equal(await r.decide('approve'), false);
  assert.equal(api.calls.length, 0);
});

test('everyone sees the indicator only while recording is verifiably active', async () => {
  for (const can_control of [false, true]) {
    const r = new ClassroomRecording(fakeApi(), 7, status({ can_control, approval_status: 'APPROVED', recording_state: 'RECORDING', is_recording: true }), fakeTimers());
    assert.equal(r.view().indicator, 'recording');
  }
  const studentApproved = new ClassroomRecording(fakeApi(), 7, status({ approval_status: 'APPROVED', recording_state: 'STARTING' }), fakeTimers());
  assert.equal(studentApproved.view().indicator, 'none', 'students see nothing about the pipeline until recording is live');
  const none = new ClassroomRecording(fakeApi(), 7, status(), fakeTimers());
  assert.equal(none.view().indicator, 'none');
});

test('polling updates the indicator during class and reports start and stop transitions', async () => {
  const seq = [status({ is_recording: true, recording_state: 'RECORDING', approval_status: 'APPROVED' }), status({ recording_state: 'PROCESSING', approval_status: 'APPROVED' })];
  const api = fakeApi({ 'GET /class-runs/7/recording': () => seq.shift() });
  const timers = fakeTimers();
  const r = new ClassroomRecording(api, 7, status(), timers);
  const events = []; r.onTransition = (w) => events.push(w);
  await r.start();
  assert.equal(timers.ms, POLL_MS);
  await timers.fns[0]();
  assert.equal(r.view().indicator, 'recording');
  await timers.fns[0]();
  assert.equal(r.view().indicator, 'none');
  assert.deepEqual(events, ['started', 'stopped']);
});

test('a transient polling failure keeps the last known status; stop() clears the timer', async () => {
  const api = fakeApi({ 'GET /class-runs/7/recording': new Error('network') });
  const timers = fakeTimers();
  const r = new ClassroomRecording(api, 7, status({ is_recording: true, approval_status: 'APPROVED', recording_state: 'RECORDING' }), timers);
  await r.start();
  await r.refresh();
  assert.equal(r.view().indicator, 'recording');
  r.stop();
  assert.equal(timers.cleared, 1);
});

test('a failed recording start is only explained to the trainer', () => {
  const t = new ClassroomRecording(fakeApi(), 7, status({ can_control: true, approval_status: 'APPROVED', recording_state: 'FAILED' }), fakeTimers());
  assert.match(t.view().notice, /could not be started/);
  const s = new ClassroomRecording(fakeApi(), 7, status({ approval_status: 'APPROVED', recording_state: 'FAILED' }), fakeTimers());
  assert.equal(s.view().notice, '');
});

test('an unexpected decision value is refused before any request', async () => {
  const api = fakeApi();
  const r = new ClassroomRecording(api, 7, status({ can_control: true }), fakeTimers());
  assert.equal(await r.decide('start'), false);
  assert.equal(await r.decide('stop'), false);
  assert.equal(api.calls.length, 0);
});

/* ---- reliability: waiting / failure notices and Retry recording ---- */
const waiting = (over) => status(Object.assign({ can_control: true, approval_status: 'APPROVED', recording_state: 'STARTING',
  notice_code: 'waiting_for_media', notice: 'Waiting for your camera, microphone or screen share.', can_retry: false }, over));
const failedNoMedia = (over) => status(Object.assign({ can_control: true, approval_status: 'APPROVED', recording_state: 'FAILED',
  notice_code: 'no_media_published', notice: 'Recording could not start because your camera, microphone and screen share were all off. Turn one on, then choose Retry recording.',
  can_retry: true }, over));

test('while waiting for camera or mic the trainer sees why, and the indicator stays "starting"', () => {
  const v = new ClassroomRecording(fakeApi(), 7, waiting(), fakeTimers()).view();
  assert.equal(v.noticeCode, 'waiting_for_media');
  assert.match(v.notice, /camera, microphone or screen share/);
  assert.equal(v.indicator, 'starting');
  assert.equal(v.canRetry, false);
});

test('after a no-media failure the trainer gets the server explanation and a retry, not the generic text', () => {
  const v = new ClassroomRecording(fakeApi(), 7, failedNoMedia(), fakeTimers()).view();
  assert.match(v.notice, /all off/);
  assert.equal(v.canRetry, true);
  assert.equal(v.canDecide, false);   // the original decision is not re-opened; only a retry is offered
});

test('students never get a notice, a retry or a waiting state, even if the payload carried one', () => {
  const v = new ClassroomRecording(fakeApi(), 7, failedNoMedia({ can_control: false }), fakeTimers()).view();
  assert.deepEqual([v.notice, v.noticeCode, v.canRetry, v.canDecide], ['', '', false, false]);
  const w = new ClassroomRecording(fakeApi(), 7, waiting({ can_control: false }), fakeTimers()).view();
  assert.deepEqual([w.notice, w.noticeCode, w.indicator], ['', '', 'none']);
});

test('Retry recording opens the dialog, survives status polls, and posts the same consent-checked approval', async () => {
  const api = fakeApi({
    'GET /class-runs/7/recording': failedNoMedia(),
    'POST /class-runs/7/recording/approval': (body) => waiting({ consent_version: body.consent_version }),
  });
  const r = new ClassroomRecording(api, 7, failedNoMedia(), fakeTimers());
  r.openDialog();
  assert.equal(r.view().showDialog, true);
  await r.refresh();
  assert.equal(r.view().showDialog, true);          // an approved class must not close the retry dialog on the next poll
  assert.equal(await r.decide('approve'), true);
  assert.deepEqual(api.calls.at(-1), ['POST', '/class-runs/7/recording/approval', { decision: 'approve', consent_version: 'v1' }]);
  assert.equal(r.view().showDialog, false);
  assert.equal(r.view().canRetry, false);
});

test('a student can never open the retry dialog or send a retry', async () => {
  const api = fakeApi();
  const r = new ClassroomRecording(api, 7, failedNoMedia({ can_control: false }), fakeTimers());
  r.openDialog();
  assert.equal(r.view().showDialog, false);
  assert.equal(await r.decide('approve'), false);
  assert.equal(api.calls.length, 0);
});

test('a retry that the server refuses shows the server message and keeps the dialog open', async () => {
  const api = fakeApi({ 'POST /class-runs/7/recording/approval': () => { throw Object.assign(new Error('x'), { detail: 'The recording notice has changed.' }); } });
  const r = new ClassroomRecording(api, 7, failedNoMedia(), fakeTimers());
  r.openDialog();
  assert.equal(await r.decide('approve'), false);
  assert.equal(r.view().error, 'The recording notice has changed.');
  assert.equal(r.view().showDialog, true);
});

/* ---- static wiring ---- */
const root = path.join(__dirname, '..', '..', 'arckenites-orange');
const html = fs.readFileSync(path.join(root, 'classroom.html'), 'utf8');
const js = fs.readFileSync(path.join(root, 'js', 'classroom.js'), 'utf8');
const rec = fs.readFileSync(path.join(root, 'js', 'classroom-recording.js'), 'utf8');

test('recording UI ids used by classroom.js exist in classroom.html and the module loads before the page script', () => {
  const ids = [...js.matchAll(/\$\('(cls[A-Za-z]+)'\)/g)].map((m) => m[1]);
  const runtime = new Set(['clsJoinBtn', 'clsPrejoinError', 'clsChatReconnect']);
  assert.deepEqual(ids.filter((id) => !runtime.has(id) && !html.includes(`id="${id}"`)), []);
  assert.ok(html.indexOf('js/classroom-recording.js') > -1 && html.indexOf('js/classroom-recording.js') < html.indexOf('js/classroom.js'));
});

test('the recording module only uses the existing status, prompt and approval endpoints', () => {
  const urls = [...rec.matchAll(/`(\/class-runs\/[^`]+)`/g)].map((m) => m[1].replace('${this.runId}', '{id}'));
  assert.deepEqual([...new Set(urls)].sort(), ['/class-runs/{id}/recording', '/class-runs/{id}/recording/approval', '/class-runs/{id}/recording/prompt']);
  const code = (rec + js).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert.doesNotMatch(code, /recordings?\/(start|stop)|\/meetings\/|download|playback|method:\s*'(DELETE|PATCH|PUT)'|revoke/i);
});

test('the approval dialog text is set with textContent, not innerHTML', () => {
  assert.match(js, /clsConsentText'\)\.textContent = v\.consentText/);
  assert.doesNotMatch(js, /clsConsentText'\)\.innerHTML/);
});

test('the page script labels the retry, hides the decline button while retrying, and says each notice once', () => {
  assert.match(js, /Retry recording/);
  assert.match(js, /clsDeclineBtn'\)\.style\.display = v\.canRetry \? 'none'/);
  assert.match(js, /v\.notice !== lastNotice/);
  assert.match(js, /waiting_for_media/);
  const code = (rec + js).replace(/\/\*[\s\S]*?\*\//g, '');
  assert.doesNotMatch(code, /\.innerHTML\s*=\s*[^;]*(notice|detail)/i);
});

test('general meetings have no classroom code and no recording control of any kind', () => {
  const html2 = fs.readFileSync(path.join(root, 'meeting-room.html'), 'utf8');
  const js2 = fs.readFileSync(path.join(root, 'js', 'meeting-room.js'), 'utf8');
  assert.doesNotMatch(html2 + js2, /class-runs|classroom-recording/);
  assert.doesNotMatch(html2, /meetToolRecord|meetRecordingBadge|Start\/stop recording/);
  const code = js2.replace(/\/\*[\s\S]*?\*\//g, '');
  assert.doesNotMatch(code, /recordings\/start|\/recordings\/|toggleRecording|meetToolRecord/);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log('ok  -', name); } catch (e) { failed += 1; console.log('FAIL-', name, '\n   ', e.message); }
  }
  console.log(`\n${tests.length - failed}/${tests.length} passed`);
  process.exit(failed ? 1 : 0);
})();
