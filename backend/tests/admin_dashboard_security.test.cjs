/* Run from repository root: node backend/tests/admin_dashboard_security.test.cjs */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    const node = {
      innerHTML: '', textContent: '', style: {},
      classList: { add() {}, remove() {}, toggle() { return false; }, contains() { return false; } },
      addEventListener() {}, setAttribute() {},
      closest() { return node; }, querySelectorAll() { return []; },
    };
    elements.set(id, node);
  }
  return elements.get(id);
}

let ready;
const hostile = '<img src=x onerror="alert(1)">';
const document = {
  addEventListener(name, callback) { if (name === 'DOMContentLoaded') ready = callback; },
  getElementById: element,
  querySelectorAll() { return []; },
};
const context = {
  document,
  ArckAuth: {
    async requireRole() { return { full_name: 'Admin User', role: 'admin' }; },
    hasPermission() { return true; }, logout() {},
  },
  ArckAPI: {
    async request(url) {
      if (url === '/dashboard/stats') return {
        total_users: 1, users_by_role: [], active_users: 1, logins_today: 0,
        failed_logins_today: 1, locked_accounts: 0,
      };
      if (url.startsWith('/dashboard/activity')) return { items: [{
        actor_name: hostile, message: `Failed login for ${hostile}`,
        event_type: 'login_failure', created_at: '2026-10-17T00:00:00Z',
      }] };
      if (url === '/admin/system/health') return {
        database: hostile, authentication: 'healthy', api: 'healthy',
      };
      throw new Error(`Unexpected API call: ${url}`);
    },
  },
};
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,
  '../../arckenites-orange/js/admin-dashboard.js'), 'utf8'), context);

(async () => {
  await ready();
  for (const id of ['adminActivityBody', 'adminNotifList', 'dashboardSystemHealth']) {
    const html = element(id).innerHTML;
    assert.ok(html.includes('&lt;img'), `${id} should display escaped input`);
    assert.ok(!html.includes(hostile), `${id} must not contain executable markup`);
  }
  assert.match(element('adminActivityBody').innerHTML, /title="Failed login for &lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;"/);
  console.log('Admin dashboard escapes untrusted activity, notification, and health text.');
})().catch((error) => { console.error(error); process.exitCode = 1; });
