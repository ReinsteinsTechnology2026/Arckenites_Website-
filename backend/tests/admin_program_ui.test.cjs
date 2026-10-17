/* Run from repository root: node backend/tests/admin_program_ui.test.cjs
   Simulated DOM regression tests; no browser, real API or network used. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const choices = [
  {id: 101, name: 'Institutional Program', slug: 'institutional'},
  {id: 201, name: 'Freshers Job Hack Program', slug: 'real_freshers_slug'},
  {id: 301, name: 'Certificational Program', slug: 'real_certificate_slug'},
];
const legacyProgram = {id: 507, name: 'Corporate Training Program', slug: 'corporate_training'};

class Option {
  constructor(text, value) { this.textContent = text; this.value = String(value); }
}

function environment(script, {batch = false, catalogFailure = false} = {}) {
  const elements = new Map();
  class Element {
    constructor(id = '') {
      this.id = id;
      this.rawValue = '';
      this.isSelect = ['newStudentProgram', 'editStudentProgram', 'editBatchProgram'].includes(id);
      this.options = this.isSelect ? [new Option('Select a program', '')] : [];
      this.style = {display: 'none'};
      this.listeners = {};
      this.validity = {valid: true};
      this.classList = {add() {}, remove() {}, toggle() {return false;}};
      this.innerHTML = '';
      this.textContent = '';
    }
    set value(value) {
      const text = String(value);
      this.rawValue = !this.isSelect || this.options.some(o => o.value === text) ? text : '';
    }
    get value() { return this.rawValue; }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    appendChild(option) { this.options.push(option); }
    replaceChildren(...options) { this.options = options; this.rawValue = ''; }
    reset() {} focus() {} scrollIntoView() {} setAttribute() {}
  }
  const get = id => {
    if (!elements.has(id)) elements.set(id, new Element(id));
    return elements.get(id);
  };
  let boot;
  const calls = [];
  const student = {id: 42, full_name: 'Existing Student', username: 'legacy.student', email: 'legacy@example.com',
    program: 'corporate_training', program_id: 507, program_name: legacyProgram.name,
    is_active: true, must_change_password: false, created_at: '2026-10-17T00:00:00Z'};
  const batchRecord = {id: 9, name: 'Existing Batch', program: legacyProgram, trainer: null, students: [],
    batch_type: 'offline', status: 'active', student_count: 0};
  const api = {
    avatarHtml() { return ''; },
    async approvedProgramOptions() {
      if (catalogFailure) throw new Error('Catalog unavailable');
      return {options: choices, detail: null};
    },
    async request(url, options = {}) {
      calls.push({url, ...options});
      if (url === '/admin/students' && !options.method) return [student];
      if (url === '/admin/students/42' && options.method === 'PATCH') return {...student, ...options.body};
      if (url === '/admin/staff') return [];
      if (url === '/admin/batches/9' && !options.method) return batchRecord;
      if (url === '/admin/batches/9' && options.method === 'PATCH') return batchRecord;
      if (/\/admin\/batches\/9\/(sessions|videos|materials)$/.test(url)) return [];
      throw new Error('Unexpected request: ' + url);
    },
  };
  const location = {search: batch ? '?id=9' : ''};
  const context = {
    document: {getElementById: get, querySelectorAll() {return [];}, createElement() {return new Element();},
      addEventListener(name, fn) {if (name === 'DOMContentLoaded') boot = fn;}, body: {classList: {toggle() {}}}},
    window: {location, confirm() {return true;}, alert() {}}, location,
    ArckAPI: api, ArckAuth: {async requireRole() {return {full_name: 'Admin', role: 'admin'};}, hasPermission() {return true;}},
    Option, URLSearchParams, TextEncoder, Date, console,
  };
  vm.runInNewContext(fs.readFileSync(path.join(root, script), 'utf8'), context);
  return {get, calls, boot: () => boot()};
}

const submit = {preventDefault() {}};

async function studentHistoryTest() {
  const app = environment('arckenites-orange/js/admin-students.js');
  await app.boot();
  assert.deepEqual(app.get('newStudentProgram').options.map(o => o.value), ['', '101', '201', '301']);
  await app.get('studentsTableBody').listeners.click({target: {closest() {return {dataset: {id: '42', action: 'edit'}};}}});
  const select = app.get('editStudentProgram');
  assert.deepEqual(select.options.map(o => o.value), ['', '101', '201', '301']);
  assert.match(select.options[0].textContent, /Keep current program: Corporate Training Program/);
  await app.get('editStudentForm').listeners.submit(submit);
  const unchanged = app.calls.at(-1).body;
  assert.ok(!('program_id' in unchanged));
  assert.ok(!('program' in unchanged));
  select.value = '201';
  await app.get('editStudentForm').listeners.submit(submit);
  assert.equal(app.calls.at(-1).body.program_id, 201);
}

async function batchHistoryTest(catalogFailure) {
  const app = environment('arckenites-orange/js/admin-batch-detail.js', {batch: true, catalogFailure});
  await app.boot();
  app.get('editBatchBtn').listeners.click();
  const select = app.get('editBatchProgram');
  assert.ok(!select.options.some(o => o.value === '507'));
  assert.match(select.options[0].textContent, /Keep current program: Corporate Training Program/);
  await app.get('editBatchForm').listeners.submit(submit);
  const unchanged = app.calls.at(-1).body;
  assert.ok(!('program_id' in unchanged));
  assert.ok(!('clear_program' in unchanged));
  if (!catalogFailure) {
    select.value = '201';
    await app.get('editBatchForm').listeners.submit(submit);
    assert.equal(app.calls.at(-1).body.program_id, 201);
    assert.ok(!('clear_program' in app.calls.at(-1).body));
  } else {
    assert.match(app.get('batchProgramCatalogNotice').textContent, /Existing batch assignments will be kept/);
  }
}

(async () => {
  await studentHistoryTest();
  await batchHistoryTest(false);
  await batchHistoryTest(true);
  console.log('PASS: student and batch selectors allow approved IDs only; legacy mappings survive edits and catalog failure.');
})().catch(error => {console.error(error); process.exitCode = 1;});
