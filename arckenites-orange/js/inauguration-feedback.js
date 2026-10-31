// Inauguration feedback page: validation, conditional fields, and the real
// POST to /api/feedback/inauguration. Success is only shown on a 2xx response, which the
// server sends only after Google Sheets confirms the row (the sheet is the only store).
// Pure helpers are exported for backend/tests/inauguration_feedback_ui.test.cjs.
(function () {
  'use strict';

  var REQUIRED_CHOICES = {
    domain: 'Select your domain.',
    overall_session: 'Rate the overall session.',
    usefulness: 'Tell us how useful the session was.',
    learned_something: 'Tell us whether you learned something new.',
    liked_topic: 'Select the topic you liked most.',
    attend_future: 'Tell us whether you would attend future sessions.',
    contact_preference: 'Choose a contact option.'
  };

  // Indian mobile: optional +91 / 91 / 0 prefix, then 10 digits starting 6-9.
  function normalizeIndianMobile(raw) {
    var digits = String(raw || '').replace(/[\s\-().]/g, '');
    var prefixes = ['+91', '91', '0'];
    for (var i = 0; i < prefixes.length; i++) {
      if (digits.indexOf(prefixes[i]) === 0 && digits.length > 10) {
        digits = digits.slice(prefixes[i].length);
        break;
      }
    }
    return /^[6-9]\d{9}$/.test(digits) ? digits : null;
  }

  function validate(v) {
    var errors = {};
    if (!String(v.name || '').trim()) errors.name = 'Enter your name.';
    Object.keys(REQUIRED_CHOICES).forEach(function (key) {
      if (!v[key]) errors[key] = REQUIRED_CHOICES[key];
    });
    if (v.domain === 'Others' && !String(v.domain_other || '').trim()) {
      errors.domain_other = 'Please specify your domain.';
    }
    if (v.contact_preference === 'yes' || v.contact_preference === 'maybe') {
      var phone = String(v.contact_number || '').trim();
      if (!phone) errors.contact_number = 'Enter a contact number so we can reach you.';
      else if (!normalizeIndianMobile(phone)) errors.contact_number = 'Enter a valid 10-digit Indian mobile number.';
    }
    return errors;
  }

  function buildPayload(v) {
    var wantsContact = v.contact_preference === 'yes' || v.contact_preference === 'maybe';
    var text = function (s) { return String(s || '').trim() || null; };
    return {
      submission_id: v.submission_id,
      name: String(v.name || '').trim(),
      domain: v.domain,
      domain_other: v.domain === 'Others' ? text(v.domain_other) : null,
      overall_session: v.overall_session,
      usefulness: v.usefulness,
      learned_something: v.learned_something,
      liked_topic: v.liked_topic,
      liked_topic_other: v.liked_topic === 'Other' ? text(v.liked_topic_other) : null,
      attend_future: v.attend_future,
      suggestion: text(v.suggestion),
      contact_preference: v.contact_preference,
      contact_number: wantsContact ? normalizeIndianMobile(v.contact_number) : null
    };
  }


  // One id per submission attempt series. It is kept across failed attempts so a retry cannot
  // create a second spreadsheet row (the sheet de-duplicates on it), and replaced after success.
  function newSubmissionId(cryptoObj) {
    var c = cryptoObj || (typeof crypto !== 'undefined' ? crypto : null);
    if (c && typeof c.randomUUID === 'function') return c.randomUUID();
    var b = new Uint8Array(16);
    if (c && typeof c.getRandomValues === 'function') c.getRandomValues(b);
    else for (var i = 0; i < 16; i++) b[i] = Math.floor(Math.random() * 256);
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    var h = Array.prototype.map.call(b, function (x) { return (x + 256).toString(16).slice(1); }).join('');
    return h.slice(0, 8) + '-' + h.slice(8, 12) + '-' + h.slice(12, 16) + '-' + h.slice(16, 20) + '-' + h.slice(20);
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { validate: validate, buildPayload: buildPayload, normalizeIndianMobile: normalizeIndianMobile, newSubmissionId: newSubmissionId };
  }
  if (typeof document === 'undefined') return;

  var form = document.getElementById('feedbackForm');
  if (!form) return;
  var submitBtn = document.getElementById('submitBtn');
  var formError = document.getElementById('formError');
  var submitting = false;
  var submissionId = newSubmissionId();

  document.getElementById('year').textContent = new Date().getFullYear();

  function radio(name) {
    var el = form.querySelector('input[name="' + name + '"]:checked');
    return el ? el.value : '';
  }

  function values() {
    return {
      submission_id: submissionId,
      name: form.elements.name.value,
      domain: radio('domain'),
      domain_other: form.elements.domain_other.value,
      overall_session: radio('overall_session'),
      usefulness: radio('usefulness'),
      learned_something: radio('learned_something'),
      liked_topic: radio('liked_topic'),
      liked_topic_other: form.elements.liked_topic_other.value,
      attend_future: radio('attend_future'),
      suggestion: form.elements.suggestion.value,
      contact_preference: radio('contact_preference'),
      contact_number: form.elements.contact_number.value
    };
  }

  function showErrors(errors) {
    form.querySelectorAll('.fb-error').forEach(function (p) { p.textContent = ''; });
    form.querySelectorAll('[aria-invalid]').forEach(function (el) { el.removeAttribute('aria-invalid'); });
    var first = null;
    Object.keys(errors).forEach(function (key) {
      var p = document.getElementById('err-' + key);
      if (p) p.textContent = errors[key];
      var target = form.querySelector('[name="' + key + '"]');
      if (target) {
        target.setAttribute('aria-invalid', 'true');
        if (!first || (target.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_FOLLOWING)) first = target;
      }
    });
    return first;
  }

  function syncConditional() {
    var domain = radio('domain');
    var topic = radio('liked_topic');
    var pref = radio('contact_preference');
    document.getElementById('domainOtherWrap').hidden = domain !== 'Others';
    document.getElementById('topicOtherWrap').hidden = topic !== 'Other';
    document.getElementById('contactWrap').hidden = !(pref === 'yes' || pref === 'maybe');
  }

  form.addEventListener('change', function (e) {
    syncConditional();
    var key = e.target.name;
    var p = key && document.getElementById('err-' + key);
    if (p) p.textContent = '';
  });

  function setBusy(busy) {
    submitting = busy;
    submitBtn.disabled = busy;
    submitBtn.textContent = busy ? 'Submitting…' : 'Submit Feedback';
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (submitting) return;
    formError.hidden = true;

    var v = values();
    var errors = validate(v);
    var first = showErrors(errors);
    if (Object.keys(errors).length) {
      formError.textContent = 'Please fix the highlighted fields and try again.';
      formError.hidden = false;
      if (first) first.focus();
      return;
    }

    setBusy(true);
    ArckAPI.request('/feedback/inauguration', { method: 'POST', body: buildPayload(v), auth: false, timeoutMs: 25000 })
      .then(function () {
        submissionId = newSubmissionId();
        form.reset();
        syncConditional();
        document.getElementById('formCard').hidden = true;
        var thanks = document.getElementById('thanksCard');
        thanks.hidden = false;
        // Display only: not a form field, never part of the payload, and following is optional.
        document.getElementById('socialCard').hidden = false;
        thanks.focus();
        thanks.scrollIntoView({ behavior: 'smooth', block: 'center' });
      })
      .catch(function (err) {
        formError.textContent = ArckAPI.errorMessage(err, 'We could not submit your feedback. Please try again.');
        formError.hidden = false;
        formError.scrollIntoView({ behavior: 'smooth', block: 'center' });
        setBusy(false);
      });
  });
})();
