# Meetings module — server deployment

The application code (admin dashboard, meeting room, chat, notes, database) is
complete and works today against any reachable Jitsi Meet instance. This
document covers the one piece that's still infrastructure, not application
code: standing up a real, self-hosted Jitsi (and optionally Jibri for
recording) on your production server, and pointing `MEET_DOMAIN` at it.

Everything here assumes your existing setup: Ubuntu server, Caddy reverse
proxy, systemd services, self-hosted GitHub Actions runner, matching
`/opt/arckenites` — same as the rest of this deployment.

## 1. Deploy Jitsi Meet (self-hosted, Docker-based)

```bash
sudo mkdir -p /opt/jitsi-meet
cd /opt/jitsi-meet
sudo curl -O https://raw.githubusercontent.com/jitsi/docker-jitsi-meet/stable/docker-compose.yml
sudo curl -O https://raw.githubusercontent.com/jitsi/docker-jitsi-meet/stable/env.example
sudo mv env.example .env
```

Edit `/opt/jitsi-meet/.env`:
- `HTTP_PORT=8000` / `HTTPS_PORT=8443` → change to unused local ports, e.g. `8180`/`8543` (Caddy will front it — Jitsi doesn't need to own 80/443)
- `PUBLIC_URL=https://meet.arckenites.com`
- `ENABLE_AUTH=1`, `AUTH_TYPE=jwt`, `JWT_APP_ID=arckenites`, `JWT_APP_SECRET=<generate a long random secret>` — **this is what lets our backend eventually issue moderator-role tokens**; without it, Jitsi's own "first to join is moderator" default is what backs the `is_moderator` flag today, which works but isn't cryptographically enforced by Jitsi itself.
- `ENABLE_RECORDING=1` if you're deploying Jibri now (step 3) — otherwise leave at `0` and add later.

```bash
sudo docker compose up -d
```

## 2. Front it with Caddy (add to your existing Caddyfile)

```
meet.arckenites.com {
    reverse_proxy 127.0.0.1:8543 {
        transport http {
            tls_insecure_skip_verify
        }
    }
}
```

(Jitsi's own container terminates TLS on 8543 with a self-signed cert by
default; Caddy gets you a real cert for the public-facing domain and proxies
through. Alternatively set `DISABLE_HTTPS=1` in Jitsi's `.env` and proxy to
the plain HTTP port instead — simpler, and fine since Caddy is the only thing
that talks to it.)

```bash
sudo systemctl reload caddy
```

Confirm: `curl -I https://meet.arckenites.com` should return 200.

## 3. Recording (Jibri) — optional, do this once the above is confirmed working

Jibri is a separate service that joins a room as a silent participant and
records it via headless Chrome + ffmpeg. It's the heaviest piece here —
budget a dedicated VM or at least 2 CPU cores / 4GB RAM free, since it's
doing real-time video encoding.

```bash
sudo curl -O https://raw.githubusercontent.com/jitsi/docker-jitsi-meet/stable/docker-compose.yml  # already has a jibri profile
sudo docker compose --profile jibri up -d
```

Set in `.env`: `ENABLE_RECORDING=1`, `JIBRI_RECORDER_USER`, `JIBRI_RECORDER_PASSWORD`,
`JIBRI_XMPP_USER`, `JIBRI_XMPP_PASSWORD` (any values — internal-only creds
between Jibri and Prosody), and `JIBRI_LOGS_DIR`/`JIBRI_RECORDING_DIR` to a
host-mounted volume.

**Wiring it to this app** (the one piece of application code not yet
written, deliberately deferred — see the "Recording" section of the feature
itself): Jibri writes finished recordings to disk and can call a webhook on
completion. Add a `POST /api/admin/meetings/{id}/recordings/{rec_id}/complete`
endpoint that Jibri's finalize script calls with the file path/duration/size,
then have a small script move the file into `backend/uploads/recordings/`
and update the `MeetingRecording` row to `AVAILABLE`. Until this exists,
recordings show as "Not captured" in the admin UI — the start/stop actions
and audit trail all work, just no file is produced yet.

## 4. Backend environment variable

Add to `backend/.env` (and to your CI/deploy secrets):

```
MEET_DOMAIN=meet.arckenites.com
```

No other backend changes are needed — `alembic upgrade head` (already run
automatically by your `deploy.sh`) creates the meeting tables, and the
`meetings.*` permissions are seeded by `python seed.py`.

## 5. Verify end-to-end

1. Admin dashboard → Meetings → Create Meeting, host = yourself.
2. Click Start, then Join — you should land in a real Jitsi room at
   `https://meet.arckenites.com/ArckMeeting-<token>`.
3. Camera/mic/screen-share should work exactly as they do on meet.jit.si,
   since it's the same underlying software, self-hosted.

## Security follow-ups before relying on this in production

- **Enable JWT auth on Jitsi** (step 1) rather than leaving it open — right
  now anyone who discovers a room name (a 20+ char unguessable token, but
  still) could theoretically connect directly to Jitsi without going through
  our app's membership check, if they know the exact room name format. JWT
  auth makes Jitsi itself reject any connection without a valid token issued
  by our backend.
- **Firewall the raw Jitsi ports** (8180/8543 or whatever you chose) so only
  Caddy (localhost) can reach them — don't expose them directly.
- **TURN server**: for participants behind restrictive NATs/firewalls (common
  on corporate or mobile networks), add a TURN server (coturn is the usual
  self-hosted choice) — without one, some participants may fail to connect
  even though the app and Jitsi itself are working correctly.
- **Rotate `JWT_APP_SECRET`** if it's ever exposed, same as any other secret.
