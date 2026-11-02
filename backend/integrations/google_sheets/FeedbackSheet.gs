/**
 * Arckenites inauguration feedback -> Google Sheet.
 *
 * A Google Apps Script Web App that appends one row per feedback submission.
 * Called only by the Arckenites backend (never by a browser). See
 * backend/FEEDBACK_GOOGLE_SHEETS.md for deployment.
 *
 *  - Authenticated by a shared secret kept in Script Properties (SHARED_SECRET, and optionally
 *    SHARED_SECRET_PREVIOUS while rotating), sent in the JSON body because Apps Script web apps
 *    cannot read request headers.
 *  - The spreadsheet is fixed below. Requests cannot choose a spreadsheet or sheet.
 *  - Idempotent: a Submission ID that is already in column A is not appended again.
 *  - Always answers HTTP 200 with JSON: {ok:true,result:"appended"|"duplicate"} or
 *    {ok:false,error:"<code>"}. Error codes never contain submitted data.
 */

var SPREADSHEET_ID = '1lNLPpvMJxFjC9KTrZyvG7GJlCTTjPJSC6LFlXDRN0fY';
var SHEET_GID = 0; // the first tab (…/edit?gid=0)

// Column order. Keep in step with SHEET_COLUMNS in backend/app/core/feedback_sheets.py.
var HEADERS = [
  'Submission ID',
  'Submitted At',
  'Name',
  'Domain',
  'Other Domain',
  'Overall Session Rating',
  'Session Usefulness',
  'Learned Something New',
  'Favourite Topic',
  'Other Topic',
  'Future Session Attendance',
  'Suggestions',
  'Interested in Course Contact',
  'Contact Number',
  'Google Sheets Sync Status'
];

// Request field for each column, in HEADERS order. null = written by this script.
var FIELDS = [
  'submission_id', 'submitted_at', 'name', 'domain', 'domain_other',
  'overall_session', 'usefulness', 'learned_something', 'liked_topic', 'liked_topic_other',
  'attend_future', 'suggestion', 'contact_preference', 'contact_number', null
];

var UUID_RE = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
var MAX_CELL_CHARS = 5000;
var MAX_BODY_CHARS = 20000; // a real submission is a few KB; anything bigger is refused before parsing

function doPost(e) {
  try {
    var raw = e && e.postData && e.postData.contents;
    if (typeof raw !== 'string') return reply_({ ok: false, error: 'bad_request' });
    if (raw.length > MAX_BODY_CHARS) return reply_({ ok: false, error: 'payload_too_large' });
    var body;
    try {
      body = JSON.parse(raw);
    } catch (parseErr) {
      return reply_({ ok: false, error: 'bad_request' });
    }
    if (!body || typeof body !== 'object' || Array.isArray(body)) return reply_({ ok: false, error: 'bad_request' });

    var props = PropertiesService.getScriptProperties();
    var current = props.getProperty('SHARED_SECRET');
    var previous = props.getProperty('SHARED_SECRET_PREVIOUS'); // only set during a rotation
    if (!current) return reply_({ ok: false, error: 'not_configured' });
    var supplied = typeof body.secret === 'string' ? body.secret : '';
    // Both comparisons always run so the check does not reveal which secret matched.
    var matchesCurrent = safeEqual_(supplied, current);
    var matchesPrevious = previous ? safeEqual_(supplied, previous) : false;
    if (!matchesCurrent && !matchesPrevious) return reply_({ ok: false, error: 'unauthorized' });

    var id = typeof body.submission_id === 'string' ? body.submission_id : '';
    if (!UUID_RE.test(id)) return reply_({ ok: false, error: 'bad_submission_id' });
    for (var f = 0; f < FIELDS.length; f++) {
      var v = FIELDS[f] === null ? undefined : body[FIELDS[f]];
      if (v !== undefined && v !== null && typeof v !== 'string') return reply_({ ok: false, error: 'bad_request' });
    }

    var lock = LockService.getScriptLock();
    try {
      lock.waitLock(20000);
    } catch (lockErr) {
      return reply_({ ok: false, error: 'busy' });
    }
    try {
      var sheet = getSheet_();
      if (!headersOk_(sheet)) return reply_({ ok: false, error: 'header_mismatch' });

      var last = sheet.getLastRow();
      if (last > 1) {
        var ids = sheet.getRange(2, 1, last - 1, 1).getValues();
        for (var i = 0; i < ids.length; i++) {
          if (String(ids[i][0]) === id) return reply_({ ok: true, result: 'duplicate' });
        }
      }

      var row = [];
      for (var c = 0; c < HEADERS.length; c++) {
        row.push(FIELDS[c] === null ? 'Synced' : cell_(body[FIELDS[c]]));
      }
      var range = sheet.getRange(last + 1, 1, 1, HEADERS.length);
      range.setNumberFormat('@'); // plain text: nothing is parsed as a formula, a date or a number
      range.setValues([row]);
      SpreadsheetApp.flush();
      return reply_({ ok: true, result: 'appended' });
    } finally {
      lock.releaseLock();
    }
  } catch (err) {
    return reply_({ ok: false, error: 'server_error' });
  }
}

function doGet() {
  return reply_({ ok: false, error: 'method_not_allowed' });
}

/** Run once from the editor to write the header row (and nothing else). */
function setupHeaders() {
  var sheet = getSheet_();
  if (sheet.getLastRow() === 0) {
    sheet.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]).setFontWeight('bold');
    sheet.setFrozenRows(1);
  } else if (!headersOk_(sheet)) {
    throw new Error('Row 1 already has different headers. Fix or clear it first.');
  }
}

function getSheet_() {
  var spreadsheet = SpreadsheetApp.openById(SPREADSHEET_ID);
  var sheets = spreadsheet.getSheets();
  for (var i = 0; i < sheets.length; i++) {
    if (sheets[i].getSheetId() === SHEET_GID) return sheets[i];
  }
  throw new Error('sheet_not_found');
}

function headersOk_(sheet) {
  if (sheet.getLastRow() === 0) {
    sheet.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]).setFontWeight('bold');
    sheet.setFrozenRows(1);
    return true;
  }
  var found = sheet.getRange(1, 1, 1, HEADERS.length).getValues()[0];
  for (var i = 0; i < HEADERS.length; i++) {
    if (String(found[i]) !== HEADERS[i]) return false;
  }
  return true;
}

/** Text only, length-capped, and neutralised against spreadsheet formula injection. */
function cell_(value) {
  var text = value === null || value === undefined ? '' : String(value);
  if (text.length > MAX_CELL_CHARS) text = text.substring(0, MAX_CELL_CHARS);
  return /^[=+\-@\t\r]/.test(text) ? "'" + text : text;
}

/** Constant-time string comparison. */
function safeEqual_(a, b) {
  var diff = a.length ^ b.length;
  var len = Math.max(a.length, b.length);
  for (var i = 0; i < len; i++) {
    diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  }
  return diff === 0;
}

function reply_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
