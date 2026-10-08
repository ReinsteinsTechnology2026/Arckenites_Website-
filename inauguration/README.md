# Arckenites Inauguration — Phase 1

A standalone landing page and registration form for the New Era Technologies
Inauguration Meeting (15 October 2026). Phase 1 only: no QR codes, no
tickets, no WhatsApp, no check-in, no admin dashboard, and no PostgreSQL
involvement. Registrations go to a Google Sheet.

This folder has no dependency on `arckenites-orange/` or `backend/`, and
nothing in those folders depends on it. It can be deployed to its own
subdomain (`inauguration.arckenites.com`) independently, whenever that is
set up.

## Files

- `index.html` — the landing page and the registration modal.
- `css/style.css` — all styling. Self-contained; does not use the portal's stylesheet.
- `js/app.js` — validation, the modal, and the one `fetch` call to Google Sheets.
- `google-apps-script.gs` — reference code to paste into the Apps Script editor. Not run by this website directly; it runs on Google's servers once deployed there.

## Setting up the Google Sheet

1. Create a new Google Sheet, e.g. "Arckenites Inauguration Registrations".
2. **Extensions → Apps Script**. Delete the sample code and paste in the contents of `google-apps-script.gs`.
3. Save the project.
4. **Deploy → New deployment**. Type: **Web app**. Execute as: **Me**. Who has access: **Anyone**.
5. Click **Deploy**, authorize the requested permissions, and copy the **Web app URL** it gives you (it ends in `/exec`).
6. Open `index.html` and replace the placeholder:
   ```html
   window.INAUGURATION_SHEET_ENDPOINT = "REPLACE_WITH_YOUR_APPS_SCRIPT_URL";
   ```
   with the URL from step 5.
7. If you edit the script later, use **Manage deployments → Edit → New version** so the URL stays the same, rather than creating a brand-new deployment.

The sheet gets a `Registrations` tab with columns: Timestamp, Name, Mobile, Email. The script creates that tab and the header row itself the first time it runs.

## How submission works

The form sends one `fetch` POST with `Content-Type: text/plain` (not
`application/json`). That's deliberate: it keeps the request a "simple"
cross-origin request, so the browser does not need to preflight it against
Apps Script, which does not implement OPTIONS. The Apps Script side still
parses the body as JSON. This is the standard way to post to an Apps Script
Web App from a browser.

If the request fails for any reason (network error, Apps Script not yet
configured, a non-2xx response), the page shows an error and lets the
visitor try again — it never shows "Registration Successful" unless Google
actually accepted the row.

## Testing locally

Serve the folder with any static file server, for example:

```
cd inauguration
python -m http.server 5600
```

Then open `http://127.0.0.1:5600/`.

**Before the real Apps Script URL is configured**, submitting shows "Registration
is not configured yet." That is expected — it means the page correctly
refuses to claim success without a real endpoint.

**To test the full flow without Google**, append a `?endpoint=` query
parameter pointing at any server that accepts a POST and returns 2xx, for
example a local stand-in server. This override exists only for testing; it
does nothing for a real visitor who never adds that parameter.

**Once the real Apps Script URL is deployed**, open the Web App URL directly
in a browser (a GET request) — it should return
`{"status":"ok","message":"Inauguration registration endpoint is live."}`.
Then submit a real test registration from the page (without the `?endpoint=`
override) and confirm the row appears in the Sheet.

## What remains for Phase 2

- QR code generation for each registration.
- Digital ticket (PDF or image) and its delivery by email.
- WhatsApp delivery.
- A hostess-facing scanner and server-side ticket validation.
- Check-in recording and an admin view of registrations and attendance.
- Moving registration storage from Google Sheets to a dedicated, isolated
  store if the event needs it (still not the existing portal's Postgres
  tables, per the isolation requirement).
- Configuring `inauguration.arckenites.com` in Cloudflare/DNS and Caddy, and
  deploying this folder there.
