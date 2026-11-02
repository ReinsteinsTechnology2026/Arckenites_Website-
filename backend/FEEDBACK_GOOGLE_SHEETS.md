# Inauguration feedback → Google Sheet

Every successful feedback submission is saved to PostgreSQL first, then copied as one row to a
Google Sheet through a Google Apps Script Web App. The sheet is a convenience copy; PostgreSQL
stays the source of truth.

```
Browser ──POST /api/feedback/inauguration──▶ FastAPI ──▶ PostgreSQL (saved, 201 returned)
                                                │
                              background task + retry worker
                                                ▼
                           Apps Script Web App (secret checked) ──▶ Google Sheet
```

The browser never talks to Google and never sees the URL or secret.

## Behaviour

- The visitor always gets the normal thank-you once PostgreSQL has the row, whether or not the
  sheet is reachable.
- Each row has a `submission_id` (UUID). The Apps Script skips an ID already in column A, so
  retries and a lost reply cannot create duplicate rows.
- Sync status lives on the row (`inauguration_feedback.sheets_sync_status`):
  `PENDING` (will be retried), `SYNCED`, `FAILED` (gave up after `FEEDBACK_SHEETS_MAX_ATTEMPTS`).
  A failure is never recorded as `SYNCED`.
- Retries: first attempt right after the submission, then a worker retries `PENDING` rows with
  backoff 60 s, 2 min, 4 min … capped at 1 h, up to 15 attempts (about 9 h). Retries survive a restart.
- Several API workers or instances may run the retry loop at once. Each row is claimed with a
  PostgreSQL row lock (`FOR UPDATE SKIP LOCKED`), and its status and retry time are re-checked
  under the lock, so one row is sent by one worker at a time and never before its backoff ends.
  The Apps Script's Submission ID check is a second, independent guard.
- If the application is restarted or killed during a Google request, the attempt was never
  committed: the row is still `PENDING` and is retried after restart. If Google had already
  written the row, the retry gets `duplicate` and the row becomes `SYNCED` without a second row.
- The retry worker runs in the background and does not delay startup. On shutdown it takes no new
  rows; a request already in flight is allowed to finish (up to the 15 s timeout, 5 s to connect).
- If the two environment variables are blank, feedback is still saved; rows stay `PENDING` and
  are sent automatically once the variables are set (this also back-fills earlier submissions).
- The contact number is sent only when the visitor chose "Yes" or "Maybe" for course contact.
- Cell values starting with `=`, `+`, `-`, `@`, tab or CR get a leading `'` (both in the backend
  and again in the script), and the row is written as plain text.
- Logs and `sheets_last_error` hold only the submission ID, attempt number and a short code
  (`http_503`, `timeout`, `script_unauthorized` …), never names, numbers, answers or the URL.

## Sheet columns (row 1)

| # | Header |
|---|---|
| 1 | Submission ID |
| 2 | Submitted At (UTC) |
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
| 15 | Google Sheets Sync Status (written as `Synced`) |

The script writes the header row itself (see step 3). If row 1 already holds different headers
it refuses to write (`header_mismatch`) instead of putting data under the wrong columns.

## Setup

### 1. Generate a shared secret
```
python -c "import secrets; print(secrets.token_urlsafe(32))"
```
Keep it out of git and chat. You will paste it in two places: the Apps Script and the server `.env`.

### 2. Create the Apps Script
1. Sign in as the Google account that owns (or can edit) the spreadsheet
   `1lNLPpvMJxFjC9KTrZyvG7GJlCTTjPJSC6LFlXDRN0fY`.
2. <https://script.google.com> → **New project** (standalone is fine) → name it
   "Arckenites Feedback Sheet".
3. Replace the contents of `Code.gs` with `backend/integrations/google_sheets/FeedbackSheet.gs`.
   The spreadsheet ID and tab (`gid=0`) are fixed at the top of that file.
4. **Project Settings → Script properties → Add**: `SHARED_SECRET` = the secret from step 1.

### 3. Prepare the spreadsheet
In the editor choose the function `setupHeaders` and **Run**. Approve the permission prompt (the
script needs access to Google Sheets and to run as you). This writes the 15 headers into row 1 of
the first tab. Nothing else is touched. Keep the spreadsheet **private**; do not use "anyone with
the link".

### 4. Deploy as a Web App
**Deploy → New deployment → Web app**
- Execute as: **Me**
- Who has access: **Anyone**

"Anyone" is required because the FastAPI server calls it without a Google login. It does not make
the spreadsheet public: the URL only accepts a POST that carries the secret, can only append to
this one sheet, and returns nothing from it. Copy the **Web app URL** (ends in `/exec`).

After editing the script later, use **Deploy → Manage deployments → Edit → New version**
so the URL stays the same.

### 5. Configure the server
In `backend/.env` (production: the server's real environment; never commit it):
```
FEEDBACK_SHEETS_WEBAPP_URL=https://script.google.com/macros/s/<deployment-id>/exec
FEEDBACK_SHEETS_SHARED_SECRET=<secret from step 1>
```
Optional tuning: `FEEDBACK_SHEETS_TIMEOUT_SECONDS` (15), `FEEDBACK_SHEETS_MAX_ATTEMPTS` (15),
`FEEDBACK_SHEETS_RETRY_INTERVAL_SECONDS` (60).

### 6. Migrate and restart
```
cd backend
alembic upgrade head      # f1a2b3c4d5e6 (feedback table) then a7c3d9e2f1b8 (sync columns)
```
The chain is `… → 9c1e5a7b3d20 → 9d2f6b8c4e31 (current production head) → f1a2b3c4d5e6 → a7c3d9e2f1b8`.
It does not depend on the meeting or recording migrations. Both only add to `inauguration_feedback`;
no other table is touched. Take a database backup first (`maintenance/backup_database.py`).
Then restart the API. Existing feedback rows receive an ID and are queued as `PENDING`.

### 7. Smoke test
Submit one real feedback entry on the page (use an obviously fake name such as "Sheets Test").
A row should appear in the sheet within seconds. Then delete that row in the sheet and, if you
do not want it, in the database. Check status:
```sql
SELECT sheets_sync_status, count(*) FROM inauguration_feedback GROUP BY 1;
SELECT id, sheets_sync_attempts, sheets_last_error, sheets_next_attempt_at
FROM inauguration_feedback WHERE sheets_sync_status <> 'SYNCED';
```

## Operations

| Situation | What to do |
|---|---|
| `sheets_last_error = script_unauthorized` | Secret differs between `.env` and Script Properties. Fix one, restart. |
| `http_401/403/404` | Wrong URL, or the deployment is not "Anyone". Re-copy the `/exec` URL. |
| `script_header_mismatch` | Row 1 was edited. Restore the headers from the table above. |
| `script_busy`, `timeout`, `http_5xx` | Temporary; retried automatically. |
| Rows are `FAILED` | Fix the cause, then `python -m maintenance.retry_feedback_sheet_sync`. |
| Rotate the secret | See "Secret rotation and recovery" below. |
| Sheet was cleared, edited or lost | `python -m maintenance.retry_feedback_sheet_sync --all` re-sends every row; rows already present are skipped. |
| Remove the integration | Blank the two variables. Feedback keeps saving to PostgreSQL. |

## Secret rotation and recovery

**Planned rotation (no lost submissions):**
1. Generate a new secret (step 1 of Setup).
2. Apps Script → Project Settings → Script properties: set `SHARED_SECRET_PREVIOUS` = the *old*
   secret, then `SHARED_SECRET` = the *new* one. The script now accepts either. (No redeploy needed:
   properties are read on every request.)
3. Put the new secret in the server `FEEDBACK_SHEETS_SHARED_SECRET` and restart the API.
4. Check a test submission lands in the sheet, then **delete** `SHARED_SECRET_PREVIOUS`.

If you change both sides at once without step 2, requests in the gap fail as
`script_unauthorized`, stay `PENDING` and succeed on retry once the values match (about 9 hours of
retries are available).

**Suspected leak of the secret or the `/exec` URL:** set `SHARED_SECRET` to a new value and do *not*
set `SHARED_SECRET_PREVIOUS` (the old secret stops working immediately); update the server and restart.
To also invalidate the URL, create a **new deployment** (Deploy → New deployment), switch the server to
the new URL, then archive the old deployment under Manage deployments. What a holder of the secret
could do is append rows to this one sheet; they cannot read, change or delete rows. Check the sheet for
rows whose Submission ID is not in `inauguration_feedback` and delete them.

**Recovery cases**

| Problem | Recovery |
|---|---|
| Rows stuck as `FAILED` | Fix the cause, run `python -m maintenance.retry_feedback_sheet_sync`. |
| Sheet deleted, cleared or restored from an old copy | Restore headers (`setupHeaders`), then `python -m maintenance.retry_feedback_sheet_sync --all`. |
| Apps Script project deleted | Recreate it (Setup 2–4), update the URL and secret in `.env`, restart, run the command above with `--all`. |
| Server `.env` lost | PostgreSQL still holds every submission. Restore the two variables; `PENDING` rows send automatically. |
| Spreadsheet owner left the organisation | Transfer the spreadsheet and the script to a shared account, then redeploy the Web App as that account. |

PostgreSQL is always the source of truth, so the sheet can be rebuilt from it.

## Privacy notes

- The sheet will contain names, free-text suggestions and, for opted-in visitors, mobile numbers.
  Share it only with people who need it, and do not turn on link sharing.
- Apps Script web apps cannot read HTTP headers, so the secret travels in the HTTPS request body.
  Treat the `/exec` URL as confidential too.
- Deleting a row from PostgreSQL does not delete it from the sheet (and vice versa).

## Tests

```
cd backend
python -m unittest tests.test_feedback_sheets_sync tests.test_inauguration_feedback
node tests/feedback_apps_script.test.cjs
```
No network or real spreadsheet is used. `tests/test_feedback_sheets_postgres.py` (concurrent workers,
a process killed mid-request) runs only against a marked disposable PostgreSQL database, the same way
as the other `*_integration` tests (`ARCK_ENABLE_POSTGRES_INTEGRATION=1`, database name `arckenites_qa_*`).
None of these tests talk to Google; a real end-to-end check needs the manual smoke test above.
