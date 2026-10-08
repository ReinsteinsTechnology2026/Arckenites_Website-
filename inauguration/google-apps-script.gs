/* Paste this into the Apps Script editor bound to your Google Sheet
   (Extensions > Apps Script). See README.md in this folder for the full
   deployment steps. This file is not executed by the website itself — it
   only runs inside Google's servers once you deploy it there. */

function doPost(e) {
  var lock = LockService.getScriptLock();
  lock.waitLock(10000); // serializes concurrent submissions so rows are never interleaved/lost
  try {
    var data = JSON.parse(e.postData.contents);

    var name = String(data.name || '').trim();
    var mobile = String(data.mobile || '').trim();
    var email = String(data.email || '').trim();

    if (!name || !mobile || !email) {
      return ContentService.createTextOutput(JSON.stringify({ status: 'error', message: 'Missing required field' }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('Registrations');
    if (!sheet) {
      sheet = SpreadsheetApp.getActiveSpreadsheet().insertSheet('Registrations');
    }
    if (sheet.getLastRow() === 0) {
      sheet.appendRow(['Timestamp', 'Name', 'Mobile', 'Email']);
    }
    sheet.appendRow([new Date(), name, mobile, email]);

    return ContentService.createTextOutput(JSON.stringify({ status: 'ok' }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ status: 'error', message: String(err) }))
      .setMimeType(ContentService.MimeType.JSON);
  } finally {
    lock.releaseLock();
  }
}

/* Optional: open the deployed Web App URL directly in a browser (a GET
   request) to confirm it responds, without submitting a real registration. */
function doGet(e) {
  return ContentService.createTextOutput(JSON.stringify({ status: 'ok', message: 'Inauguration registration endpoint is live.' }))
    .setMimeType(ContentService.MimeType.JSON);
}
