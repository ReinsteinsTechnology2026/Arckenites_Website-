# Feedback form on its own domain: https://feedbackform.arckenites.com

A standalone deployment of the inauguration feedback form. It is separate from the main website:
nothing in `/opt/arckenites` (the production checkout), the main API, the database, `deploy.sh` or
the main Caddy site block is touched, and `arckenites.com` is unchanged.

```
Browser ──HTTPS──▶ Cloudflare ──▶ Caddy (feedbackform.arckenites.com site block)
                                    ├─ /                      → the feedback page (static files only)
                                    └─ /api/feedback/*        → 127.0.0.1:8100  (app.feedback_app, this repo)
                                                                   └─ Apps Script ──▶ Google Sheet (the only store)
```

The page calls same-origin `/api/...`, so no CORS setting and no change to the main API is needed.
Nothing is stored in PostgreSQL: the service has no database (its environment file uses a throwaway
`sqlite://` URL that is never opened), no migration, no queue.

## What is deployed

| Piece | Where | Notes |
|---|---|---|
| Static files | `/opt/arckenites-release/arckenites-orange/` | Only five files are exposed (see Caddy block). Every other file in that folder returns 404 on this domain. |
| Feedback API | `app.feedback_app:app` on `127.0.0.1:8100` | Serves only `POST /api/feedback/inauguration` and `GET /api/health`. |
| Environment file | `/opt/arckenites-release/backend/.env.feedback` | Mode 600. Holds the Apps Script URL and the shared secret. Never committed. |
| systemd unit | `arckenites-feedback.service` | Separate from `arckenites-backend`. |
| Caddy site block | one new block in the existing Caddyfile | Plus one `systemctl reload caddy`. |

## One-time setup (administrator steps)

### 1. Production Apps Script (Google account that owns the spreadsheet)
Follow `FEEDBACK_GOOGLE_SHEETS.md` steps 1-4 (fresh secret, `setupHeaders`, deploy as Web App with
Execute as Me / Access Anyone). Use a **new secret**, never one used for testing.

### 2. DNS and Cloudflare
- Add a DNS record for `feedbackform` that points to the same origin as the `arckenites.com` record,
  with the same proxy setting (orange cloud).
- If the main site is reached through a Cloudflare Tunnel instead, add a public hostname
  `feedbackform.arckenites.com` to that tunnel with the same service target as the main site.
- SSL/TLS mode must be the same one the main site uses (Full or Full (strict)).

### 3. Code on the server (the release checkout only)
```bash
cd /opt/arckenites-release
git fetch origin
git merge --ff-only origin/release/feedback-only      # fast-forward only; never touches /opt/arckenites
python3 -m venv backend/venv
backend/venv/bin/pip install -r backend/requirements.txt
```
Do **not** run `deploy.sh`, and do not create `backend/.env` here. If a `backend/.env` already exists in this
checkout, leave it alone and check what it contains with `grep -c DATABASE_URL`, not by printing it: the
service refuses to start unless `DATABASE_URL` is the `sqlite://` placeholder, so a copied production `.env`
can never connect it to PostgreSQL.

### 4. Environment file
```bash
sudo install -m 600 -o <service-user> -g <service-user> /dev/null /opt/arckenites-release/backend/.env.feedback
sudoedit /opt/arckenites-release/backend/.env.feedback
```
```
ENV=production
DATABASE_URL=sqlite://
JWT_SECRET=<any long random string; required by the settings loader, not used for feedback>
FEEDBACK_SHEETS_WEBAPP_URL=<production Apps Script /exec URL>
FEEDBACK_SHEETS_SHARED_SECRET=<production secret from step 1>
```

### 5. systemd unit: `/etc/systemd/system/arckenites-feedback.service`
```ini
[Unit]
Description=Arckenites feedback form API
After=network.target

[Service]
User=<service-user>
WorkingDirectory=/opt/arckenites-release/backend
EnvironmentFile=/opt/arckenites-release/backend/.env.feedback
ExecStart=/opt/arckenites-release/backend/venv/bin/uvicorn app.feedback_app:app --host 127.0.0.1 --port 8100 --proxy-headers --forwarded-allow-ips 127.0.0.1
Environment=PYTHONDONTWRITEBYTECODE=1
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload && sudo systemctl enable --now arckenites-feedback
curl -s http://127.0.0.1:8100/api/health        # {"status":"ok"}
```
`--proxy-headers` makes the 10-per-minute limit apply per visitor (from `X-Forwarded-For`, which only
Caddy on 127.0.0.1 can set) instead of to everyone at once. Run exactly one process: the limit is held in
memory per process, so do not add `--workers`. It also resets when the service restarts.

### 6. Caddy: add this block to the existing Caddyfile
```
feedbackform.arckenites.com {
    encode gzip zstd
    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "strict-origin-when-cross-origin"
    }

    root * /opt/arckenites-release/arckenites-orange

    # The site opens directly on the form.
    @home path /
    rewrite @home /inauguration-feedback.html

    # Only these files are served; everything else (login pages, dashboards, ...) is a 404 here.
    @files path /inauguration-feedback.html /css/inauguration-feedback.css /js/api.js /js/inauguration-feedback.js /assets/favicon.png /assets/logo-wordmark.png
    handle @files {
        file_server
    }

    # The page's footer "Visit our website" link points at index.html.
    redir /index.html https://arckenites.com/ 302

    handle /api/feedback/* {
        # Cloudflare's published address ranges (check https://www.cloudflare.com/ips-v4 and /ips-v6
        # before use; they change rarely). CF-Connecting-IP is believed ONLY when the connection really
        # comes from one of these, so a request sent straight to the origin cannot fake its address.
        @fromcloudflare remote_ip 173.245.48.0/20 103.21.244.0/22 103.22.200.0/22 103.31.4.0/22 141.101.64.0/18 108.162.192.0/18 190.93.240.0/20 188.114.96.0/20 197.234.240.0/22 198.41.128.0/17 162.158.0.0/15 104.16.0.0/13 104.24.0.0/14 172.64.0.0/13 131.0.72.0/22 2400:cb00::/32 2606:4700::/32 2803:f800::/32 2405:b500::/32 2405:8100::/32 2a06:98c0::/29 2c0f:f248::/32

        route {
            reverse_proxy @fromcloudflare 127.0.0.1:8100 {
                header_up X-Forwarded-For {http.request.header.CF-Connecting-IP}
            }
            # Anything else: use the real connecting address and discard whatever the client sent.
            reverse_proxy 127.0.0.1:8100 {
                header_up X-Forwarded-For {remote_host}
            }
        }
    }

    handle {
        respond "Not found" 404
    }
}
```
Back up the Caddyfile first, validate, then reload (a failed reload leaves the running config in place):
```bash
sudo cp -p /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak-$(date +%Y%m%d-%H%M%S)
sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy
```
The `caddy` user must be able to read `/opt/arckenites-release/arckenites-orange` (and nothing else there is
exposed: only the six listed files are served).

## Verification (after deployment)
1. `https://feedbackform.arckenites.com` opens the form, with a valid certificate, on desktop and mobile.
2. `https://feedbackform.arckenites.com/login.html` and `/api/auth/login` return 404 here.
3. Submit one entry named "Feedback Deploy Test": the thank-you appears and one row is in the sheet.
4. Press Submit again with the same form: no second row (duplicate id).
5. Temporarily stop the service (`sudo systemctl stop arckenites-feedback`), submit: the retry message
   appears and the answers stay; start it again and submit: success, one row.
6. `https://arckenites.com` still loads and logins work (nothing was changed there).
7. Delete the test rows from the sheet.

### Routing and address checks (run after the Caddy reload; none of these need a login)
From outside, expecting these status codes on `https://feedbackform.arckenites.com`:

| Request | Expected |
|---|---|
| `/` , `/inauguration-feedback.html`, `/css/inauguration-feedback.css`, `/js/api.js`, `/js/inauguration-feedback.js`, `/assets/logo-wordmark.png`, `/assets/favicon.png` | 200 |
| `/index.html` | 302 to `https://arckenites.com/` |
| `/login.html`, `/student-dashboard.html`, `/js/student-dashboard.js`, `/assets/logo.png`, `/css/../login.html`, `/%2e%2e/login.html`, `/.env`, `/.git/config`, `/backend/.env.feedback` | 404 |
| `/api/auth/login`, `/api/health`, `/docs`, `/openapi.json`, `/api/students` | 404 |
| `GET /api/feedback/inauguration` | 405 |

On the server, prove a forged address is ignored when the request does not come from Cloudflare. The
payload below is valid, so the first request writes one row (the rest are duplicates); delete that row
afterwards. Eleven requests with eleven different fake `CF-Connecting-IP` values must still end in a 429:
```bash
for i in $(seq 1 11); do
  curl -sk --resolve feedbackform.arckenites.com:443:127.0.0.1 -o /dev/null -w "%{http_code} " \
    -H "CF-Connecting-IP: 203.0.113.$i" -H "Content-Type: application/json" \
    -d '{"submission_id":"123e4567-e89b-42d3-a456-426614174999","name":"Spoof Test","domain":"IT","overall_session":"Good","usefulness":"Useful","learned_something":"Yes","liked_topic":"AI","attend_future":"Yes","contact_preference":"no"}' \
    https://feedbackform.arckenites.com/api/feedback/inauguration
done; echo   # expect: ten 201 followed by 429
```

## Rollback
1. Restore the Caddyfile backup made in step 6 and `sudo caddy validate` then `sudo systemctl reload caddy`.
2. `sudo systemctl disable --now arckenites-feedback`; remove `.env.feedback` if the project is abandoned.
3. Delete the `feedbackform` DNS record.

The main website and `/opt/arckenites` are unaffected at every step.

## Notes
- Spam: the only protection is the per-visitor limit (10 a minute). It does not stop someone with many
  addresses from filling the sheet. If that becomes a problem, add a Cloudflare rate-limiting rule or Turnstile.
- Optionally also restrict ports 80/443 on the server to Cloudflare's ranges at the firewall; the Caddy
  rules above already stop a forged address from being believed.
- If the Apps Script URL or secret is missing, the endpoint answers 503 and the page shows the retry message.
- The sheet is the only copy of the feedback: keep version history on and download periodic backups.
- Rotating the secret: see `FEEDBACK_GOOGLE_SHEETS.md`; afterwards edit `.env.feedback` and
  `sudo systemctl restart arckenites-feedback`.
