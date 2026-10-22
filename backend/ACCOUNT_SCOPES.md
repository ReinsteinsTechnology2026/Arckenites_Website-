# Community and managed portal account scopes

## Diagnosis

All completed accounts share `users`; Community details live in `community_profiles`,
linked by `user_id`. Pending OTP verification lives in `community_registrations`.
Student, Trainer, and Admin details have their own profile tables, also keyed by user ID.

The creation path is:

- `js/admin-students.js` / `js/admin-staff.js`: validate required fields and email syntax,
  normalize email, POST to `/api/admin/students` / `/api/admin/staff`, display backend errors.
- `schemas/admin_students.py` / `schemas/admin_staff.py`: validate and normalize input.
- `routes_admin_students.create_student` / `routes_admin_staff.create_staff`: authorize
  the actor, validate policy, serialize provisioning on PostgreSQL, check `email_conflict`,
  hash the password, insert user/profile (and student enrollment), stage audit, commit,
  then send credentials. Integrity errors roll back and recheck conflicts.
- `crud/user.email_conflict` previously checked every username and every profile table,
  including Community. It returned `portal_login`, which became HTTP 409 in the routes.
- Even bypassing that check failed: migration `d17b417f440b` created global unique
  `ix_users_username`; `c6e8a2b4d9f1` added global normalized unique
  `uq_users_email_login_normalized`. Both were also present in SQLAlchemy metadata.

Admin-user creation uses generated name-based login IDs, not an email-input provisioning
API. Its existing role delegation and Super Admin restrictions are unchanged.

## Scope decision and implementation

Two identity scopes: Community, and managed portal (Student/Trainer/Admin/Super Admin).
This change permits reuse between those scopes, not new reuse between managed accounts.
Scope is derived explicitly from persisted role using a CASE expression in the email
unique index. No independently writable scope field can disagree with authorization.

`ix_users_username` becomes a nonunique lookup index. The normalized email index now
keys on scope plus `lower(trim(username))`; `uq_users_legacy_username` preserves global
exact uniqueness for non-email login names. Community profile email/mobile uniqueness,
registration email uniqueness, profile user-ID uniqueness, student/staff code uniqueness,
Google subject/user-ID uniqueness, session primary keys, and MFA token constraints remain.
Managed profile contact emails were not globally unique database identities and remain
unchanged; existing provisioning checks still inspect managed profiles.

Password login resolves candidates within the requested Student, Staff (staff/admin),
or Community audience before checking a password or updating lockout counters. It never
chooses an account by a matching password or exact email casing. Missing-context legacy
requests succeed only with exactly one candidate; ambiguity fails closed. Community
links and the login chooser now supply `portal=community`. Generated login-name allocation
uses an existence check so an ambiguous login result cannot be mistaken for availability.

## Security flow review

- Password hashing, strength policy, activation, lockout, maintenance policy, and MFA
  challenge/session issuance retain their existing implementation.
- JWT subject, server sessions, MFA credentials/challenges, self-service password changes,
  administrator resets, revocation, and authorization resolve user IDs, not shared emails.
- Community verification checks only Community identities. OTP and verification tokens
  remain scoped to pending Community registration; completion inserts a separate user,
  never links, promotes, or overwrites a managed account.
- No public email-only password-reset or invitation acceptance endpoint was found.
  Credential delivery/resend uses the selected student/trainer ID and existing password
  checks; email remains a delivery destination, not a grant of access to another account.
- Google matching explicitly excludes Community, requires exactly one managed candidate,
  and retains its unique subject linking checks. Tickets are bound to user ID. No automatic
  cross-scope identity linking was added.
- Audit writes and role checks are preserved. Email text in legacy audit targets remains
  descriptive and is not used to authenticate or select a session.

## Migration review and operational limits

`c365dd2994ef` follows repository head `c4e5f6a7b8c9`. It changes indexes only;
there is no user-data DML. It inspects actual unique constraints/indexes at application
and rejects unexpected additional global username constraints rather than guessing.
Downgrade refuses before DDL when cross-scope email duplicates exist. It does not merge,
rename, or delete accounts to force a rollback.

The migration was first source-reviewed and tested with mocked Alembic operations;
PostgreSQL index SQL was compiled from the model. It was later run, together with the
migration-graph, enum-transaction and related regression tests, against a disposable
PostgreSQL 16.14 database: fresh `upgrade head`, `downgrade -1` and re-upgrade, plus
upgrades from six selected earlier revisions including `c4e5f6a7b8c9`. Not every
historical starting revision, other PostgreSQL versions, live schema drift, or locking
behavior under production load has been verified. Production was never accessed or
modified, and its migration state is unverified. Full offline SQL generation
(`alembic upgrade head --sql`) remains unsupported (see below).
Future rollout must coordinate schema and application changes and stop old workers before
accepting duplicate emails: old code does not resolve accounts by context. No deployment,
commit, or push is part of this change.

### Offline SQL generation is unsupported

Full Alembic offline SQL generation (`alembic upgrade head --sql`) is currently
unsupported. Some migrations require live database inspection or data-dependent safety
checks (for example `b3d6f1a9c4e2` checks that `lab_access` is empty, and `c365dd2994ef`
inspects existing username constraints), and those checks cannot run without a database
connection. Run migrations online against the intended database. To review the SQL,
migrate a disposable database and inspect it with `pg_dump`. Do not bypass or remove the
safety checks to make offline generation work.

## Changed files

Backend: `app/models/user.py`, `app/crud/user.py`, `app/schemas/auth.py`,
`app/api/routes_auth.py`, `app/api/routes_community_auth.py`,
`alembic/versions/c365dd2994ef_scope_community_email_login.py`.

Frontend: `arckenites-orange/login.html`, `js/login.js`, `community.html`,
`community-register.html`, `js/community-register.js`, `js/community-join-popup.js`.

Tests: `tests/test_account_scopes.py`, `tests/portal_login_ui.test.cjs`.
Documentation: this file.

## Validation

Tests use temporary SQLite databases and mocked email; environment overrides set
`DATABASE_URL=sqlite://` and a synthetic JWT secret. Integration tests requiring the
marked disposable PostgreSQL database are not run. SQLite verifies actual ORM constraints
and HTTP behavior, but does not prove PostgreSQL DDL or advisory-lock behavior.

Passed regression coverage: StudentCreationTests (39), test_trainer_creation (6), test_google_auth (19), test_mfa (22), test_account_scopes (12).
The 12 scope tests were exercised as the 11-test scope run plus the added PostgreSQL DDL compilation test; migration tests and the strengthened session-isolation test were also rerun successfully.
`node backend/tests/portal_login_ui.test.cjs` and `git diff --check` passed.
