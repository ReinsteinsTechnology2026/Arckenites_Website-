// Inauguration feedback page: client validation, payload, and static page contract.
// Run: node backend/tests/inauguration_feedback_ui.test.cjs
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const site = path.join(__dirname, '..', '..', 'arckenites-orange');
const html = fs.readFileSync(path.join(site, 'inauguration-feedback.html'), 'utf8');
const { validate, buildPayload, normalizeIndianMobile } = require(path.join(site, 'js', 'inauguration-feedback.js'));

const base = {
  name: 'Asha', domain: 'IT', domain_other: '', overall_session: 'Good', usefulness: 'Useful',
  learned_something: 'Yes', liked_topic: 'AI', liked_topic_other: '', attend_future: 'Maybe',
  suggestion: '', contact_preference: 'no', contact_number: '',
};

// Required fields
assert.deepStrictEqual(validate(base), {});
const empty = validate({});
['name', 'domain', 'overall_session', 'usefulness', 'learned_something', 'liked_topic', 'attend_future', 'contact_preference']
  .forEach((k) => assert.ok(empty[k], `${k} should be required`));
assert.ok(validate({ ...base, name: '   ' }).name);

// Conditional: Others domain
assert.ok(validate({ ...base, domain: 'Others' }).domain_other);
assert.deepStrictEqual(validate({ ...base, domain: 'Others', domain_other: 'Data' }), {});
// Conditional: Other topic is optional
assert.deepStrictEqual(validate({ ...base, liked_topic: 'Other' }), {});

// Conditional: phone
assert.deepStrictEqual(validate({ ...base, contact_preference: 'no', contact_number: 'garbage' }), {});
['yes', 'maybe'].forEach((pref) => {
  assert.ok(validate({ ...base, contact_preference: pref }).contact_number);
  assert.ok(validate({ ...base, contact_preference: pref, contact_number: '12345' }).contact_number);
  assert.deepStrictEqual(validate({ ...base, contact_preference: pref, contact_number: '+91 98765 43210' }), {});
});
assert.strictEqual(normalizeIndianMobile('098765-43210'), '9876543210');
assert.strictEqual(normalizeIndianMobile('5876543210'), null);

// Payload
const p = buildPayload({ ...base, domain: 'IT', domain_other: 'stale', contact_preference: 'yes', contact_number: '+91 98765 43210' });
assert.strictEqual(p.domain_other, null);
assert.strictEqual(p.contact_number, '9876543210');
assert.strictEqual(buildPayload(base).contact_number, null);

// Page contract
assert.match(html, /Inauguration Session — Feedback &amp; Reviews/);
assert.match(html, /Thank you for being part of our journey! Your feedback helps us improve/);
assert.match(html, /Thank you for your valuable feedback!/);
const link = html.match(/<a [^>]*id="reviewLink"[^>]*>/)[0];
assert.match(link, /href="https:\/\/g\.page\/r\/CVsd_O-2e-jSEBM\/review"/);
assert.match(link, /target="_blank"/);
assert.match(link, /rel="noopener noreferrer"/);
assert.ok(html.indexOf('id="feedbackForm"') < html.indexOf('Share Your Experience with Arckenites'), 'review section must follow the form');
assert.ok(html.indexOf('Share Your Experience with Arckenites') < html.indexOf('<footer'), 'footer comes last');
assert.match(html, /href="index\.html"/);
assert.match(fs.readFileSync(path.join(site, 'js', 'inauguration-feedback.js'), 'utf8'), /getFullYear/);

console.log('Inauguration feedback UI checks passed.');
