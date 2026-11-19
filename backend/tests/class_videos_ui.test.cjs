// Browser-side Class Videos helpers: link safety, escaping, and the player's markup.
// Run: node backend/tests/class_videos_ui.test.cjs
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(path.join(__dirname, '..', '..', 'arckenites-orange', 'js', 'class-videos.js'), 'utf8');

const created = [];
const listeners = {};
const makeEl = () => {
  const el = {
    className: '', innerHTML: '', style: {}, dataset: {}, classList: { add() {}, remove() {}, contains: () => false },
    _children: {}, addEventListener() {}, appendChild() {}, removeAttribute() {}, pause() {}, load() {}, play: () => Promise.resolve(), paused: true, currentTime: 0,
    querySelector(selector) { return (this._children[selector] ||= makeEl()); },
  };
  created.push(el);
  return el;
};
const sandbox = {
  document: { createElement: makeEl, addEventListener: (type, fn) => { listeners[type] = fn; }, body: { appendChild() {} } },
  window: { location: { href: 'https://arckenites.example/student-dashboard.html' } },
  API_BASE: 'https://arckenites.example/api',
  ArckAPI: { request: async () => ({ stream_url: '/api/recordings/x/stream?t=abc' }) },
  setInterval: () => 1, clearInterval() {}, URL,
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const V = vm.runInContext('ArckClassVideos', sandbox);

// Only web links may ever become an href.
for (const bad of ['javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'data:text/html,<script>', 'vbscript:x', 'file:///etc/passwd', '//evil.example', '', null, undefined, 42]) {
  assert.strictEqual(V.safeHref(bad), null, `must reject ${String(bad)}`);
}
assert.strictEqual(V.safeHref('https://youtu.be/abc'), 'https://youtu.be/abc');
assert.strictEqual(V.safeHref('  http://example.com/v  '), 'http://example.com/v');

// A stored unsafe link renders as an em dash, never as an anchor.
assert.strictEqual(V.linkCellHtml({ video_url: 'javascript:alert(1)' }), '—');
const link = V.linkCellHtml({ video_url: 'https://example.com/a?x="1"&y=<2>' });
assert.ok(link.includes('rel="noopener noreferrer"'));
assert.ok(!/<2>|"1"/.test(link), 'attribute values must be escaped');

// Recordings are recognised only when they carry a recording id; they never become links.
assert.strictEqual(V.isRecording({ source: 'RECORDING', recording_id: 'abc' }), true);
assert.strictEqual(V.isRecording({ source: 'RECORDING', recording_id: null }), false);
assert.strictEqual(V.isRecording({ source: 'LINK', video_url: 'https://x.example' }), false);

// The Watch button carries the id and escaped title, and no URL or storage path.
const button = V.watchButtonHtml({ recording_id: '11111111-2222-3333-4444-555555555555', title: '<img src=x onerror=alert(1)>' });
assert.ok(button.includes('data-watch-recording="11111111-2222-3333-4444-555555555555"'));
assert.ok(!button.includes('<img'), 'title must be escaped');
assert.ok(!/href=|\.mp4|storage|incoming/i.test(button));

// Durations.
assert.strictEqual(V.formatDuration(null), '—');
assert.strictEqual(V.formatDuration(45), '45s');
assert.strictEqual(V.formatDuration(125), '2m 05s');
assert.strictEqual(V.formatDuration(3 * 3600 + 7 * 60), '3h 07m');

// The player markup offers no download or share control.
(async () => {
  listeners.click({ target: { closest: () => ({ dataset: { watchRecording: 'abc', watchTitle: 'Lesson' } }) } });
  await new Promise((r) => setTimeout(r, 10));
  const modal = created.find((el) => el.innerHTML.includes('classVideoPlayer'));
  assert.ok(modal, 'the player modal is created on demand');
  assert.ok(/controlsList="nodownload/.test(modal.innerHTML));
  assert.ok(/disablePictureInPicture/.test(modal.innerHTML));
  assert.ok(!/<a [^>]*download|download=/.test(modal.innerHTML), 'no download link');
  assert.ok(!/share|copy link/i.test(modal.innerHTML));
  console.log('class_videos_ui.test.cjs: all assertions passed');
})().catch((err) => { console.error(err); process.exit(1); });
