# Arckenites portal audit and repair report

**18 October 2026; current source commit `88a19bd` with uncommitted follow-up repairs.**

Sections 1–15 document the earlier audit of the `a249de1` working tree. Those
repairs were subsequently committed before this follow-up. Section 16 records
the current Admin/student/catalog task and supersedes earlier working-tree
status statements and program-management boundaries where they differ.

## 1. Executive summary

This is a local source and isolated-test audit. It does not establish that the
live portal is production-ready. The highest-risk confirmed local defects in
admin rendering, Super Admin account protection, shared-history deletion,
password handling and transaction boundaries were repaired in the earlier
audit. The current follow-up changes are uncommitted. No production database, server or email
provider was accessed for this repair task. No reset, push or deployment ran.

Evidence labels used here:

- **VERIFIED LOCALLY:** source inspection, syntax checks and the working-tree diff.
- **VERIFIED IN ISOLATED TEST ENVIRONMENT:** temporary SQLite/API, mocked DOM,
  Node, synthetic headless-browser tests, and a disposable PostgreSQL 14
  cluster on localhost port 55432/database `arckenites_qa_final`.
- **VERIFIED IN STAGING:** none; no staging URL, credentials or test provider
  was available.
- **VERIFIED READ-ONLY IN PRODUCTION:** none. A GitHub Actions run was checked
  read-only, but that does not establish the state of the production server.
- **NOT VERIFIED:** live portal behavior, database revision, email delivery,
  Jitsi/VM enforcement and end-to-end role coverage.
- **BLOCKED — ACCESS REQUIRED:** staging credentials/test email provider and
  approved read-only production access for the checks listed in sections 11
  and 14.

The existing reset preparation files were present and uncommitted at task start;
they were preserved. Their current source inventory covers 45 ORM tables,
62 foreign-key edges, 58 model indexes and 124 model constraints. The
preparation command's `execute` path still refuses before connecting.

### Task checklist

- [x] Inspect branch, commit, pre-existing changes, architecture, migration head, deployment workflow and reset artifacts.
- [x] Run a safe baseline suite with an unusable local-only database URL.
- [x] Trace Admin/Super Admin account, student provisioning, program and deletion paths.
- [x] Repair confirmed security and data-integrity defects; add isolated regression tests.
- [x] Check 55 portal HTML pages for missing local references and 43 portal/event JavaScript files for syntax errors.
- [x] Verify the deploy shell script parses with Git Bash and review deployment gates.
- [x] Verify PostgreSQL migrations, selected constraints and the last-Super-Admin concurrency race in a disposable PostgreSQL environment.
- [ ] Verify live service, reverse proxy, email, Jitsi and Windows agents with approved read-only access.
- [ ] Complete interactive browser QA across all role/page combinations.

## 2. Issues found

| Severity | Evidence/root cause | Status and regression evidence |
|---|---|---|
| Critical | `js/admin-dashboard.js` inserted public failed-login usernames and derived messages into HTML and an attribute without escaping. `routes_dashboard.py` reads `auth_audit_log.username_attempted` supplied through `routes_auth.py`. | Fixed; simulated DOM test injects HTML into activity, notification and health responses and checks escaped output. |
| High | `routes_admin_users.py` allowed an Admin with `admin_users.edit` to reset a Super Admin password or revoke sessions. Its delete path also lacked an actor Super Admin check for another Super Admin when more than one existed. | Fixed through canonical role checks on PATCH, revoke and DELETE; isolated HTTP test grants a regular Admin these permissions and gets 403 for all three actions. |
| High | Trainer and Admin delete routes deleted whole direct conversations to satisfy FKs, including another participant's message history. | Fixed; deletion now returns 409 for related/shared records and directs the operator to deactivate. Isolated HTTP tests confirm accounts and shared messages remain. Simple accounts still delete transactionally. |
| High | Self-service password change left other database-backed sessions active. Password hash, session revocation and audit used separate commits. | Fixed; other sessions are revoked and audit is staged with the hash in one transaction. The current onboarding session remains valid. Tests cover success and failed-commit rollback. |
| High | bcrypt truncates input after 72 bytes while several account, Community and meeting password schemas allowed longer UTF-8 strings. | Fixed with a hashing guard, verification guard and schema validators. Tests use a 74-byte Unicode value and a different suffix after byte 72. |
| High | Full `seed.py` could create demo accounts on a production-configured process; `config.py` had a known fallback bootstrap password. | Fixed; full seed refuses production before DB connection and rejects empty/placeholder admin passwords. `--rbac-only` remains available. Two isolated guard tests passed. |
| High | Public inauguration `?endpoint=` could redirect a visitor's registration to an arbitrary URL. | Fixed; the override works only on localhost/loopback. Node test covers public and local origins. |
| Medium | Other FastAPI validation errors could echo rejected password values through default 422 responses. | Fixed; standard detail-list fields remain while `input` and validator context are removed. HTTP regression checks password is absent. |
| Medium | Four upload routes read entire multipart files before checking their documented size limit. | Fixed with a limit-plus-one-byte read; two isolated tests cover overflow and exact-size input. Reverse-proxy request limits remain unverified. |
| Medium | Trainer and Admin creation committed accounts before their audit events; a later failure could return an error while leaving an account created. Trainer PATCH audit helpers could commit part of an update early. | Fixed with one transaction and rollback paths; isolated failed-commit tests confirm no account or partial trainer change persists. |
| Medium | Deployment liveness only checked `/api/health`, which does not query PostgreSQL, and the old workflow ran a server-side script copy. | Added `/api/ready`, a post-start readiness check, and a workflow that copies the script from the tested checkout. Isolated HTTP and local workflow checks pass; no new workflow run has occurred. |
| Medium | `student-dashboard.js` sent every non-Institutional student to a minimal welcome layout, even when newly provisioned into the approved Freshers or Certificational programs. | Assigned programs now use the full student dashboard. A headless Chromium smoke test checks both approved programs and the unassigned fallback. |
| Medium | The public home page's newsletter form cleared the email and claimed a successful subscription without any API or mailing-list call. | Replaced the form with an honest coming-soon message and removed the fake submit handler. A source regression check passed; no external subscription service was contacted. |
| Low | `routes_auth.py` assigned a raw dictionary to the typed `UserOut.admin_role` field after model validation, causing a Pydantic serializer warning. | The route now builds `AdminRoleSummary` through model validation; an existing multi-role login regression was rerun. |
| High | Concurrent changes to the final two Super Admins could both pass the count check; a first locking attempt acquired account rows before the shared role row and deadlocked in PostgreSQL. | Admin PATCH/DELETE now lock the shared role row before the target account row. Deterministic two-thread PostgreSQL tests cover disable, delete and role change; each leaves one active Super Admin. All three passed in 10 repeated runs. |
| High | PostgreSQL integration tests ran against any reachable `DATABASE_URL`; a mistaken environment could let tests write to an unintended database. | They now require explicit opt-in, `ENV=test`, an exact psycopg URL for a loopback `arckenites_qa_` database with explicit port, and a matching marker row. Marked QA, missing marker, unreachable target and rejected URL cases were exercised. |
| Medium | Uploads accepted extension-only signatures, allowing an executable renamed to a supported image/document extension. | New upload and profile-photo writes check file signatures or supported text/Office structure; targeted tests cover spoofed and valid content. This does not replace malware scanning or image decoding. |

### Open issues requiring separate work or policy

| Severity | Source evidence and required next step |
|---|---|
| High | Scheduled meeting join data contains a Jitsi domain and room name but no backend-issued Jitsi JWT (`routes_meetings.py`, `meeting-room.js`). Portal membership therefore does not by itself enforce media-server access. Review the actual Jitsi deployment and token configuration before implementing enforcement. |
| High | Community registration returns distinct email/mobile duplicate responses (`routes_community_auth.py`, `community-register.js`), revealing account existence. A generic response needs a usable registration/recovery UX and product approval; no response behavior was changed here. |
| High | The checked-in workflow now has a PostgreSQL-backed quality job and copies `deploy.sh` from the tested checkout. The workflow itself has not run yet; the systemd unit, runner configuration and proxy config are not checked in. Inspect `systemctl`/listener state read-only before release. |
| Medium | Upload signatures now reject obvious mismatches, but full malware scanning, image decoding, reverse-proxy request limits and storage/download review need separate design. |
| Medium | Lists such as student and trainer admin lists are unpaginated. Measure actual row counts/query timings before setting limits or indexes. |

## 3. Issues fixed

The changed files are listed in section 13. Each fix followed an identifiable
source path and has a targeted regression test. The Admin dashboard cache
version and inauguration script cache version were advanced so a deployed
browser can load corrected JavaScript after a separately approved deployment.
No existing migration or table was changed.

## 4. Admin audit

The source-side audit covered all `routes_admin_*.py` declarations for a
recognized backend dependency: no Admin route declaration lacked one. This is
a structural check, not proof of every record-level ownership rule. Existing
program tests verify approved catalog options and backend rejection of an
unapproved student program ID. Student creation writes User, profile,
enrollment and audit together before email; it returns email acceptance state
separately. The current student table's Super Admin trash action points to the
typed-confirmation permanent endpoint; the ordinary Admin action uses safe
DELETE/deactivation.

Trainer/Admin creation and trainer update now commit their audits atomically.
Trainer/Admin delete no longer destroys shared chat history. Other Admin
pages, filters and external services still need interactive QA in a staging
environment; the local link scan found no missing local HTML assets or links.

## 5. Super Admin audit

`require_super_admin` and `is_super_admin` use the current database user's
admin role slug, not a username. The permanent student route requires this
dependency and typed confirmation, calls the shared-history-preserving helper,
commits its audit in the same transaction and rolls back on errors. Existing
isolated tests cover regular Admin denial, revoked sessions, related records,
shared files, audit retention and FK failure rollback.

Regular Admin users with account-management grants are now blocked from
updating, revoking or deleting a Super Admin account. The last-active-Super-
Admin guard now serializes Admin PATCH/DELETE through the Super Admin role
row before locking the target account. The same order is used for disable,
delete and demotion; the role management route does not allow changing or
deleting the built-in Super Admin role. Disposable PostgreSQL races for all
three operations passed 10 repetitions each, with one success, one 400, and
one active Super Admin after each race. The rejected account remained intact.
Route transactions roll back on write failure. No database constraint alone
enforces the cross-row rule that one active Super Admin must remain: direct
SQL or a future writer that omits this lock could bypass it.

## 6. Authentication and security

The API uses bearer JWTs backed by `auth_sessions` rows; `get_current_user`
checks revocation, expiry and active user status. Student email login performs
case/outer-whitespace normalization while legacy Admin/Trainer exact matching
is preserved. Password hashes use bcrypt. The new 72-byte guard prevents
silent truncation for new credentials and rejects longer login candidates.

Password change retains the current session for profile completion and revokes
other sessions in the same transaction as password/audit updates. Password
reset and account deactivation had existing session-revocation paths.
Session storage is browser `sessionStorage`; CORS disallows credentials.
Further live rate-limit, Google sign-in and WebSocket checks remain outstanding.

## 7. Student provisioning

`POST /api/admin/students` normalizes the supplied candidate email, resolves
an approved catalog record by its actual ID, creates User/Profile/Enrollment
and audit in one transaction, then calls the configured email transport. A
provider failure is returned as a warning after account creation. Resend
checks the original temporary password and does not create another account.

The previously reported address with digits was investigated using a
synthetic `candidate03@example.com`. Chromium's `type=email` validity check
accepted the normalized value, and an isolated API test created the student.
The exact production incident cannot be reproduced without the original
browser state, loaded JavaScript version and response trace. No real email
was sent.

The two other approved program assignments now enter the full student
dashboard instead of the minimal fallback. A headless Chromium test verifies
the layout decision with synthetic Freshers and Certificational users.

## 8. Program restrictions

The source uses the three approved names: Institutional Program, Freshers Job
Hack Program and Certificational Program. Existing backend and simulated DOM
tests resolve actual IDs from catalog records, reject unauthorized IDs for new
students, and retain historical catalog/enrollment data. No program rows were
created, deleted or renamed during this audit.

## 9. Database and migrations

The local Alembic head is `d7f9b3c5e2a1`. The ORM source has 45 tables and
62 FK edges. The reset inventory also records 58 model indexes and 124 model
constraints. A fresh, disposable PostgreSQL 14 cluster was initialized under
the Windows temporary directory, bound only to `127.0.0.1:55432`, with
database `arckenites_qa_final`. The project's configured local database on
port 5433 was not used. `alembic upgrade head` from empty, `downgrade -1`,
and `upgrade head` succeeded. `alembic current` returned the expected head.
On a second QA database, downgrade failed as intended when a pending file
cleanup job existed and again when detached conversation identity existed;
the revision remained at head after both failures. PostgreSQL integration
tests verified selected constraints, real transaction rollback and the
last-Super-Admin disable/delete/demotion races. This is evidence for
PostgreSQL 14 in an isolated cluster, not the deployed database version or
migration state. `arckenites_qa_final` and `arckenites_qa_permanent` carry the
explicit marker `arckenites-local-qa-20261018`. The QA gate accepted the
marked target and refused a missing marker, an unreachable port and a
nonlocal URL. Its target check also requires explicit opt-in, `ENV=test`, a
loopback host, `arckenites_qa_` database name and exact psycopg driver.

## 10. Test results

The earlier no-PostgreSQL baseline had 188 tests, 174 passed and 14 skipped;
it is superseded by the final isolated PostgreSQL run below. A preceding
full suite on the marked QA database passed 197 tests before the last edits.
The final suite was rerun after the last source edits: **203 tests, 203
passed, 0 failed, 0 skipped** (261.671 seconds). The PostgreSQL concurrency
cases each passed 10 consecutive repetitions (30 tests total). The Admin
integration test module also passed
all 8 tests. The QA target guard's four unit tests passed, and manual
checks exercised a marked target, absent marker, unreachable port and
rejected nonlocal URL.

From `backend/`, the final suite used these isolated settings and command:

```powershell
$env:DATABASE_URL='postgresql+psycopg://arckenites_qa@127.0.0.1:55432/arckenites_qa_final'
$env:JWT_SECRET='isolated-qa-only'
$env:ENV='test'
$env:ARCK_ENABLE_POSTGRES_INTEGRATION='1'
$env:ARCK_QA_DATABASE_MARKER='arckenites-local-qa-20261018'
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

For the 10 race repetitions, `DATABASE_URL` pointed to the separately
marked `arckenites_qa_permanent` database and `PYTHONPATH=tests` was set.
The command in each repetition was:

```powershell
.\.venv\Scripts\python.exe -m unittest -q `
  tests.test_admin_user_integration.TestAdminUserUpdateIntegration.test_concurrent_deactivation_preserves_one_active_super_admin `
  tests.test_admin_user_integration.TestAdminUserUpdateIntegration.test_concurrent_deletion_preserves_one_active_super_admin `
  tests.test_admin_user_integration.TestAdminUserUpdateIntegration.test_concurrent_role_change_preserves_one_active_super_admin
```

The database marker guard also ran through `python -m unittest -q
tests.test_postgres_safety` with `PYTHONPATH=tests`. No production URL or
provider was used.

After the final edits, four checked-in Node regressions passed individually
with `node`: `admin_program_ui.test.cjs`, `admin_dashboard_security.test.cjs`,
`inauguration_endpoint.test.cjs`, and `public_newsletter.test.cjs`. All 43
portal/event JavaScript files passed `node --check`; `python -m compileall -q
backend/app backend/tests`, Git Bash `bash -n deploy.sh`, `git diff --check`,
and a PyYAML parse of `.github/workflows/deploy.yml` passed. The synthetic
headless Chromium student dashboard check (`python
backend/tests/browser_student_dashboard.py`) passed. No frontend build
pipeline or type checker is configured in this repository.

## 11. Deployment review

The latest read-only GitHub Actions query showed run 39031264614 completed
successfully for `a249de1`, before these local changes. Its original workflow
copied `/opt/arckenites/deploy.sh` from the server before fetching new source,
so the first release of any script change could execute stale deployment code.
The local workflow now checks out the pushed commit, runs static checks and
the full Python suite against disposable PostgreSQL, then lets deployment
start only if the quality job passes. `deploy.needs: quality` applies to each
workflow run, including a retry, while its `if` limits deployment to a push
on `main`; a manual `workflow_dispatch` runs quality but skips deployment.
The QA marker includes run ID and attempt so a new attempt cannot reuse an
old marker. The deploy job copies `deploy.sh` from the tested checkout.
`deploy.sh` refuses a `main` tip different from the tested GitHub SHA and
avoids a second Git reset when the frontend is a subdirectory of the app
checkout. Workflow YAML and shell syntax parsed locally and a source guard
test covers these links; the revised workflow itself has **not** run. A
rerun of only a failed deploy job may reuse a successful quality job from
the same SHA, which still satisfies the dependency. GitHub success for the
old run does not verify the current process, database revision, email delivery
or real Windows/Jitsi
enforcement.

No checked-in systemd unit or Caddy production config was found. The reported
Uvicorn `address already in use` error needs read-only server process/listener
evidence to determine whether a manually started process or service
configuration caused it.

## 12. Production reset status

The existing [production reset plan](PRODUCTION_RESET_PLAN.md) and dependency
inventory remain uncommitted and intact. Its `execute` command still refuses
before opening a database. Production DB target/schema/rows, protected IDs,
backup checksum and isolated restore remain unverified. **No production reset
or other production mutation occurred.**

## 13. Files changed

- Dashboard: `arckenites-orange/js/admin-dashboard.js`, `admin-dashboard.html`, and `backend/tests/admin_dashboard_security.test.cjs` — escape untrusted activity/notification/health text and refresh browser cache.
- Student dashboard: `arckenites-orange/js/student-dashboard.js`, `student-dashboard.html`, and `backend/tests/browser_student_dashboard.py` — use full dashboard for assigned programs, with an isolated browser smoke check.
- Public home page: `arckenites-orange/index.html`, `js/script.js`, and `backend/tests/public_newsletter.test.cjs` — remove a newsletter form that falsely claimed successful subscription.
- Account/auth: `backend/app/api/routes_auth.py`, `routes_admin_users.py`, `routes_admin_staff.py`, `backend/app/crud/session.py`, `backend/app/core/security.py`, `backend/app/config.py`, `backend/seed.py` — permissions, history-safe deletion, transaction/session/password/seed safety.
- Password contracts: `backend/app/schemas/{auth,admin_students,admin_staff,admin_users,community,meeting}.py` and `backend/app/main.py` — byte boundaries and secret-safe validation errors.
- Uploads: `backend/app/core/uploads.py` and `backend/app/api/routes_{profile,students,staff,admin_support}.py` — bounded reads and signature checks for new uploads.
- Readiness/release: `backend/app/api/routes_health.py`, `deploy.sh`, `.github/workflows/deploy.yml` — DB-aware readiness, isolated CI test gate, tested-commit pinning and frontend checkout detection.
- Event: `inauguration/js/app.js`, `inauguration/index.html`, `inauguration/README.md`, and `backend/tests/inauguration_endpoint.test.cjs` — local-only test endpoint override.
- Tests: `backend/tests/test_student_creation.py`, `test_admin_user_transactions.py`, `test_admin_user_integration.py`, `test_student_deletion_integration.py`, `test_password_boundaries.py`, `test_seed_safety.py`, `test_upload_limits.py`, `test_readiness.py`, `test_postgres_safety.py`, `test_postgres_constraints_integration.py`, `postgres_safety.py`, `prepare_postgres_qa.py`, `test_workflow_gate.py` — isolated regression coverage, deterministic concurrency checks and fail-closed PostgreSQL test targeting.

The eight uncommitted production-reset preparation files predated this task;
none was changed or used to perform a reset. All generated test data was
synthetic. The disposable PostgreSQL service was stopped after verification.

For later review, keep reset preparation in a separate commit from portal
repairs. The portal changes can be reviewed as (1) account/security/transaction
fixes, (2) UI and inauguration truthfulness/escaping fixes, and (3) readiness
and upload limits. No commit was created here.

## 14. Known limitations

No staging credentials, production SSH/DB credentials, approved backup, email
test provider, real Jitsi configuration, Windows VM, runner unit file or Caddy
config were available. The first rerun used an unreachable localhost database
URL; PostgreSQL-specific checks then used only the new temporary cluster on
port 55432. PostgreSQL 14 results do not prove PostgreSQL 16 CI or production
behavior. The last-active-Super-Admin rule is protected by application locks
and transactions, not a database-level cross-row constraint; direct SQL or
future nonconforming writers can violate it. Source and mock checks do not
prove every browser path or external service. The new CI workflow has not
executed on GitHub.

## 15. Next steps

1. Review this working-tree diff and the open high-severity policy/infrastructure items.
2. Run the revised GitHub quality job on a nonproduction branch or approved draft workflow, including its PostgreSQL 16 service, before enabling the new deploy path on `main`.
3. Complete staging browser QA for each Admin, Super Admin, trainer and student workflow, including email through a test provider.
4. Inspect production systemd/runner/Caddy/Jitsi settings read-only with authorized access; diagnose the Uvicorn listener conflict and design a tested CI gate.
5. Obtain a verified DB/uploads backup and isolated restore before considering any separately authorized production reset.
6. Approve and schedule code deployment separately. Pushing `main` triggers deployment.

## 16. Admin dashboard, provisioning and catalog follow-up (18 October 2026)

**VERIFIED LOCALLY:** This follow-up started from clean commit `88a19bd`.
The older migration `a7b3f9d1e6c4` inserts nine legacy programs, including
Institutional Program. It does not insert Freshers Job Hack Program or
Certificational Program. The options endpoint resolves real rows by normalized
name and refuses missing, inactive or ambiguous rows. Its previous message is
therefore consistent with the migration seed; it is not evidence of the live
catalog contents. Name comparison now collapses internal/outer whitespace
and uses Unicode case folding, while displayed approved names remain
canonical. Approved identity repair is separate from general catalog creation.

**VERIFIED IN ISOLATED TEST ENVIRONMENT:** The explicitly marked PostgreSQL
14 QA database contained nine active legacy rows: Institutional Program was
ID 3 and the other approved names were absent. The guarded, explicit
`tests/repair_approved_programs_qa.py` rehearsal inserted only the two
missing records. The resulting active IDs were Institutional 3, Freshers 10,
Certificational 11. A second run kept those IDs, and a run without the QA
marker refused before any catalog write. These are QA IDs, **not production
IDs**. No automatic production seed or migration was added.

**NOT VERIFIED:** The exact duplicate-email incident cannot be classified as
an existing student, another account type, an incomplete student, or a
different uniqueness conflict without authorized read-only identity data and
the request/response trace. Source shows that the earlier 409 text came from
a normalized `users.username` or legacy student contact-email match, or from
the same check after a concurrent uniqueness error. The user-facing wording
did not identify the matching record. The new check distinguishes an existing
student from another portal login/contact email, including Admin, trainer and
Community profiles. It leaves the existing account untouched. An unrelated
database constraint now has a separate safe response and logs only SQLSTATE
and constraint name, never the password or SQL parameters.

**VERIFIED LOCALLY:** General Admin program listing, metadata edits,
activation and deactivation are available through existing permission gates.
Explicit `programs.create` calls can add general programs with unique
normalized names; approved names are reserved for a reviewed catalog repair.
A new general program does not enter the three-option provisioning list.
PostgreSQL API writes serialize normalized-name checks with an advisory
transaction lock. Student creation still commits user, profile, enrollment
and audit together before attempting credential email. The student form now
reloads options when opened, disables submission if options fail to load,
refreshes the table after create/edit and labels password-reset accounts
accurately. Backend account creation requires `admin_users.create` and checks
that a non-Super-Admin caller cannot delegate permissions beyond their own;
role creation/editing applies the same scope rule. The user selected RBAC
only: no Admin account quota or approval step is required.

**VERIFIED IN ISOLATED TEST ENVIRONMENT:** Synthetic tests cover approved
options and enrollments, normalization, missing/inactive/ambiguous catalog
rows, reserved names, duplicate identities and contact emails, incomplete
student accounts, RBAC delegation, transaction rollback, and failed email
delivery. PostgreSQL concurrency tests create only one student identity and
enrollment for two simultaneous submissions, and one general program for
two simultaneous normalized-name submissions. The full-suite result and
final static checks are recorded below after completion.

**BLOCKED — ACCESS REQUIRED:** To classify the reported real email, an
authorized operator must inspect matching normalized `users.username`,
`student_profiles.email`, `admin_profiles.email`, `staff_profiles.email`
and `community_profiles.email` through read-only access, then review the
matching account/profile/enrollment state and the browser request's status.
Do not copy the personal address into fixtures or logs. To repair production
catalog rows, first read all candidate names/slugs/statuses and dependencies;
resolve ambiguity or inactivity explicitly. If a canonical approved row is
truly absent, obtain separate approval for a single transactional insert
using a reviewed slug, then verify the resulting actual ID and options API.
Do not infer production IDs from QA or rename Official Certification Program.

**NOT VERIFIED:** No staging or production browser, database, email provider,
or deployment was used. The current database schema has only active/inactive
program statuses; archival semantics and permanent program deletion need a
separate retention policy and dependency review. No migration was added.
The normalized program-name uniqueness guarantee covers writes through this
API; a direct SQL writer can still introduce duplicates, which the options
resolver then rejects as ambiguous. No production account, catalog row,
program ID, or enrollment was changed. No commit, push, deployment or reset
was performed for this follow-up.

### Follow-up verification and changed files

The first complete Python run on marked `arckenites_qa_final` passed **213
tests, 0 failures, 0 skips** in 243.440 seconds. The second complete run,
started after the last source edit, passed **213 tests, 0 failures, 0 skips**
in 280.122 seconds. These results include the marked PostgreSQL integration
tests; they do not establish staging or production behavior.
Both used, from `backend/`:

```powershell
$env:DATABASE_URL='postgresql+psycopg://arckenites_qa@127.0.0.1:55432/arckenites_qa_final'
$env:JWT_SECRET='isolated-qa-only'
$env:ENV='test'
$env:ARCK_ENABLE_POSTGRES_INTEGRATION='1'
$env:ARCK_QA_DATABASE_MARKER='arckenites-local-qa-20261018'
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

The explicit QA repair additionally required `ARCK_QA_PROGRAM_REPAIR=1` and
ran `python -m tests.repair_approved_programs_qa` from `backend/`; a missing
QA marker refused the write. The API options resolver was read against the
marked QA database after repair and returned IDs 3, 10, 11 with no catalog
notice. Direct PostgreSQL concurrency cases for student creation and general
program creation each passed. Four Node regressions, 43 JavaScript syntax
checks, Python compilation, `git diff --check`, and synthetic Chromium student
dashboard smoke passed. The Windows checkout has CRLF line endings for
`deploy.sh`; an LF-normalized temporary copy passed `bash -n`, matching the
repository's LF index content. The temporary copy was removed.

Changed tracked files: `arckenites-orange/admin-programs.html`,
`admin-students.html`, `js/admin-programs.js`, `js/admin-students.js`,
`backend/app/api/routes_admin_programs.py`, `routes_admin_roles.py`,
`routes_admin_students.py`, `routes_admin_users.py`,
`backend/app/core/approved_programs.py`, `backend/tests/admin_program_ui.test.cjs`,
`test_approved_programs.py`, `test_student_creation.py`, and this report.
New QA-only files: `backend/tests/repair_approved_programs_qa.py`,
`test_admin_creation_delegation.py`, `test_program_catalog_integration.py`,
and `test_student_creation_integration.py`. The reset preparation files
were unchanged. No generated or secret file was added to the working tree;
the supplied real address appears in no test fixture or code change.
