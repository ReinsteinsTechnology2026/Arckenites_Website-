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

// Submission id: a UUID v4, stable per attempt series, present in the payload
const { newSubmissionId } = require(path.join(site, 'js', 'inauguration-feedback.js'));
const UUID4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
assert.match(newSubmissionId(), UUID4);
assert.notStrictEqual(newSubmissionId(), newSubmissionId());
assert.match(newSubmissionId({ getRandomValues(b) { b.fill(255); return b; } }), UUID4, 'getRandomValues fallback');
assert.match(newSubmissionId({}), UUID4, 'Math.random fallback');
assert.strictEqual(newSubmissionId({ randomUUID: () => 'fixed' }), 'fixed');
assert.strictEqual(buildPayload({ ...base, submission_id: 'abc' }).submission_id, 'abc');

// The browser never holds the Sheets secret, the Apps Script URL or the spreadsheet id
const jsSource = fs.readFileSync(path.join(site, 'js', 'inauguration-feedback.js'), 'utf8');
['SHARED_SECRET', 'shared_secret', 'script.google.com', 'docs.google.com', '1lNLPpvMJx'].forEach((word) => {
  assert.ok(!jsSource.includes(word) && !html.includes(word), `${word} must not appear in the page`);
});
// Success is only shown from the resolved request; failures keep the form and re-enable the button
assert.match(jsSource, /\.then\(function \(\) \{[\s\S]*?thanksCard[\s\S]*?\}\)\s*\.catch\(function \(err\) \{[\s\S]*?formError[\s\S]*?setBusy\(false\)/);

// Page contract
// Form wording: exact title, introduction, ten questions and closing message
assert.match(html, /<h1>Arckenites IT Academy – Session Feedback Form<\/h1>/);
assert.match(html, /<title>Arckenites IT Academy – Session Feedback Form/);
assert.match(html, /Thank you for participating in our session\. Your feedback is valuable to us and will help us improve our future training programmes and learning experiences\./);
const questions = [
  '1. Please enter your full name.',
  '2. Which domain are you currently interested in?',
  '3. How would you rate the overall session?',
  '4. How relevant and useful was the session to your learning goals?',
  '5. Did the session help you gain new knowledge or insights?',
  '6. Which topic or area of the session did you find most valuable?',
  '7. How interested are you in attending our upcoming sessions and workshops?',
  '8. What suggestions or improvements would you recommend for our future sessions?',
  '9. Would you like to receive information about our courses and training programmes?',
  '10. Please provide your contact number if you would like to receive course-related information.',
];
let at = -1;
questions.forEach((q) => { const i = html.indexOf(q); assert.ok(i > at, 'question missing or out of order: ' + q); at = i; });
assert.match(html, /<h2>Thank you for sharing your feedback\.<\/h2>\s*<p>We appreciate your time and support\.<\/p>/);
const pageBody = html.split('<body>')[1];
['Inauguration Session', 'One Suggestion', 'Your response has been recorded'].forEach((gone) => assert.ok(!pageBody.includes(gone), gone + ' should be gone from the visible page'));

// Displayed label -> the value sent to the (unchanged) API / sheet. Only the label changed, never the value.
const radios = {};
for (const m of html.matchAll(/<input type="radio" name="([a-z_]+)" value="([^"]+)"><span>([^<]+)<\/span>/g)) (radios[m[1]] = radios[m[1]] || []).push([m[3], m[2]]);
assert.deepStrictEqual(radios, {
  domain: [['IT', 'IT'], ['Cloud Computing', 'Cloud'], ['Networking', 'Networking'], ['Cybersecurity', 'Cybersecurity'], ['Other', 'Others']],
  overall_session: [['Excellent', 'Excellent'], ['Very Good', 'Very Good'], ['Good', 'Good'], ['Average', 'Average']],
  usefulness: [['Very Useful', 'Very Useful'], ['Useful', 'Useful'], ['Moderately Useful', 'Average'], ['Not Useful', 'Not Useful']],
  learned_something: [['Yes', 'Yes'], ['No', 'No']],
  liked_topic: [['Artificial Intelligence', 'AI'], ['Cloud Computing', 'Cloud'], ['Cybersecurity', 'Cybersecurity'], ['Networking', 'Networking'], ['Other', 'Other']],
  attend_future: [['Very Interested', 'Yes'], ['Somewhat Interested', 'Maybe'], ['Not Interested', 'No']],
  contact_preference: [['Yes', 'yes'], ['Maybe', 'maybe'], ['No', 'no']],
});
// Structure and conditional fields are unchanged
['domainOtherWrap', 'topicOtherWrap', 'contactWrap', 'fbPhone', 'fbSuggestion', 'fbDomainOther', 'fbTopicOther'].forEach((id) => assert.ok(html.includes('id="' + id + '"'), id));
assert.match(html, /<label for="fbDomainOther">Please specify your domain/);
assert.match(html, /<label for="fbTopicOther">Which topic\? \(optional\)/);
assert.match(html, /8\. What suggestions[^<]*<span class="fb-optional">\(optional\)<\/span>/);
const link = html.match(/<a [^>]*id="reviewLink"[^>]*>/)[0];
assert.match(link, /href="https:\/\/g\.page\/r\/CVsd_O-2e-jSEBM\/review"/);
assert.match(link, /target="_blank"/);
assert.match(link, /rel="noopener noreferrer"/);
assert.ok(html.indexOf('id="feedbackForm"') < html.indexOf('Share Your Experience with Arckenites'), 'review section must follow the form');
assert.ok(html.indexOf('Share Your Experience with Arckenites') < html.indexOf('<footer'), 'footer comes last');
assert.match(html, /href="index\.html"/);
assert.match(fs.readFileSync(path.join(site, 'js', 'inauguration-feedback.js'), 'utf8'), /getFullYear/);

console.log('Inauguration feedback UI checks passed.');
