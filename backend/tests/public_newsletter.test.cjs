const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, 'arckenites-orange/index.html'), 'utf8');
const script = fs.readFileSync(path.join(root, 'arckenites-orange/js/script.js'), 'utf8');
assert.match(html, /Newsletter sign-up is coming soon\./);
assert.doesNotMatch(html, /id="newsletterForm"/);
assert.doesNotMatch(script, /Thanks! You're subscribed/);
console.log('Public home page makes no unsupported newsletter subscription claim.');
