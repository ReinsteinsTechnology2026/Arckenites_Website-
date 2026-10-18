const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const context = {
  document: {addEventListener() {}},
  URLSearchParams,
};
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../../inauguration/js/app.js'), 'utf8'), context);
const endpoint = context.registrationEndpoint;
const configured = 'https://script.google.com/macros/s/approved/exec';
const hostile = '?endpoint=https%3A%2F%2Fexample.invalid%2Fcollect';
assert.equal(endpoint({hostname: 'arckenites.com', search: hostile}, configured), configured);
assert.equal(endpoint({hostname: 'www.arckenites.com', search: hostile}, configured), configured);
assert.equal(endpoint({hostname: '127.0.0.1', search: hostile}, configured), 'https://example.invalid/collect');
assert.equal(endpoint({hostname: 'localhost', search: ''}, configured), configured);
console.log('Public inauguration URLs ignore endpoint overrides; local testing still works.');
