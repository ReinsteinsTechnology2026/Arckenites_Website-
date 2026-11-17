/* Run from repository root: node backend/tests/portal_files_readonly_controls.test.cjs
   Real-browser regression test (headless Chrome/Edge, no npm packages).
   Loads the real css/style.css and js/portal-files.js with a stubbed API and
   asserts that read-only viewers do not SEE upload controls or "Load more"
   (the `hidden` attribute used to be overridden by display rules). */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const site = path.resolve(__dirname, '../../arckenites-orange');
const candidates = [process.env.CHROME_BIN,
  'C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium', '/usr/bin/chromium-browser'].filter(Boolean);
const browser = candidates.find((p) => fs.existsSync(p));
if (!browser) {
  // CI must never pass silently without exercising a real browser.
  if (process.env.CI) { console.error('FAIL: no Chrome/Edge found in CI (set CHROME_BIN)'); process.exit(1); }
  console.log('SKIP: no Chrome/Edge found (set CHROME_BIN)'); process.exit(0);
}

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'portal-files-test-'));
const page = path.join(tmp, 'fixture.html');
const fileUrl = (p) => 'file:///' + p.split(path.sep).join('/');
fs.writeFileSync(page, `<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="${fileUrl(path.join(site, 'css/style.css'))}"></head><body>
<div id="viewer"></div><div id="writer"></div><div id="paged"></div><pre id="result"></pre>
<script>
const files = [{ id: 'f1', file_name: 'a.pdf', size_bytes: 10, can_delete: false }];
window.API_BASE = '/api';
window.ArckAPI = { getToken: () => 't', request: async (p) => {
  if (p.includes('/capabilities')) return { uploads_enabled: true, max_bytes: 1, video_max_bytes: 1, extensions: ['.pdf'] };
  if (p.includes('/materials/1')) return { files, next_offset: null, can_upload: false };
  if (p.includes('/materials/2')) return { files, next_offset: null, can_upload: true };
  return { files, next_offset: 100, can_upload: false };
} };
</script>
<script src="${fileUrl(path.join(site, 'js/portal-files.js'))}"></script>
<script>
(async () => {
  const roots = [PortalFiles.mount(viewer, 'materials', 1, 'Viewer'), PortalFiles.mount(writer, 'materials', 2, 'Writer'), PortalFiles.mount(paged, 'materials', 3, 'Paged')];
  roots.forEach((r) => { r.open = true; });
  await new Promise((r) => setTimeout(r, 800));
  const info = (r) => ({
    label: getComputedStyle(r.querySelector('label')).display,
    upload: getComputedStyle(r.querySelector('.portal-file-controls .btn-accent')).display,
    more: getComputedStyle([...r.querySelectorAll('button')].find((b) => b.textContent === 'Load more files')).display,
    refresh: getComputedStyle(r.querySelector('.portal-file-controls .btn')).display,
    files: r.querySelectorAll('.portal-file-list li').length });
  result.textContent = JSON.stringify({ viewer: info(roots[0]), writer: info(roots[1]), paged: info(roots[2]) });
})();
</script></body></html>`);

const run = spawnSync(browser, ['--headless=new', '--disable-gpu', '--no-sandbox', '--allow-file-access-from-files',
  '--virtual-time-budget=4000', '--user-data-dir=' + path.join(tmp, 'profile'), '--dump-dom', fileUrl(page)],
  { encoding: 'utf8', timeout: 60000 });
fs.rmSync(tmp, { recursive: true, force: true });
const match = /<pre id="result">([^<]*)<\/pre>/.exec(run.stdout || '');
assert.ok(match, 'fixture did not produce a result; browser exit ' + run.status);
const r = JSON.parse(match[1].replace(/&quot;/g, '"'));

assert.equal(r.viewer.files, 1, 'viewer should still see the shared file');
assert.equal(r.viewer.label, 'none', 'read-only viewer must not see the file chooser');
assert.equal(r.viewer.upload, 'none', 'read-only viewer must not see the Upload file button');
assert.equal(r.viewer.more, 'none', 'Load more must be hidden when there is no next page');
assert.notEqual(r.viewer.refresh, 'none', 'Refresh stays visible');
assert.notEqual(r.writer.label, 'none', 'uploader must still see the file chooser');
assert.notEqual(r.writer.upload, 'none', 'uploader must still see the Upload file button');
assert.notEqual(r.paged.more, 'none', 'Load more must show when another page exists');
console.log('portal files read-only controls browser test passed');
