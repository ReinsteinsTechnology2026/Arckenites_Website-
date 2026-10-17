# Arckenites: context to give a developer or coding assistant

Copy the text below into the next development request, then append the specific feature and acceptance criteria. Read `PROJECT_REPORT.md` for the detailed assessment and `docs/project-inventory.json` for the route/table inventory.

---

You are extending an existing Arckenites production-oriented repository. Inspect the actual source before editing. Do not replace the application, infer complete features from sidebar labels, or claim production verification from a local test alone.

## Snapshot and structure

Source snapshot: 17 October 2026, commit `f306d1c`, branch `main`.

There are three systems:

1. `arckenites-orange/` + `backend/`: static marketing website and authenticated Admin/Trainer/Student/Community portal with a FastAPI/PostgreSQL backend.
2. `inauguration/`: independent event website. Google Apps Script stores registrations in Google Sheets, generates PDF tickets and sends Graph email. It does not use portal auth or PostgreSQL.
3. `windows-lab-agent/`: per-VM Windows service that polls the portal and enforces student RDP group membership, session logoff and rotating passwords.

The main project has 55 HTML pages, 42 frontend JS files, 33 API route modules with 228 route declarations including WebSocket, 44 ORM tables and 37 migrations. There is no frontend npm build requirement in this snapshot.

## Stack and implementation conventions

- Frontend: HTML/CSS/plain JS; existing Bootstrap/design classes; orange/dark styling with theme behavior. Reuse components and responsive rules.
- `arckenites-orange/js/api.js`: shared fetch helper, API base selection, sessionStorage token/user state. Localhost uses port 8000; production uses same-origin `/api`.
- `js/auth.js`: role entry points, onboarding redirects, page permission map and sidebar hiding.
- Backend: Python 3.11, FastAPI, SQLAlchemy 2, psycopg 3, PostgreSQL, Alembic, Pydantic, bcrypt, PyJWT, MSAL/httpx, openpyxl.
- `backend/app/main.py`: routers, middleware, startup loops.
- `app/api/routes_*.py`: existing handlers; `schemas/`: contracts; `models/`: entities; `crud/`: shared persistence/audit/permissions; `core/`: auth/email/uploads/labs/meeting helpers.
- Reuse `get_db`/SessionLocal; do not introduce another database/session/auth/email layer.

## Accounts and authorization

Top-level roles: `admin`, `staff` (trainer), `student`, `community`.

Admins have additional RBAC roles/permissions in `crud/permissions.py`. Use backend `require_permission("module.action")` for admin operations and role plus ownership checks for students/trainers. Hidden UI buttons are not an authorization boundary. Some dashboard/health/contact routes are role-only; verify the source rather than assuming universal granular RBAC.

Password login: `POST /api/auth/login` with `username` and `password`. bcrypt verifies the password. JWT JTI corresponds to an `auth_sessions` row. `get_current_user` checks signature/expiry, session revocation/expiry and account status. Preserve database-backed revocation and existing session semantics.

Google OAuth is for existing Admin/Trainer/Student accounts, with state/nonce checks and one-time login tickets. Community registration uses the separate OTP flow.

New students use normalized candidate email as `users.username` and initial `student_profiles.email`. Student email login ignores case/outer whitespace. Existing Admin/Trainer exact username behavior and legacy student IDs remain. Browser onboarding routes forced password changes and incomplete student profiles through the existing screens.

## Student provisioning just implemented

`POST /api/admin/students` requires `students.create` and accepts:

```json
{
  "full_name": "Arun Kumar",
  "email": "arun@example.com",
  "temp_password": "<admin supplied temporary password>",
  "program_id": 1
}
```

Validate/normalize -> duplicate check -> bcrypt -> User + StudentProfile + active ProgramEnrollment + audit -> commit -> credential email. Temporary passwords cannot exceed 72 UTF-8 bytes. Never return/store/log the plaintext password.

Response retains student fields and adds `program_id`, `program_name`, `credential_email_sent`, `detail`. Provider failure leaves the account created and returns a warning. `GET /api/admin/students/program-options` exposes active catalog choices to authorized student provisioners/editors. `POST /api/admin/students/{id}/credentials-email` verifies a supplied original temporary password; it does not reset it. Resend is restricted to active email-login students awaiting a password change.

Migration head: `c6e8a2b4d9f1` after `f9c2b7d4e1a8`. It widens login IDs to 255, checks normalized email-login duplicates before adding a unique index, and adds nullable `student_profiles.program_id`. Existing identities are not rewritten. See `backend/STUDENT_EMAIL_PROVISIONING.md`.

## Data and feature boundaries

- Program catalog and ProgramEnrollment are real tables. Legacy `StudentProfile.program` enum still coexists with primary catalog `program_id`; preserve compatibility.
- Programs have batches. Batches have trainers and student enrollments. Do not invent direct student-to-trainer assignments.
- `Batch.course` is still text; a Course/Module/Lesson engine is not implemented.
- Institutional students have the extensive dashboard; other programs currently use a smaller fallback. Avoid accidentally granting/denying features based solely on a new program slug.
- Support, resources, schedules, interviews, lead management, chat, Community posts, lab slots and VM grants have source implementations.
- Assignment/assessment/attendance/payment/certificate engines and disabled placement-company/job/statistics items are not complete merely because labels exist.
- Newsletter success UI is demo-only, without a subscription backend.
- User contact-email changes do not automatically rename login IDs.
- Preserve history on deletion; student accounts with related records are deactivated.

## Services and deployment

Portal email uses the existing shared `core/email.py`: Graph when fully configured; otherwise SMTP. Secrets are backend environment variables. Graph send failure does not fall back to SMTP per message. Community posts have a persisted retry queue; credential emails cannot use a plaintext content queue.

Main configuration: DATABASE_URL, JWT_SECRET, ENV, CORS_ORIGINS, PUBLIC_SITE_URL, Google OAuth fields, MS_GRAPH_* or SMTP_* fields, MEET_DOMAIN. Do not disclose values. Runtime uploads under `backend/uploads/` are ignored data and need backups outside Git.

WebSocket endpoint: `/api/chat/ws`. Connection manager and rate limiting are process-local. VM expiry and Community email loops start inside API processes. Do not assume horizontal scaling already works.

Scheduled meetings use Jitsi via MEET_DOMAIN. The separate `js/video-conference.js` helper still hardcodes `localhost:8443`. Backend-issued Jitsi JWT enforcement and a working recording-finalization pipeline are not complete. VM service behavior has not been verified on real hardware in project notes.

Inauguration: current script uses Graph credentials from Script Properties (`GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`), an embedded logo and external QR images. `doPost` saves Pending registrations quickly; the `resendFailedTickets` worker builds/sends tickets. Run `createPendingTicketsTrigger_setup` in Apps Script to install the every-minute trigger. The README contains older MailApp/live-logo/synchronous descriptions. Verify deployed script/trigger separately. QR verification is read-only, not authenticated attendance check-in.

Pushes to `main` trigger `.github/workflows/deploy.yml` on a self-hosted runner. `deploy.sh` stops the systemd backend before source/schema updates, migrates, runs `seed.py --rbac-only`, then starts and checks HTTP. Full `seed.py` creates demo accounts; do not casually run it on production. `/api/health` is liveness only, not database readiness. Deployment outcome and live migration revision are unverified here.

## Known priorities

1. Fix stored XSS in admin dashboard activity/notifications: raw actor/message fields can originate from public failed-login usernames.
2. Revalidate/revoke existing chat sockets; initial socket authentication alone does not enforce later revocation.
3. Define password-change/reset session revocation policy; student reset currently does not revoke existing sessions in that branch.
4. Unify video host configuration and enforce Jitsi media authorization.
5. Restrict inauguration `?endpoint=` override to development; current code permits arbitrary destinations.
6. Add CI tests, database-aware readiness, verified backups and staging.
7. Resolve documentation drift and verify real email/trigger/agent behavior.
8. Expand the shared student learning experience before treating the portal as a full LMS.

## Test evidence and working rules

Earlier full run: 112 tests, 98 passed, 14 live PostgreSQL tests skipped; all 27 new provisioning tests passed. JavaScript syntax and simulated-DOM form checks passed. SQLite/mocked mail does not verify live PostgreSQL migrations, actual inbox delivery, Jitsi, Apps Script deployment or Windows Service enforcement.

Before editing, identify affected files and inspect matching tests. Keep changes scoped. Use additive migrations and explicit legacy-data plans. Couple business changes and audits in a single owning transaction when required (`write_audit_event(..., commit=False)`). Add meaningful authorization/validation/rollback tests. Report implemented behavior, local test results and production checks separately. Do not push a report or feature to `main` without authorization for that push.

**Next requested feature:** [insert the feature, affected roles, UI flow, acceptance criteria and constraints here].
