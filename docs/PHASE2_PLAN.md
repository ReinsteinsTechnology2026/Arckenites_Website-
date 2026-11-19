# Phase 2 - Arckenites Custom Video Classroom (plan)

Phase 1 (commit 00dc42c) fixed the recording/permission rules on the backend. Phase 2 builds the
Arckenites-branded classroom UI on LiveKit. Local/mocked services only; no production access.

## What exists (inspected)
- Backend: `POST /api/class-runs/batch/{batch_id}/join` and `/api/class-runs/{run_id}/join` return `JoinOut`
  (livekit_url, short-lived room-scoped token, room_name, identity, display_name, is_trainer, recording status).
  Trainer-only approval: `/class-runs/{id}/recording/approval`. Status: `/class-runs/{id}/recording`.
  General meetings: `/api/meetings/*` (Jitsi-era join, chat, notes, participants, waiting room) - never recordable.
- Frontend (`arckenites-orange/`): `meeting-room.html` + `js/meeting-room.js` + `css/meeting-room.css` drive a
  **Jitsi iframe** for general meetings (own toolbar, side panel with participants/chat/notes, waiting room,
  hand-raise toast). No LiveKit client code exists yet. Shared helpers: `js/api.js` (`ArckAPI.request`), `js/auth.js`.
- Tests: node `.test.cjs` files run in a `vm` sandbox with fake DOM (see `backend/tests/*.test.cjs`).

## Reuse
`css/meeting-room.css` layout/tool-button/side-panel classes, `ArckAPI`/`ArckAuth`, the side-panel markup pattern,
notes + chat REST endpoints (milestones 3-4), `escapeHtml` pattern.

## Decision
Batch classrooms get a NEW page (`classroom.html`) so `meeting-room.html` (general meetings, Jitsi) is not changed.
This keeps "separate general meetings and batch classrooms" and avoids overwriting existing behaviour.

## Milestones
1. **Media + participants (this milestone):** `classroom-media.js` controller (mic, camera, screen share,
   participant state, injectable LiveKit SDK), `classroom.html/js/css` (join, tile grid, toolbar, participant list),
   node tests with a fake LiveKit room.
2. Recording indicator + trainer approval prompt (uses existing class-runs endpoints; trainer-only UI).
3. Live chat + private student notes with autosave (class-scoped endpoints; backend additions if needed).
4. Raise hand (LiveKit data message from students needs a server-side relay, since students cannot publish data) + trainer notifications.
5. Trainer moderation (mute/remove through backend, room_admin scoped to the room).
6. Entry points from student/staff dashboards; general-meeting page left on its own path; docs.

## Constraints
No weakening of token grants or recording guards; no production LiveKit; no migrations; nothing pushed.
