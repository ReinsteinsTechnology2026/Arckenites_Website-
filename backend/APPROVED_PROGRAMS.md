# Three-program admin catalog

The only programs available for new admin selection are:

1. Institutional Program
2. Freshers Job Hack Program
3. Certificational Program

## Identity and database safety

`app/core/approved_programs.py` is the shared backend policy. It resolves
these exact names against existing `programs` rows, allowing outer
whitespace/case differences. It uses the returned database IDs and preserves
their actual slugs. It has no hardcoded IDs, guessed aliases, inserts,
updates or migrations.

“Official Certification Program” is not automatically mapped to
“Certificational Program.” “Placement Training Program,” “Job Assist,” and
similar existing programs are not assumed to be Freshers Job Hack Program.
Changing those identities would need an explicit decision based on the
actual records and their history.

Missing approved names are reported. If more than one row matches an
approved name, neither is selectable until the canonical identity is
resolved. Inactive approved programs stay visible in catalog management but
cannot receive new assignments. Other unambiguous active approved programs
remain usable when one name is missing, ambiguous or inactive.

No existing program, student, enrollment, batch, resource, password or login
ID is automatically deleted, renamed, reassigned or rewritten. No database
schema migration is required; the previous migration head remains unchanged.

The configured local PostgreSQL database could not be reached, including
on a read-only retry outside the sandbox. Actual live program IDs/names are
therefore **unverified**. The migration seeds contain Institutional Program
and Official Certification Program, but migration contents are not proof
of current production records or approval to treat different names as aliases.

Before deployment, inspect the intended database with this read-only query:

```sql
SELECT id, slug, name, status
FROM programs
ORDER BY id;
```

Then check exact approved matches:

```sql
SELECT id, slug, name, status
FROM programs
WHERE lower(trim(name)) IN (
    'institutional program',
    'freshers job hack program',
    'certificational program'
)
ORDER BY name, id;
```

If a name is missing or multiple rows match, review the canonical records
explicitly. This implementation does not create substitutes or guess IDs.

## Enforced behavior

- Add New Student and Edit Student offer approved active database IDs only.
- A historical student assignment appears in a “Keep current program”
  placeholder; it is not added back to selectable options.
- An unrelated edit, including an old client echoing the existing historical
  program ID/slug, does not reassign the student or create an enrollment.
- Student creation and reassignment enforce the policy server-side for both
  ID requests and the legacy slug-based update path.
- Admin Programs cards and headline statistics include only the resolved
  approved catalog. Missing/ambiguous/inactive warnings are visible.
- Existing historical program detail URLs remain readable, including their
  students and batches. They are read-only for program/enrollment operations.
- Program creation is disabled in the UI and rejected by the API, preventing
  additional programs or duplicate approved records through the admin form.
- Approved program identity/name cannot be changed to another identity.
  Other program metadata and active status remain editable using the
  existing permission checks. Merely echoing the displayed name does not
  normalize or rewrite the stored name.
- Direct enrollment endpoints reject historical/unapproved program IDs.
- Batch creation and changed program assignments use the same approved
  active policy. Batch selectors use the shared options endpoint.
- Existing batch mappings survive unrelated edits and catalog load failure.
  An explicit choice of another approved program remains an admin action;
  the change does not happen automatically.
- Existing students/resources in legacy batches remain readable. New
  students cannot be allocated into a batch linked to an unapproved program.
  Repeating an already-existing batch membership remains a harmless no-op.
- Historical student lists, exports, batch details and read-only batch
  filters retain existing data. Historical labels are not erased merely to
  make those records look like one of the approved programs.

## API compatibility

`GET /api/admin/students/program-options` still returns a list, now restricted
to approved active records.

`GET /api/admin/students/program-options?include_status=true` adds opt-in
metadata with these fields:

- `options`: approved active `{id, name, slug}` choices
- `approved_names`: the three approved labels, in configured display order
- `missing_programs`
- `ambiguous_programs`
- `inactive_programs`
- `detail`: a human-readable catalog notice, or null

It still requires an admin account and appropriate existing student,
program-view or batch permissions. It exposes no student identities or
credentials. The shared frontend helper is `ArckAPI.approvedProgramOptions()`.

`GET /api/admin/programs/stats` adds `catalog_notice`.
Program detail responses add `is_approved` and `allows_new_enrollments` so
historical views can render without offering unsupported actions.

## Changed source files

- `app/core/approved_programs.py` (new shared policy)
- `app/api/routes_admin_students.py`
- `app/api/routes_admin_programs.py`
- `app/api/routes_admin_batches.py`
- `app/schemas/admin_students.py`
- `app/schemas/admin_programs.py`
- `../arckenites-orange/js/api.js`
- `../arckenites-orange/admin-students.html` and `js/admin-students.js`
- `../arckenites-orange/admin-programs.html` and `js/admin-programs.js`
- `../arckenites-orange/admin-program-detail.html` and `js/admin-program-detail.js`
- `../arckenites-orange/admin-batches.html` and `js/admin-batches.js`
- `../arckenites-orange/admin-batch-detail.html` and `js/admin-batch-detail.js`
- `tests/test_student_creation.py` (approved catalog fixtures; same provisioning checks)
- `tests/test_approved_programs.py` (26 new policy/HTTP regression tests)
- `tests/admin_program_ui.test.cjs` (simulated DOM preservation checks)

## Verification

Full unittest run: **138 tests, 124 passed, 14 skipped, zero failures**.
The skipped cases are pre-existing live PostgreSQL integration tests; the
new tests use isolated temporary SQLite databases and mocked email.

The 26 new tests cover returned names/actual IDs/slugs, changed database
IDs, name normalization without stored-name rewriting, missing and ambiguous
records, inactive choices, catalog statistics, ID and slug bypass attempts,
unchanged legacy assignments, enrollment protections, approved metadata
editing, program-creation rejection, batch reassignment/allocation and
existing authentication gates.

An additional focused regression check passed after the final name-preservation
adjustment. JavaScript syntax checks and the following UI smoke tests passed:

```powershell
# From the repository root:
node backend/tests/admin_program_ui.test.cjs
```

That file executes the real student and batch-detail scripts with a simulated
DOM, checking that only approved IDs are offered and that unchanged legacy
assignments are omitted from update payloads, including catalog failure.
It is not a full visual/browser test or a live PostgreSQL verification.

Use the safe isolated full-suite command in `STUDENT_EMAIL_PROVISIONING.md`
to reproduce Python tests without pointing existing integration fixtures at
a production database. No new dependencies or environment variables are needed.

Deployment and a live catalog check have not been performed for this change.
Deploy matching frontend/backend versions together after confirming the
canonical rows. No new migration is required for this policy.
