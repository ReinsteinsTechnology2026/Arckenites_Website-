/* Run from repository root: node backend/tests/mfa_controls_ui.test.cjs
   Executes the real api.js and mfa-controls.js against a minimal simulated DOM
   and a fake fetch. No browser, backend or network is used. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const js = path.resolve(__dirname, '../../arckenites-orange/js');

function setup(responder) {
  const listeners = {};
  let registry = new Map();
  const make = (key) => {
    const el = {key, value: '', textContent: '', style: {}, disabled: false, isConnected: true,
      dataset: {}, listeners: {}, addEventListener(n, f) { this.listeners[n] = f; },
      closest(sel) { return this.matches && this.matches(sel) ? this : null; }};
    return el;
  };
  const lookup = (selector) => {
    if (!registry.has(selector)) {
      const el = make(selector);
      const action = /^\[data-mfa-action="(.+)"\]$/.exec(selector);
      if (action) { el.dataset.mfaAction = action[1]; el.textContent = action[1] === 'confirm' ? 'Confirm MFA setup' : action[1]; }
      el.id = selector.startsWith('#') ? selector.slice(1) : '';
      el.matches = (s) => s === '[data-mfa-action]' && !!el.dataset.mfaAction;
      el.querySelector = lookup;
      registry.set(selector, el);
    }
    return registry.get(selector);
  };
  const root = {
    addEventListener(name, fn) { listeners[name] = fn; },
    querySelector: lookup,
    set innerHTML(_) { registry = new Map(); },   // a re-render replaces every child
    get innerHTML() { return ''; },
    set textContent(v) { this.text = v; },
  };
  const store = new Map([['ak_token', 'test-token'], ['ak_user', JSON.stringify({admin_role: null})]]);
  const calls = [];
  const ctx = {
    console, setTimeout, clearTimeout, AbortController, TextEncoder, JSON,
    window: {location: {hostname: 'example.test', href: ''}, confirm: () => true},
    sessionStorage: {getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k)},
    document: {getElementById: (id) => (id === 'mfaControls' ? root : null),
      addEventListener: (name, fn) => { ctx.__ready = fn; }},
    fetch: async (url, options) => {
      calls.push({url, options});
      const r = await responder(url, options);
      if (r.network) throw new TypeError('Failed to fetch');
      return {ok: r.status >= 200 && r.status < 300, status: r.status,
        json: async () => { if (r.body === undefined) throw new SyntaxError('no body'); return r.body; }};
    },
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(js, 'api.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(js, 'mfa-controls.js'), 'utf8'), ctx);
  return {ctx, root, listeners, calls, lookup, el: (sel) => lookup(sel)};
}

const status = (enabled) => ({status: 200, body: {enabled, required_by_policy: false, recovery_codes_remaining: enabled ? 8 : 0}});
const BAD_TEXT = /\[object Object\]|undefined|Traceback|Fernet|secret|otpauth/i;

async function confirmWith(responder, code, {expectRequest = true} = {}) {
  const t = setup(async (url, options) => {
    if (url.endsWith('/auth/mfa/status')) return status(false);
    return responder(url, options);
  });
  await t.ctx.__ready();
  t.el('#mfaSetupCode').value = code;
  t.listeners.input({target: t.el('#mfaSetupCode')});
  const button = t.el('[data-mfa-action="confirm"]');
  const pending = t.listeners.click({target: button});
  if (expectRequest) {
    assert.equal(button.disabled, true, 'button disabled while the request runs');
    assert.notEqual(button.textContent, 'Confirm MFA setup', 'progress feedback shown');
  }
  await pending;
  const sent = t.calls.filter((c) => c.url.endsWith('/setup/confirm'));
  assert.equal(sent.length, expectRequest ? 1 : 0);
  return {t, button, text: t.el('#mfaMessage').textContent, sent};
}

(async () => {
  // Shared extractor: every response shape yields readable text, never an object.
  const {ctx} = setup(async () => status(false));
  const msg = (err, fb) => vm.runInContext('(e, f) => ArckAPI.errorMessage(e, f)', ctx)(err, fb);
  const cases = [
    [{detail: 'Invalid authenticator code.'}, 'Invalid authenticator code.'],
    [{detail: [{loc: ['body', 'code'], type: 'string_pattern_mismatch', msg: 'String should match pattern'}]},
      'Enter the six-digit code from your authenticator app.'],
    [{detail: [{loc: ['body', 'password'], msg: 'x'}]}, 'Enter your current password.'],
    [{detail: [{loc: ['body', 'other'], msg: 'technical'}]}, 'Please check the information you entered and try again.'],
    [{detail: []}, 'Please check the information you entered and try again.'],
    [{detail: {message: 'Readable message'}}, 'Readable message'],
    [{detail: {unexpected: true}}, 'FALLBACK'],
    [{detail: 'Something went wrong.'}, 'FALLBACK'],
    [{detail: ''}, 'FALLBACK'], [{}, 'FALLBACK'], [null, 'FALLBACK'], [undefined, 'FALLBACK'],
    [new Error('plain error with stack'), 'FALLBACK'],
    [{status: 0, detail: 'Cannot reach the Arckenites server. Check that the backend is running and try again.'},
      'Cannot reach the Arckenites server. Check that the backend is running and try again.'],
  ];
  for (const [err, expected] of cases) {
    const out = msg(err, 'FALLBACK');
    assert.equal(typeof out, 'string');
    assert.equal(out, expected);
    assert.doesNotMatch(out, /\[object Object\]/);
  }

  // Input is restricted to six digits; "123 456" is accepted as pasted by users.
  let r = await confirmWith(async () => ({status: 200, body: {enabled: true, recovery_codes: ['AAAAAAAA-BBBBBBBB-CCCCCCCC-DDDDDDDD']}}), '123 456');
  assert.equal(JSON.parse(r.sent[0].options.body).code, '123456');
  assert.match(r.sent[0].options.headers.Authorization, /^Bearer test-token$/);
  assert.equal(r.sent[0].options.method, 'POST');

  // Successful enrollment: status refreshed, recovery codes shown once, success message.
  const okTest = setup(async (url) => {
    if (url.endsWith('/auth/mfa/status')) return okTest.enabled ? status(true) : status(false);
    okTest.enabled = true;
    return {status: 200, body: {enabled: true, recovery_codes: ['AAAAAAAA-BBBBBBBB-CCCCCCCC-DDDDDDDD']}};
  });
  await okTest.ctx.__ready();
  okTest.el('#mfaSetupCode').value = '654321';
  await okTest.listeners.click({target: okTest.el('[data-mfa-action="confirm"]')});
  assert.equal(okTest.el('#mfaStatus').textContent, 'Enabled');
  assert.equal(okTest.el('#mfaRecoveryOutput').style.display, 'block');
  assert.match(okTest.el('#mfaRecoveryOutput').querySelector('pre').textContent, /AAAAAAAA-BBBBBBBB/);
  assert.match(okTest.el('#mfaMessage').textContent, /MFA is now enabled/);
  assert.equal(okTest.calls.filter((c) => c.url.endsWith('/auth/mfa/status')).length, 2, 'status refreshed');

  // Wrong-format entry never reaches the server and is explained.
  r = await confirmWith(async () => ({status: 500}), '12', {expectRequest: false});
  assert.equal(r.text, 'Enter the six-digit code from your authenticator app.');
  assert.equal(r.button.disabled, false);

  // Every failure renders readable text, re-enables the button, keeps the entered code.
  const failures = [
    ['invalid code (400)', {status: 400, body: {detail: 'Invalid authenticator code.'}}, /Invalid authenticator code/],
    ['expired setup (400)', {status: 400, body: {detail: 'MFA setup expired. Start again.'}}, /expired/],
    ['FastAPI validation array (422)', {status: 422, body: {detail: [{loc: ['body', 'code'], type: 'string_pattern_mismatch', msg: 'String should match pattern'}]}}, /six-digit code/],
    ['auth failure (401)', {status: 401, body: {detail: 'Not authenticated'}}, /Session expired|Not authenticated/],
    ['config 503', {status: 503, body: {detail: 'Multi-factor authentication is not available because the server is not configured correctly.'}}, /not configured correctly/],
    ['server error without JSON (500)', {status: 500}, /MFA setup could not be completed/],
    ['message field', {status: 400, body: {message: 'Readable server message'}}, /Readable server message/],
    ['network failure', {network: true}, /Cannot reach the Arckenites server/],
    ['empty error object', {status: 400, body: {}}, /MFA setup could not be completed/],
  ];
  for (const [name, response, expected] of failures) {
    const result = await confirmWith(async () => response, '123456');
    assert.match(result.text, expected, name);
    assert.doesNotMatch(result.text, BAD_TEXT, name);
    assert.equal(result.button.disabled, false, `${name}: button re-enabled`);
    assert.equal(result.button.textContent, 'Confirm MFA setup', `${name}: label restored`);
    assert.equal(result.t.el('#mfaStatus').textContent, 'Disabled', `${name}: MFA stays disabled`);
    assert.equal(result.t.el('#mfaRecoveryOutput').style.display, undefined, `${name}: no recovery codes shown`);
  }
  console.log('PASS: MFA confirmation errors are readable text, input is six digits, buttons recover, success refreshes status.');
})().catch((error) => { console.error(error); process.exit(1); });
