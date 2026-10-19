/* Run from repository root: node backend/tests/student_settings_navigation.test.cjs */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const site = path.resolve(__dirname, '../../arckenites-orange');
const html = fs.readFileSync(path.join(site, 'student-dashboard.html'), 'utf8');
const source = fs.readFileSync(path.join(site, 'js/student-settings-nav.js'), 'utf8');
const viewNames = [...html.matchAll(/data-settings-view="([^"]+)"/g)].map((match) => match[1]);
const detailNames = [...html.matchAll(/data-settings-detail="([^"]+)"/g)].map((match) => match[1]);
const categories = [...html.matchAll(/data-settings-category="([^"]+)"/g)].map((match) => match[1]);
assert.deepEqual(categories, ['profile', 'security', 'contact']);
assert.equal([...html.matchAll(/class="admin-sidebar-link" data-panel="settings"/g)].length, 1);
assert.ok(!html.includes('admin-sidebar-account'));
assert.ok(!html.includes('id="studentProfileTrigger"'));
assert.ok(!html.includes('id="studentProfilePanel"'));
assert.ok(html.includes('id="profilePhotoInput"'));
assert.ok(html.includes('id="studentAvatarWrap"'));
assert.equal(detailNames.length, 11);
assert.ok(html.includes('href="change-password.html?from=student-settings"'));
for (const detail of detailNames) assert.ok(viewNames.includes(`detail:${detail}`), detail);
assert.ok(viewNames.includes('contact'));
assert.ok(html.includes('data-settings-action="logout"'));

const views = viewNames.map((name) => ({dataset: {settingsView: name}, hidden: name !== 'menu'}));
const elements = new Map();
const element = (key) => {
  if (!elements.has(key)) elements.set(key, {
    hidden: true, textContent: '', dataset: {},
    focus() {},
    querySelector: () => ({textContent: ''}),
  });
  return elements.get(key);
};
const root = {
  dataset: {}, listeners: {}, scrollIntoView() {},
  querySelectorAll(selector) { assert.equal(selector, '[data-settings-view]'); return views; },
  querySelector: element,
  addEventListener(name, handler) { this.listeners[name] = handler; },
};
const base = 'https://example.test/student-dashboard.html';
const entries = [{url: base, state: null}];
let position = 0;
let popstate;
const location = {href: base, hash: ''};
const setEntry = () => {
  location.href = entries[position].url;
  location.hash = new URL(location.href).hash;
};
const history = {
  state: null,
  pushState(state, _title, url) {
    entries.splice(position + 1);
    entries.push({url: new URL(url, base).href, state});
    position += 1;
    this.state = state;
    setEntry();
  },
  back() {
    if (position === 0) return;
    position -= 1;
    this.state = entries[position].state;
    setEntry();
    popstate({state: this.state});
  },
  forward() {
    if (position + 1 >= entries.length) return;
    position += 1;
    this.state = entries[position].state;
    setEntry();
    popstate({state: this.state});
  },
};
const window = {location, history, addEventListener(name, fn) { if (name === 'popstate') popstate = fn; }};
const context = {window, document: {querySelector: () => root}, URL};
vm.runInNewContext(source, context);
const nav = vm.runInNewContext('StudentSettingsNav', context);
let visiblePanel = 'overview';
let loggedOut = false;
nav.init({
  showSettingsPanel: () => { visiblePanel = 'settings'; },
  getDashboardPanel: () => 'overview',
  restoreDashboardPanel: (name) => { visiblePanel = name; },
  logout: () => { loggedOut = true; },
});
const visible = () => views.filter((item) => !item.hidden).map((item) => item.dataset.settingsView);
const click = (selector, dataset = {}) => root.listeners.click({target: {
  closest(query) { return query === selector ? {dataset} : null; },
}});

nav.openMenu();
assert.equal(location.hash, '#settings');
assert.deepEqual(visible(), ['menu']);
assert.equal(visiblePanel, 'settings');
const menuHistoryLength = entries.length;
nav.openMenu();
assert.equal(entries.length, menuHistoryLength);
click('[data-settings-category]', {settingsCategory: 'profile'});
assert.deepEqual(visible(), ['profile']);
assert.equal(element('#settingsActiveCategory').textContent, 'Personal Profile');
for (const detail of detailNames.slice(0, 5)) {
  click('[data-settings-detail]', {settingsDetail: detail});
  assert.ok(visible().every((view) => view === `detail:${detail}`), detail);
  assert.ok(visible().length >= 1, detail);
  click('#settingsBackBtn');
  assert.deepEqual(visible(), ['profile']);
}
click('#settingsBackBtn');
assert.deepEqual(visible(), ['menu']);
history.back();
assert.equal(visiblePanel, 'overview');
history.forward();
assert.deepEqual(visible(), ['menu']);

click('[data-settings-category]', {settingsCategory: 'security'});
assert.deepEqual(visible(), ['security']);
assert.equal(element('#settingsActiveCategory').textContent, 'Privacy & Security');
for (const detail of detailNames.slice(5)) {
  click('[data-settings-detail]', {settingsDetail: detail});
  assert.deepEqual(visible(), [`detail:${detail}`]);
  click('#settingsBackBtn');
  assert.deepEqual(visible(), ['security']);
}
click('#settingsHomeBtn');
assert.deepEqual(visible(), ['menu']);
click('[data-settings-category]', {settingsCategory: 'contact'});
assert.deepEqual(visible(), ['contact']);
click('#settingsBackBtn');
assert.deepEqual(visible(), ['menu']);
nav.leaveToPanel('support');
assert.equal(location.hash, '');
assert.deepEqual(visible(), []);
history.back();
assert.deepEqual(visible(), ['menu']);
history.forward();
assert.deepEqual(visible(), []);
assert.equal(visiblePanel, 'support');
history.back();
click('[data-settings-action="logout"]');
assert.equal(loggedOut, true);
console.log('PASS: Single Settings entry, no Account accordion, Settings categories, all 11 detail items, password destination, Back navigation, browser history, and Logout.');
