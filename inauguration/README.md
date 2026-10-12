# Arckenites Inauguration

A standalone landing page, registration form, and automatic ticketing
system for the Arckenites Inauguration Event (Sunday, 18 October 2026,
10:00 AM – 12:30 PM, Krishna Kutir, Nungambakkam, Chennai). Registrations
go to a Google Sheet; Google Apps Script generates a unique ticket ID,
builds a personalised PDF invitation, and emails it to the registrant —
there is no separate backend and no PostgreSQL involvement.

This folder has no dependency on `arckenites-orange/` or `backend/`, and
nothing in those folders depends on it. It can be deployed to its own
subdomain (`inauguration.arckenites.com`) independently, whenever that is
set up.

## Files

- `index.html` — the landing page and the registration modal (10 fields: Full Name, Mobile, WhatsApp, Email, Address, Domain, Travel Method, Preferred Food, T-Shirt Size, Comments).
- `css/style.css` — all styling. Self-contained; does not use the portal's stylesheet.
- `js/app.js` — validation, the modal, and the one `fetch` call to Google Sheets. Inspects the JSON response body's `status` field (`ok` / `duplicate` / `error`) — it never shows success just because the HTTP status was 200, since Apps Script always returns 200 even for a rejected submission.
- `google-apps-script.gs` — reference code to paste into the Apps Script editor. Not run by this website directly; it runs on Google's servers once deployed there. This is also the Phase 1 ticket system: ticket ID generation, PDF building, email sending, and duplicate-email protection all live here.

## Setting up the Google Sheet (first time)

1. Create a new Google Sheet, e.g. "Arckenites Inauguration Registrations".
2. **Extensions → Apps Script**. Delete the sample code and paste in the contents of `google-apps-script.gs`.
3. Save the project.
4. **Deploy → New deployment**. Type: **Web app**. Execute as: **Me**. Who has access: **Anyone**.
5. Click **Deploy**. You'll be asked to authorize the script — see "Authorization" below; this version needs more permissions than the original (it now sends email and fetches external images).
6. Copy the **Web app URL** it gives you (it ends in `/exec`) and put it in `index.html`:
   ```html
   window.INAUGURATION_SHEET_ENDPOINT = "https://script.google.com/macros/s/.../exec";
   ```
7. If you edit the script later, use **Manage deployments → Edit → New version** so the URL stays the same, rather than creating a brand-new deployment.

### Updating an existing deployment to add the ticket system

If you already had the older version of this script running (registration
only, no tickets), you do **not** need to recreate the Sheet or the
deployment:

1. Open the same Apps Script project (Extensions → Apps Script on the same Sheet).
2. Replace its entire contents with the current `google-apps-script.gs`.
3. **Deploy → Manage deployments → (pencil icon on the existing deployment) → Version: New version → Deploy.** The `/exec` URL stays the same, so `index.html` does not need to change.
4. Re-authorize when prompted (see below) — the new permissions (Gmail send, external URL fetch) are not covered by the old authorization.
5. The next registration will automatically add 5 new columns to the end of your existing header row (Ticket ID, Ticket Status, Email Status, Email Sent At, Email Error) — your existing rows and columns A–K are never touched or reordered.

### Authorization

The first deploy (or the first run after pasting the new version) will show
Google's "This app isn't verified" / permission screen, because the script
now calls:

- **Gmail / MailApp** — to send the ticket email.
- **External URL fetch (UrlFetchApp)** — to fetch the Arckenites logo and
  generate the QR code image (see "External dependencies" below).
- **Spreadsheet access** — as before.

Click **Advanced → Go to (project name) (unsafe)** → **Allow**. This is
expected for a script you wrote yourself; "unverified" just means it hasn't
gone through Google's public-marketplace review, which isn't needed for a
script only you deploy and run.

**Who the email is actually sent from:** `MailApp.sendEmail` always sends
from the Google account that authorized the script (whoever clicks Allow
during deployment) — there is no way to make it send from a custom
`@arckenites.com` address unless that is itself the Google/Workspace
account doing the authorizing. The email's display **name** is set to
"Arckenites Inauguration", but the actual From address will be that
Google account's real address. If you want a branded sending address,
authorize the deployment using an `@arckenites.com` Google Workspace
account, if one exists; otherwise recipients will see the authorizing
account's own address as the sender.

**Email quota:** `MailApp` has a daily sending quota tied to the
authorizing account (roughly 100/day for a plain Gmail account, higher for
Google Workspace — see Google's current Apps Script quota documentation,
as these numbers change). For an event with many registrants, authorize
with a Workspace account if possible.

## The Google Sheet layout

The `Registrations` tab has these columns, in this order. Columns A–K are
the original registration fields and are never reordered; L–P were added
for the ticket system and are always appended after them, including when
migrating an existing sheet that only had A–K:

| Col | Header | Notes |
|---|---|---|
| A | Timestamp | |
| B | Full Name | |
| C | Mobile Number | |
| D | WhatsApp Number | same as Mobile, or a separate number |
| E | Email | one registration per email address (duplicates are rejected before any ticket is generated) |
| F | Address | |
| G | Domain | |
| H | Travel Method | |
| I | Preferred Food | |
| J | T-Shirt Size | |
| K | Comments / Feedback | optional |
| L | Ticket ID | e.g. `ARCK-4F2A9C`; unique per row, verified against existing IDs before being assigned |
| M | Ticket Status | `Generated` or `Failed` (PDF build failed — see Email Error) |
| N | Email Status | `Sent`, `Failed`, or `Skipped` (ticket PDF itself failed, so email was never attempted) |
| O | Email Sent At | populated only when Email Status is `Sent` |
| P | Email Error | the error message, if Ticket Status or Email Status is not a success |

## What happens on submission

1. Required fields are validated server-side (in Apps Script — this never
   relies on the browser's own validation alone).
2. If the email already has a row, the submission is rejected with
   `{"status":"duplicate", ...}` — no new ticket or email is ever generated
   for an email that has already registered, including from an accidental
   double-click or a browser retrying a dropped request.
3. A unique ticket ID is generated and **the registration row is saved
   first**, with placeholder `Pending` statuses — before any PDF or email
   work is attempted. If everything after this point fails, the
   registration itself is never lost.
4. The PDF ticket is built (name, ticket ID, event date/time/venue, a QR
   code containing only the ticket ID — never the name, email or mobile
   number — plus the Arckenites logo when it can be fetched; see below).
5. The email is sent with the PDF attached, to the registrant's email
   address, from the Google account that authorized the deployment.
6. The row is updated in place with the final Ticket Status, Email Status,
   Email Sent At, and Email Error.
7. The website's success message ("Your ticket will be sent to your mail.")
   is shown once the **registration** is saved — it does not depend on step
   5 having succeeded, since it only ever promises delivery will be
   attempted, not that it already happened. The actual delivery outcome is
   always in the Sheet (columns M–P) for you to check.

### Safe retry for failed emails

If Email Status is `Failed` or `Skipped` for any row, nothing further
happens automatically — there is no retry endpoint exposed on the web (by
design, so there is no extra admin surface to secure). Instead:

1. Open the same Apps Script project in the Apps Script editor.
2. In the function dropdown at the top, select **`resendFailedTickets`**.
3. Click **Run**.

This scans every row that has a Ticket ID but whose Email Status isn't
already `Sent`, and tries again — it skips every row that already
succeeded, so it is always safe to run more than once (e.g., once a day
until everything shows `Sent`).

### External dependencies (both optional/best-effort)

- **Logo image**: fetched at send time from
  `https://arckenites.com/assets/logo-wordmark.png` (the real production
  asset — Apps Script has no access to this repository's files directly,
  so a public URL is the only way to embed it). **As of this writing, that
  URL returns an error (HTTP 530, a Cloudflare-level error) when checked
  from this machine** — this needs to be resolved on the production
  site/Cloudflare side (out of scope for this change) before the real logo
  will appear in tickets. Until then, the PDF automatically falls back to
  a styled text "ARCKENITES" wordmark instead of failing — tickets and
  emails still generate and send normally either way.
- **QR code image**: generated via the free public service
  `api.qrserver.com`, called with only the ticket ID as its `data`
  parameter. If that service is unreachable, the PDF falls back to showing
  the ticket ID as plain text with an instruction to bring ID proof,
  instead of failing ticket generation.

Both fetches are wrapped so that a failure degrades gracefully rather than
blocking the ticket or the email.

## How submission works (transport)

The form sends one `fetch` POST with `Content-Type: text/plain` (not
`application/json`). That's deliberate: it keeps the request a "simple"
cross-origin request, so the browser does not need to preflight it against
Apps Script, which does not implement OPTIONS. The Apps Script side still
parses the body as JSON. This is the standard way to post to an Apps Script
Web App from a browser, and is unchanged by the ticket system above.

## Testing locally

Apps Script itself only runs on Google's servers — it cannot be executed
locally. Two separate things are tested instead:

**The server-side ticket logic** (ticket ID uniqueness, save-before-email
ordering, PDF/email success and failure paths, retry, duplicate handling,
field preservation, QR content) is tested by running the actual
`google-apps-script.gs` source inside a sandboxed Node environment with
mocked Apps Script services (`SpreadsheetApp`, `MailApp`, `UrlFetchApp`,
etc.) — nothing real is ever sent or written. See
`test_apps_script_ticket_system.js` (kept outside this repo folder, in the
session scratchpad, since it needs a Node runtime this static site does
not otherwise use).

**The website itself** is tested with a local static file server, for example:

```
cd inauguration
python -m http.server 5600
```

Then open `http://127.0.0.1:5600/`.

**Before the real Apps Script URL is configured**, submitting shows
"Registration is not configured yet." That is expected.

**To test the submission flow without Google**, append a `?endpoint=`
query parameter pointing at any server that accepts a POST and returns a
`{"status": "ok"}` JSON body, for example a local stand-in server. This
override exists only for testing; it does nothing for a real visitor who
never adds that parameter. A local stand-in cannot generate a real PDF or
send a real email — it only exercises the website's own request/response
handling, never Apps Script's PDF/email logic itself.

**Once the real Apps Script URL is deployed**, open the Web App URL
directly in a browser (a GET request) — it should return
`{"status":"ok","message":"Inauguration registration endpoint is live."}`.
Then submit one real test registration from the page (without the
`?endpoint=` override) using an email address you can check, and confirm:
the row appears in the Sheet with a Ticket ID and `Sent` statuses, and the
ticket email actually arrived with the PDF attached. **Do not claim this
is confirmed working without actually doing this check** — a local test
run can verify the logic, but only a real deployment can confirm Google
actually sent the email.

## What remains for later phases

- QR check-in / ticket verification at the venue (the QR code only
  contains the ticket ID — no scanner or verification endpoint exists yet;
  do not describe check-in as implemented).
- WhatsApp delivery.
- An admin view of registrations, tickets, and delivery status (today this
  is just the Sheet itself, plus the manual `resendFailedTickets` retry).
- Moving registration storage from Google Sheets to a dedicated, isolated
  store if the event needs it (still not the existing portal's Postgres
  tables, per the isolation requirement).
- Configuring `inauguration.arckenites.com` in Cloudflare/DNS and Caddy, and
  deploying this folder there.
- Resolving the production logo URL's current HTTP 530 error so the PDF's
  "best-effort" logo fetch actually succeeds instead of always falling
  back to the text wordmark.
