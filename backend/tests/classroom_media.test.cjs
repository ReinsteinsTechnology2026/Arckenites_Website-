/* Run from repository root: node backend/tests/classroom_media.test.cjs
 * Phase 2 milestone 1: classroom media controller (fake LiveKit room, no network)
 * plus static wiring checks for classroom.html / classroom.js. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { ClassroomMedia, parseRole, friendlyMediaError } = require('../../arckenites-orange/js/classroom-media.js');

const RoomEvent = Object.fromEntries(['ParticipantConnected', 'ParticipantDisconnected', 'TrackMuted', 'TrackUnmuted',
  'TrackPublished', 'TrackUnpublished', 'TrackSubscribed', 'TrackUnsubscribed', 'LocalTrackPublished',
  'LocalTrackUnpublished', 'ActiveSpeakersChanged', 'ParticipantMetadataChanged', 'Reconnecting', 'Reconnected',
  'Disconnected'].map((n) => [n, n]));
const lk = { RoomEvent, Track: { Source: { Microphone: 'microphone', Camera: 'camera', ScreenShare: 'screen_share' } } };

function participant(identity, name, role, tracks = {}) {
  return {
    identity, name, metadata: JSON.stringify({ role }), isSpeaking: false,
    getTrackPublication: (src) => (tracks[src] ? { track: { source: src }, isMuted: !!tracks[src].muted } : undefined),
  };
}
function fakeRoom() {
  const handlers = {};
  const local = Object.assign(participant('me', 'Student A', 'student'), {
    isMicrophoneEnabled: false, isCameraEnabled: false, isScreenShareEnabled: false, calls: [], fail: null,
    async setMicrophoneEnabled(on) { if (this.fail) throw this.fail; this.calls.push(['mic', on]); this.isMicrophoneEnabled = on; },
    async setCameraEnabled(on) { if (this.fail) throw this.fail; this.calls.push(['cam', on]); this.isCameraEnabled = on; },
    async setScreenShareEnabled(on) { if (this.fail) throw this.fail; this.calls.push(['screen', on]); this.isScreenShareEnabled = on; },
  });
  return {
    state: 'connected', localParticipant: local, remoteParticipants: new Map(), connectArgs: null, disconnected: false,
    on(evt, fn) { (handlers[evt] ||= []).push(fn); return this; },
    fire(evt) { (handlers[evt] || []).forEach((fn) => fn()); },
    async connect(url, token) { this.connectArgs = [url, token]; },
    disconnect() { this.disconnected = true; },
  };
}
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test('connect uses the server-issued url and token and marks connected', async () => {
  const room = fakeRoom(); const m = new ClassroomMedia(lk, { room });
  await m.connect('wss://local.test', 'tok');
  assert.deepEqual(room.connectArgs, ['wss://local.test', 'tok']);
  assert.equal(m.state().connected, true);
});

test('microphone, camera and screen share toggle through the local participant', async () => {
  const room = fakeRoom(); const m = new ClassroomMedia(lk, { room });
  await m.toggleMicrophone(); await m.toggleCamera(); await m.toggleScreenShare();
  let s = m.state();
  assert.deepEqual([s.micOn, s.cameraOn, s.screenOn], [true, true, true]);
  await m.toggleMicrophone(); await m.toggleCamera(); await m.toggleScreenShare();
  s = m.state();
  assert.deepEqual([s.micOn, s.cameraOn, s.screenOn], [false, false, false]);
  assert.deepEqual(room.localParticipant.calls.map((c) => c.join(':')),
    ['mic:true', 'cam:true', 'screen:true', 'mic:false', 'cam:false', 'screen:false']);
});

test('a blocked device produces a friendly error, no throw, and unchanged state', async () => {
  const room = fakeRoom(); const m = new ClassroomMedia(lk, { room });
  room.localParticipant.fail = Object.assign(new Error('denied'), { name: 'NotAllowedError' });
  const ok = await m.toggleCamera();
  assert.equal(ok, false);
  assert.match(m.state().error, /blocked the camera/);
  assert.equal(m.state().cameraOn, false);
  room.localParticipant.fail = null;
  assert.equal(await m.toggleCamera(), true);
  assert.equal(m.state().error, '');
});

test('closing the screen-share picker is not reported as an error', () => {
  assert.equal(friendlyMediaError(Object.assign(new Error('x'), { name: 'AbortError' }), 'screen sharing'), '');
  assert.match(friendlyMediaError(Object.assign(new Error('x'), { name: 'NotFoundError' }), 'the microphone'), /found/);
});

test('participants include local first, remote state, roles and speaking', () => {
  const room = fakeRoom(); const m = new ClassroomMedia(lk, { room });
  const trainer = participant('t1', 'Trainer T', 'trainer', { camera: {}, microphone: {}, screen_share: {} });
  trainer.isSpeaking = true;
  const muted = participant('s2', 'Student B', 'student', { microphone: { muted: true } });
  room.remoteParticipants.set('t1', trainer); room.remoteParticipants.set('s2', muted);
  const s = m.state();
  assert.deepEqual(s.participants.map((p) => p.identity), ['me', 't1', 's2']);
  assert.equal(s.participants[0].isLocal, true);
  assert.deepEqual([s.participants[1].role, s.participants[1].isSpeaking, s.participants[1].cameraOn, s.participants[1].screenOn], ['trainer', true, true, true]);
  assert.equal(s.participants[2].micOn, false, 'a muted track is not "on"');
  assert.equal(s.screenSharer, 't1');
});

test('a participant cannot claim trainer through malformed or hostile metadata', () => {
  assert.equal(parseRole('not json'), 'student');
  assert.equal(parseRole(undefined), 'student');
  assert.equal(parseRole('{"role":"admin"}'), 'student');
  assert.equal(parseRole('{"role":"trainer"}'), 'trainer');
});

test('room events notify subscribers; unsubscribe stops them; a throwing listener is isolated', async () => {
  const room = fakeRoom(); const m = new ClassroomMedia(lk, { room });
  await m.connect('wss://x', 't');
  let n = 0; const off = m.subscribe(() => { n += 1; });
  m.subscribe(() => { throw new Error('bad listener'); });
  room.fire('ParticipantConnected'); room.fire('TrackMuted');
  assert.equal(n, 2);
  off(); room.fire('ParticipantDisconnected');
  assert.equal(n, 2);
  room.fire('Disconnected');
  assert.equal(m.state().connected, false);
});

test('trackFor returns live tracks only and never a muted one', () => {
  const room = fakeRoom(); const m = new ClassroomMedia(lk, { room });
  room.remoteParticipants.set('t1', participant('t1', 'T', 'trainer', { camera: {}, microphone: { muted: true } }));
  assert.equal(m.trackFor('t1', 'camera').source, 'camera');
  assert.equal(m.trackFor('t1', 'microphone'), null);
  assert.equal(m.trackFor('ghost', 'camera'), null);
});

test('disconnect leaves the room', () => {
  const room = fakeRoom(); const m = new ClassroomMedia(lk, { room });
  m.disconnect();
  assert.equal(room.disconnected, true);
  assert.equal(m.state().connected, false);
});

/* ---- static wiring: page, script and styles agree, and no security regressions ---- */
const root = path.join(__dirname, '..', '..', 'arckenites-orange');
const html = fs.readFileSync(path.join(root, 'classroom.html'), 'utf8');
const js = fs.readFileSync(path.join(root, 'js', 'classroom.js'), 'utf8');

test('every element id used by classroom.js exists in classroom.html', () => {
  const ids = new Set([...js.matchAll(/\$\('([A-Za-z]+)'\)/g)].map((m) => m[1]));
  const created = new Set(['clsJoinBtn', 'clsPrejoinError', 'clsChatReconnect']); // rendered at runtime
  const missing = [...ids].filter((id) => !created.has(id) && !html.includes(`id="${id}"`));
  assert.deepEqual(missing, []);
});

test('the page pins the LiveKit client version and loads scripts in dependency order', () => {
  assert.match(html, /vendor\/livekit-client-\d+\.\d+\.\d+\.umd\.js" integrity="sha384-[A-Za-z0-9+\/=]+" crossorigin/); // self-hosted + SRI, no CDN
  assert.doesNotMatch(html, /https?:\/\/[^"']*livekit-client/);
  const order = ['livekit-client', 'js/api.js', 'js/auth.js', 'js/classroom-media.js', 'js/classroom.js'].map((s) => html.indexOf(s));
  assert.ok(order.every((v, i) => v > -1 && (i === 0 || v > order[i - 1])), `bad script order ${order}`);
  assert.ok(fs.existsSync(path.join(root, 'css', 'classroom.css')));
});

test('classroom page uses the batch-class join endpoints only and has no recording or meeting controls', () => {
  assert.match(js, /\/class-runs\/batch\//);
  assert.match(js, /\/class-runs\//);
  assert.doesNotMatch(js, /\/meetings\//, 'general meetings stay on meeting-room.html');
  assert.doesNotMatch(js, /recordings?\/(start|stop)|downloadUrl|playback/i);
  // Milestone 2 adds a status badge and the trainer's approve/decline dialog, but never start/stop/download/play/delete.
  assert.doesNotMatch(html, /id="[^"]*(Start|Stop|Download|Play|Delete|Revoke)[A-Za-z]*Record|id="[^"]*Record[A-Za-z]*(Start|Stop|Download|Play|Delete|Revoke)/);
});

test('participant names are escaped before being placed in HTML', () => {
  // textContent assignments are safe; anything else interpolating user text must go through clsEscape.
  const raw = js.split('\n').filter((l) => /\$\{(p\.name|user\.full_name|message)\}/.test(l) && !/textContent|note\(/.test(l));   // textContent and the toast helper (which uses textContent) are safe
  assert.deepEqual(raw, [], 'unescaped interpolation of user-controlled text into HTML');
  assert.match(js, /clsEscape\(p\.name\)/);
});

test('the general-meeting page uses LiveKit only as a media layer: no class-run code and no recording control', () => {
  const mr = fs.readFileSync(path.join(root, 'meeting-room.html'), 'utf8') + fs.readFileSync(path.join(root, 'js', 'meeting-room.js'), 'utf8');
  assert.match(mr, /vendor\/livekit-client-\d+\.\d+\.\d+\.umd\.js/);
  assert.doesNotMatch(mr, /class-runs|classroom-recording|classroom-notes|classroom-chat/);
  assert.doesNotMatch(mr, /meetToolRecord|meetRecordingBadge|recordings\/start/);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log('ok  -', name); } catch (e) { failed += 1; console.log('FAIL-', name, '\n   ', e.message); }
  }
  console.log(`\n${tests.length - failed}/${tests.length} passed`);
  process.exit(failed ? 1 : 0);
})();
