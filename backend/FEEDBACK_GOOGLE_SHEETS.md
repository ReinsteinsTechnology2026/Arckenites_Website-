# Inauguration feedback → Google Sheet (the only store)

Feedback from the inauguration feedback page is written **only** to a Google Sheet, through a
Google Apps Script Web App. Nothing is stored in PostgreSQL: there is no feedback table, no
migration, no queue and no retry worker.

```
Candidate's browser ──POST /api/feedback/inauguration──▶ FastAPI (stateless) ──POST + secret──▶ Apps Script ──▶ Google Sheet
        ▲                                                                                                          │
        └──────────── thank-you shown only after the sheet confirms the row ◀──────────────────────────────────────┘
```

**Why the browser does not call Apps Script directly.** A Web App that the browser can call must be
callable by everyone, so it could not be protected by a shared secret (any secret in page JavaScript
is public). The FastAPI endpoint is a thin, stateless relay that holds the secret server-side. It
keeps no data, opens no database session, and answers success only after Google confirms the write.

## Behaviour

- The thank-you message appears only after the Apps Script reports the row is in the sheet.
- If Google Sheets is unavailable, slow, rejects the secret or is not configured, the visitor sees
  *"We could not save your feedback right now. Please try again in a moment."*, keeps everything they
  typed, and can press Submit again. The endpoint answers `503`; the precise reason (a short code such
  as `timeout` or `script_unauthorized`) goes only to the server log.
- Each form load generates a unique `submission_id` (UUID) in the browser. It is reused for retries
  and replaced after success. The Apps Script skips an ID already in column A, so a retry after a
  lost reply cannot create a second row. A "duplicate" answer counts as success.
- The contact number is sent only when the candidate chose "Yes" or "Maybe" for course contact.
- Cell values starting with `=`, `+`, `-`, `@`, tab or CR get a leading `'` (in the backend and again
  in the script), and each row is written as plain text.
- Logs hold only the submission ID and a short error code, never names, numbers, answers, the secret
  or the Apps Script URL.
- The endpoint keeps its existing limit of 10 submissions per minute per IP.
- **The sheet is the only copy.** If it is deleted or damaged, that feedback is gone (see Backups).

## Sheet columns (row 1)

| # | Header |
|---|---|
| 1 | Submission ID |
| 2 | Submitted At (UTC, stamped by the server) |
| 3 | Name |
| 4 | Domain |
| 5 | Other Domain |
| 6 | Overall Session Rating |
| 7 | Session Usefulness |
| 8 | Learned Something New |
| 9 | Favourite Topic |
| 10 | Other Topic |
| 11 | Future Session Attendance |
| 12 | Suggestions |
| 13 | Interested in Course Contact (Yes / Maybe / No) |
| 14 | Contact Number |
| 15 | Google Sheets Sync Status (the script writes `Synced`; kept so the layout does not change) |

If row 1 holds different headers the script refuses to write (`header_mismatch`).

## Setup

### 1. Generate a shared secret
```
python -c "import secrets; print(secrets.token_urlsafe(32))"
```
Keep it out of git and chat. It goes in two places: the Apps Script and the server environment.

### 2. Create the Apps Script
1. Sign in as the Google account that owns (or can edit) the spreadsheet.
2. <https://script.google.com> → **New project** → name it "Arckenites Feedback Sheet".
3. Replace `Code.gs` with `backend/integrations/google_sheets/FeedbackSheet.gs`. **Line 17 holds the
   spreadsheet ID**; for a test use a *test* spreadsheet's ID (never the production one) and only
   put the production ID in the production script.
4. **Project Settings → Script properties → Add** `SHARED_SECRET` = the secret from step 1.

### 3. Prepare the spreadsheet
Run the function `setupHeaders` once in the editor and approve the permissions. It writes the 15
headers into row 1 of the first tab. Keep the spreadsheet **private**; never use link sharing.

### 4. Deploy as a Web App
**Deploy → New deployment → Web app**: Execute as **Me**, Who has access **Anyone**.
"Anyone" is required because the FastAPI server calls it without a Google login; it does not expose
the sheet: the URL only accepts a POST that carries the secret, can only append to this one sheet and
returns nothing from it. Copy the `/exec` URL. After editing the script later use
**Deploy → Manage deployments → Edit → New version** so the URL does not change.

### 5. Configure the server
Server environment (never commit real values):
```
FEEDBACK_SHEETS_WEBAPP_URL=https://script.google.com/macros/s/<deployment-id>/exec
FEEDBACK_SHEETS_SHARED_SECRET=<secret from step 1>
# optional: FEEDBACK_SHEETS_TIMEOUT_SECONDS=10
```
Restart the API. There is **no database migration** for this feature. If either value is blank the
endpoint answers `503` and the page shows the retry message; feedback is not stored anywhere else.

### 6. Smoke test
Submit one entry with an obviously fake name ("Sheets Test") and check the row appears, then delete it.

## Operations

| Situation (server log code) | What to do |
|---|---|
| `script_unauthorized` | Secret differs between the server environment and Script Properties. Fix one, restart. |
| `http_401` / `http_403` / `http_404` | Wrong URL, or the deployment is not "Anyone". Re-copy the `/exec` URL. |
| `script_header_mismatch` | Row 1 was edited. Restore the headers from the table above. |
| `script_busy`, `timeout`, `http_5xx` | Temporary Google problem; visitors retry. |
| `script_not_configured` | `SHARED_SECRET` is missing in Script Properties. |

## Secret rotation and recovery

**Planned rotation (no failed submissions):**
1. Generate a new secret.
2. Script properties: set `SHARED_SECRET_PREVIOUS` = the *old* secret, then `SHARED_SECRET` = the *new*
   one. The script accepts either (properties are read on every request; no redeploy).
3. Set the new secret in the server environment and restart the API.
4. Verify a test submission, then **delete** `SHARED_SECRET_PREVIOUS`.

Changing both sides at once without step 2 is safe but visitors in the gap see the retry message.

**Suspected leak of the secret or the `/exec` URL:** set a new `SHARED_SECRET` and do *not* set
`SHARED_SECRET_PREVIOUS` (the old one stops working immediately), update the server and restart. To
also invalidate the URL create a **new deployment**, switch the server to it, and archive the old one.
Someone holding the secret could append junk rows; they cannot read, change or delete rows. Review the
sheet for unexpected rows.

**Backups (the sheet is the only copy):** keep Google's version history on, restrict edit access to
named people, and download a periodic copy (File → Download → .xlsx/.csv) to private storage. If the
sheet is lost, restore from the latest copy or version history; entries made after that are lost.

| Problem | Recovery |
|---|---|
| Rows deleted or edited by mistake | File → Version history → restore. |
| Apps Script project deleted | Recreate it (Setup 2–4), update the URL and secret, restart. Existing rows are untouched. |
| Server environment lost | Restore the two variables. Nothing is queued, so nothing is lost; visitors in between saw the retry message. |
| Spreadsheet owner leaves | Transfer the spreadsheet and script to a shared account, redeploy the Web App from it. |

## Privacy notes

- The sheet holds names, free-text suggestions and, for opted-in candidates, mobile numbers. Share it
  only with people who need it.
- Apps Script web apps cannot read HTTP headers, so the secret travels in the HTTPS request body
  (server to Google only). Treat the `/exec` URL as confidential too.
- Retention is not enforced by software. Decide how long the sheet is kept and who deletes entries.

## Tests

```
cd backend
python -m unittest tests.test_inauguration_feedback tests.test_feedback_sheets_sync
node tests/feedback_apps_script.test.cjs
node tests/inauguration_feedback_ui.test.cjs
```
None of these talk to Google. A real end-to-end check needs the smoke test above against a *test*
spreadsheet and Apps Script project.
