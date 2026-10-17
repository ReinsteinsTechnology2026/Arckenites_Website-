# Student email provisioning

New students use the candidate's normalized email as `users.username` and
`student_profiles.email`. Passwords use the existing bcrypt helper. Accounts
are active with `must_change_password=true`; the existing password-change
and profile-completion screens remain in use. Legacy accounts, passwords,
login IDs, program history and sessions are not backfilled or reset.

## Files

- `app/schemas/admin_students.py`: email/password validation, program ID,
  delivery result and resend schema.
- `app/schemas/auth.py`: email-length login IDs and program display name.
- `app/models/user.py`: normalized email-login index and program properties.
- `app/models/student.py`: nullable primary catalogue program reference.
- `app/models/program.py`: enrollment relationship back to the student.
- `app/crud/user.py`: normalized student email lookup; exact Admin/Trainer
  matching remains in place.
- `app/api/routes_admin_students.py`: atomic creation/enrollment/audit,
  delivery result, program options, verified resend, list/edit/export.
- `app/core/student_credentials.py`: branded HTML/plain welcome message via
  the existing Graph/SMTP transport, with escaped content and safe logging.
- `app/main.py`: provisioning validation errors omit submitted values so
  malformed password fields cannot be echoed in responses.
- `alembic/versions/c6e8a2b4d9f1_student_email_login.py`: guarded schema change.
- `tests/test_student_creation.py`: isolated ORM/HTTP regression tests.
- `../arckenites-orange/admin-students.html` and `js/admin-students.js`:
  candidate email field, real catalogue selection, validation, notices,
  existing actions and small resend panel. Login ID in Edit is read-only.
- `../arckenites-orange/login.html`: Email ID / User ID label; retains text
  input so legacy usernames continue working.
- `../arckenites-orange/js/api.js`: optional request timeout for email calls.
- `../arckenites-orange/js/complete-profile.js`: prefill the saved email.
- `../arckenites-orange/student-dashboard.html` and `js/student-dashboard.js`:
  show the assigned catalogue program, including custom program names.

## API

`POST /api/admin/students` still requires `students.create`:

```json
{
  "full_name": "Arun Kumar",
  "email": "arun@gmail.com",
  "temp_password": "Arun@12345",
  "program_id": 1
}
```

`program_id` must reference an active catalogue program. It is not a slug or
free text. A real active ProgramEnrollment and the primary profile reference
are created in the same transaction as the user, profile and audit entry.
The legacy program enum is also populated where the slug matches, preserving
the existing Institutional dashboard behavior.

The response is HTTP 201 with the existing student fields plus
`program_id`, `program_name`, `credential_email_sent` and `detail`. It never
contains the password/hash. Email failure is still 201 with
`credential_email_sent=false`; the student remains created. HTTP 409 rejects
an existing normalized login or legacy student contact email; HTTP 422
reports invalid/missing fields without echoing input. Failed writes roll
back and never send email.

`GET /api/admin/students/program-options` exposes only active program
IDs/names/slugs to admins with `students.create` or `students.edit`, so a
student provisioner does not need unrelated `programs.view` access.

`POST /api/admin/students/{id}/credentials-email` requires `students.create`:

```json
{"temp_password": "Arun@12345"}
```

The resend verifies the supplied original temporary password against bcrypt.
It is only available for active email-login students still awaiting a
password change. It does not reset passwords or retrieve them from storage.
If the temporary password is lost, use the existing explicit admin password
reset action, then resend using that new temporary password. A reset is
never performed implicitly by resend.

The existing Community announcement retry queue is deliberately not used:
it persists message content, which would persist a recoverable password.
Credentials stay in request/process memory and use explicit admin retry.
Provider acceptance does not guarantee inbox delivery; check provider
delivery logs/spam folders when investigating a message accepted but absent.

Existing student contact emails can still be edited through the existing
profile flow; changing a contact address does not rename the login ID.
Admin/Trainer authentication and existing legacy student login IDs are
preserved. Login accepts case/whitespace variations only for student email
IDs. An admin can change a student's primary program with `program_id` on
the existing PATCH route. Prior enrollment rows remain intact.

## Database preflight and migration

New head: `c6e8a2b4d9f1`, following `f9c2b7d4e1a8`.

Changes:

1. Widen `users.username` from 120 to 255 characters for email login IDs.
2. Add a unique expression index on `lower(trim(username))` for email-style
   login IDs, covering all roles to prevent ambiguous cross-role identities.
   Exact uniqueness remains in place too.
3. Add nullable `student_profiles.program_id` referencing `programs.id`.
   Existing rows remain NULL; their legacy program is unchanged.

Before schema changes, the migration counts normalized email-login
duplicates. It aborts with an actionable error if any exist. It does not
rename, merge or delete accounts. Use this read-only query against the
intended deployment database to inspect conflicts (results contain login
IDs; keep them private):

```sql
SELECT lower(trim(username)) AS normalized_login,
       array_agg(id ORDER BY id) AS user_ids
FROM users
WHERE username LIKE '%@%'
GROUP BY lower(trim(username))
HAVING count(*) > 1;
```

Resolve any identity conflict with account owners before rerunning. Do not
guess a replacement address or delete records to satisfy the constraint.
Repeated contact emails on legacy students are not rewritten and do not
block the index; the creation API rejects reusing those contact addresses.

Local Windows migration commands, from the repository root, when the
configured local PostgreSQL server is running:

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic heads
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Do not run new code before the migration: ORM reads use the new nullable
column. The migration requires online execution for its duplicate preflight.
Downgrade refuses to narrow login IDs if any exceed 120 characters.

## Email settings

No new provider or dependency is required. Existing settings are sufficient
when one complete transport is configured:

- Preferred Graph: `MS_GRAPH_TENANT_ID`, `MS_GRAPH_CLIENT_ID`,
  `MS_GRAPH_CLIENT_SECRET`, `MS_GRAPH_SENDER_EMAIL`; the existing Entra app
  must have admin-consented application `Mail.Send` access to the sender.
- Otherwise SMTP: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`,
  `SMTP_FROM_ADDRESS`; `SMTP_FROM_NAME` is optional branding.
- Existing `PUBLIC_SITE_URL` supplies the portal origin. Set it to
  `https://www.arckenites.com` if that is the intended canonical host; the
  welcome message appends `/login.html`.

Configure secrets only on the backend. The inspected local `.env` has
neither a complete Graph setup nor a complete SMTP setup. Production email
settings were not accessible and have not been changed. Without a complete
transport, accounts still create and the admin receives a clear email
failure warning. Mail sending is mocked in tests; no real credential email
is sent by the test suite.

## Deployment / restart

The existing deployment workflow copies and runs `deploy.sh`, which stops
the backend, updates code, installs dependencies, migrates, syncs RBAC and
restarts only after migrations succeed. For that deployment path:

```bash
cp /opt/arckenites/deploy.sh /tmp/arckenites-deploy.sh
bash /tmp/arckenites-deploy.sh
```

For a manual migration/restart after the new source has been staged, using
the paths from the existing deployment script:

```bash
cd /opt/arckenites/backend
sudo systemctl stop arckenites-backend
./venv/bin/alembic current
./venv/bin/alembic upgrade head && sudo systemctl start arckenites-backend
sudo systemctl status arckenites-backend --no-pager
curl -fsS http://127.0.0.1:8000/api/health
```

If migration fails, keep the backend stopped and investigate; do not start
new code on the old schema. Publish the matching frontend files with the
backend change. No production deployment, secret update or service restart
is performed by this implementation.

## Tests

The new tests use temporary SQLite databases, real request authorization,
real bcrypt, real SQLAlchemy transactions and a mocked email boundary. They
cover creation, normalization, invalid/duplicate addresses, cross-role and
legacy collisions, role/status/first-login behavior, program enrollment and
editing, post-commit email order, rollback, delivery failure, safe logs and
HTML, resend, authorization, unchanged legacy/Admin/Trainer logins, Excel
fields and formula safety, and migration guards/index SQL.

Run the full existing unittest suite with this safe PowerShell command from
`backend`. The overridden URL intentionally cannot reach a real database;
existing tests that require a configured PostgreSQL server will skip.
The new regression tests still run against their own temporary databases:

```powershell
@'
import os, unittest
os.environ['DATABASE_URL'] = 'postgresql+psycopg://unused:unused@127.0.0.1:1/unused?connect_timeout=1'
os.environ['JWT_SECRET'] = 'student-tests-only-secret-not-for-production'
suite = unittest.defaultTestLoader.discover('tests')
result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(not result.wasSuccessful())
'@ | .\.venv\Scripts\python.exe -
```

For live PostgreSQL integration tests, use a dedicated local test database,
apply migrations there, seed the expected test admin/roles, then run
`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`. Never point
the existing integration tests at production: they create and remove fixture
records. SQLite tests and compiled PostgreSQL index SQL do not prove live
PostgreSQL DDL or advisory-lock behavior.

### Results from this implementation

- Full existing suite plus new tests: **112 run, 98 passed, 14 skipped**;
  no failures (76.580 seconds).
- **27 new provisioning/migration regression tests passed** in that run.
- Changed JavaScript files passed `node --check`; `git diff --check` passed.
- A Node simulated-DOM smoke check passed for required-field validation,
  email normalization, program ID submission, failed-email notices, form
  clearing/closing, escaped table rendering and resend. This is not a full
  browser or visual/responsive test.
- Local PostgreSQL was unreachable and Docker was not running. The 14
  pre-existing live PostgreSQL tests were skipped. No live PostgreSQL
  migration or concurrent advisory-lock test was performed.
- Neither local email transport was configured; provider behavior was mocked.
  A real welcome email and the deployed service still need verification
  after configuring email and applying the migration.
