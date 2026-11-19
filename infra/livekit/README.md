# Self-hosted LiveKit (development stack)

Components: LiveKit server (media/SFU), Redis, and LiveKit Egress (server-side
recording via headless Chrome). All Apache-2.0. Pinned tags: server `v1.13.11`,
egress `v1.15.0`, redis `7-alpine` (checked on Docker Hub 2026-10-28).

> **Status.** A staging variant of this stack (hard resource caps, Egress sharing
> LiveKit's network namespace) produced a real, verified recording on 2026-11-19.
> **This directory's own compose file has not been run yet**, and nothing here is
> production-ready. Read `NETWORKING.md` first: it records what broke (a loopback
> `node_ip` made Egress's ICE fail) and the staging/production approach.

## Resource needs
Egress renders the room in headless Chrome: budget roughly 2-4 CPU cores and
several GB of RAM **per concurrent recording**, plus Docker/WSL overhead. A
machine with ~2 GB free RAM cannot run this stack.

## Run
1. `cp .env.example .env`, set `LIVEKIT_API_SECRET`, and `RECORDING_STAGING_DIR`.
2. Backend `.env`: `LIVEKIT_URL=ws://127.0.0.1:7880`, `LIVEKIT_API_KEY`,
   `LIVEKIT_API_SECRET` (same values), `LIVEKIT_EGRESS_OUTPUT_PREFIX=/out`,
   `RECORDING_STAGING_DIR` (same folder), `RECORDING_ENABLED=true`.
3. `docker compose up -d`, then start the API and `python -m app.worker`.

## Going beyond one machine (needs explicit approval)
Public DNS + TLS for the signalling host, opening the UDP media range, a TURN
server for restrictive networks, and a real node IP. None of that is configured
here and nothing is exposed.
