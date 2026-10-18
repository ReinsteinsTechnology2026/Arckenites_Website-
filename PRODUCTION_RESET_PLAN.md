# Arckenites production reset preparation

**Preparation only — 17 October 2026. No reset, deployment, production migration,
service restart, account change, upload deletion, commit or push was performed.**

## 1. Status and decision boundary

Repository: `main`, commit `a249de1c664721da55cb11cd52a95a1cf7609d5b`.
The working tree was clean at task start. New artifacts from this task remain
uncommitted. [GitHub deployment run 39031264614](https://github.com/ReinsteinsTechnology2026/Arckenites_Website-/actions/runs/39031264614)
completed successfully; its log records both checkouts at `a249de1`, migrations
OK, and completion at **17:37:36 IST**. This is workflow evidence, not direct
inspection of the current server process or database.

**Production schema, row counts, target endpoint, backups, trigger definitions,
server sequence state and restore capability have NOT been verified.** No
production DB credential or SSH session was supplied/used. The production
collector below has not been run. Its target and output locations must be
independently verified by the operator; this report supplies no real DB endpoint.

The user selected: **protect Super Admin; individually review other admins**.
Other administrators are neither automatically protected forever nor eligible
for automatic deletion. They remain held until their IDs, responsibilities,
shared ownership and explicit retain/remove decisions are reviewed.

Deliverables:

- [Full source table/FK inventory](docs/reset-dependencies.md).
- [Machine-readable source inventory](docs/reset-source-inventory.json).
- [Read-only inventory and preview command](backend/maintenance/reset_prepare.py).
- [Draft row-classification policy](backend/maintenance/reset_policy.py).
- [Read-only verification SQL](backend/maintenance/reset_checks.sql).
- [Isolated preparation tests](backend/tests/test_reset_preparation.py).

The command contains **no reset executor**. `execute` always exits before opening
a database connection. This is deliberate: the unknowns and individual ownership
decisions do not yet permit a reviewed deletion manifest. The script previews
candidate records; it never represents candidates as authorized deletions.

## 2. Inventory: source facts versus production checks

Current SQLAlchemy metadata declares **45 tables, 62 FK edges, 58 model indexes,
and 124 model constraints**, not the
historical 44 tables. `file_cleanup_jobs` is the added table. The source migration
head is **`d7f9b3c5e2a1`**. PostgreSQL also has `alembic_version`, outside ORM
metadata. Extra schemas, extension relations, old tables, custom triggers or
operator-created tables remain possible. No custom trigger/function creation
was found in the checked-in migration source; that does not establish absence
in production.

The inventory links above list every model table, column name/type/nullability,
FK source/target, model index, and model constraint. Migration-created or
database-only indexes and constraints still require live catalog comparison.
The only declared FK cascade is
`community_post_email_deliveries.post_id → community_posts.id`; the reset design
still requires explicit bounded child operations. Do not rely on cascades or ORM
`delete-orphan` behavior as a bulk-reset policy.

| Group | Current tables | Draft treatment |
|---|---|---|
| Protected configuration | `admin_roles`, `permissions`, `admin_role_permissions`, `programs`, `system_settings` | Preserve all rows/IDs/values; never bootstrap or seed |
| Migration state | `alembic_version` | Preserve, no migration during reset |
| Mixed identity | `users`, `student_profiles`, `staff_profiles`, `admin_profiles`, `community_profiles` | Protect Super Admin by role+slug; review other admins and inconsistent profiles; ordinary operational users are candidates |
| Mixed authentication | `auth_sessions`, `user_google_identities`, `google_login_tickets` | Preserve protected/held users' links and sessions; remove only eligible account-owned records |
| Training memberships | `program_enrollments`, `batch_enrollments`, `interview_schedules` | Eligible user-owned rows only; preserve catalog and any held-user association |
| Training containers/resources | `batches`, `class_sessions`, `study_materials`, `class_videos` | Manual ownership approval: no reliable creator/owner is recorded for resources; trainer assignment alone is not ownership |
| Chat | `conversations`, `direct_messages`, `batch_messages`, `batch_chat_read_state` | Preserve mixed/protected/unknown history; fully operational direct threads can be candidates; batch history held while batch ownership is unresolved |
| Support | `support_tickets`, `support_messages`, `support_attachments` | Keep entire threads with protected/held/unknown contributions; exclusively operational threads may be candidates |
| Meetings | `meetings`, `meeting_participants`, `meeting_messages`, `meeting_notes`, `meeting_recordings` | Keep shared history; host is not sufficient proof of recording ownership; recordings and batch-linked meetings require review |
| Community | `community_registrations`, `community_posts`, `community_post_email_deliveries` | Staging registrations are candidates; posts by protected/held/unknown authors remain; remove eligible recipient delivery associations only |
| Sales/contact | `leads`, `contact_enquiries` | Enquiries are candidates; leads require review because admin authorship does not establish whether business records are protected shared data |
| Lab scheduling | `lab_slot_bookings`, `lab_access_overrides` | Eligible user-owned rows only |
| Lab machines | `lab_vms`, `lab_vm_access` | Preserve machine inventory/agent configuration; eligible grants are candidates subject to password rotation and Windows enforcement approval |
| Audit/security | `auth_audit_log`, `lab_access_audit_log`, `lab_vm_access_audit_log` | Retain event content; approved nullable identity links may detach, never automatically purge audits |
| File worker state | `file_cleanup_jobs` | Hold, reconcile queued jobs and prevent unintended cleanup on resume |

### Production inventory procedure

Use a dedicated approved read-only PostgreSQL principal and a protected libpq
service entry (`PGSERVICEFILE`/`.pgpass` with restricted permissions). Never put
connection strings or passwords on a command line, in Git, or in this report.
Do not use the backend's write credential just because it is readily available.

An operator must independently verify database name, DB OID, server address,
port, principal, and that this really is the production target (for example
against the infrastructure inventory). For Unix-socket connections whose
server address is NULL, this collector refuses: use a separately reviewed
identity method, not invented placeholder values.

Prepare an operator-owned JSON file **outside Git**, using real verified values:

```json
{
  "environment": "production",
  "verified_by_operator": true,
  "database": "<verified database name>",
  "database_oid": 0,
  "server_address": "<verified server address>",
  "server_port": 5432,
  "database_user": "<approved read-only principal>"
}
```

Zero is a placeholder, never an approved database OID. The JSON is an identity
attestation for read-only inspection, **not execution authorization**.

From an approved reviewed checkout, in `backend/` (future operator commands):

```bash
./venv/bin/python -m maintenance.reset_prepare dry-run \
  --pg-service arckenites_inventory_ro \
  --expected-target /approved/private/arckenites-target.json \
  --output /approved/private/arckenites-inventory-UNIQUE.json
```

The tool uses one repeatable-read, read-only transaction, lock/statement
timeouts and a bounded ID/FK projection (100,000 rows total by default). It
collects relations across non-system schemas, exact row counts for base tables,
columns, all catalog constraints and indexes, FK/nullability/action drift,
custom triggers, event triggers, rules, sequences, extensions, revision and draft
per-table candidate/retain/manual
counts. It never selects password/token values, account names, contact details,
message contents or uploaded file paths. It does not query view or foreign-table
contents. Trigger function bodies and foreign-server options must be inspected
privately by a DBA because they can contain credentials or external side effects.

RLS filtering raises an error rather than silently reporting incomplete counts;
the transaction-local `row_security=off` setting does not bypass policies.
PostgreSQL documents both this behavior and transaction-level read-only limits.
[RLS documentation](https://www.postgresql.org/docs/current/ddl-rowsecurity.html),
[read-only transactions](https://www.postgresql.org/docs/current/sql-set-transaction.html).

Any missing/extra table, FK change, partitioning, custom rule/trigger, RLS,
external/view relation, missing active Super Admin, unexpected role/profile,
pending cleanup or unresolved ownership blocks execution readiness. Counts may
be expensive; exceeding the timeout/projection limit is a capacity-review gate,
not permission to silently skip records or raise limits without review.

The output uses exclusive creation, owner-only permissions where the OS supports
them, and refuses live evidence paths inside the repository. On Windows, enforce
directory ACLs separately. Evidence contains internal IDs and infrastructure
metadata, so keep it access-controlled and outside Git. No production inventory
output is checked into this task.

## 3. Explicit preservation allowlist

### Mandatory row sets

1. `users` where **`role=admin` and linked `admin_roles.slug=super_admin`**.
   Use the existing `SUPER_ADMIN_SLUG` authority, not names, emails or role ID 1.
   Preserve every matching account, including inactive accounts; require at least
   one currently usable account before a reset can be authorized. Preserve ID,
   username, password hash, active status, onboarding/lockout state and admin role.
2. All profile rows and authentication identity/configuration needed by those
   users. A staff-profile attached to a protected admin does not make it eligible.
   Preserve linked Google identity and protected sessions/login tickets. Expired
   transient ticket cleanup is separate work; no global authentication purge.
3. All `admin_roles`, `permissions`, `admin_role_permissions`, `programs`,
   `system_settings`, and `alembic_version` rows, exactly as they were.
4. Every current program ID and catalog row. The three approved Admin choices
   remain Institutional Program, Freshers Job Hack Program, Certificational
   Program. Preserve any other historical catalog entries too.
5. All audit events, including protected and operational actors. Retain textual
   evidence under the current retention policy. No automatic historical erasure
   or invented retention period is introduced by this reset.
6. Environment/secrets and deployment files, identity-provider/email/JWT
   configuration, and machine inventory/agent credentials. Never print or copy
   values into source control. Recover secrets via the approved secret vault.

### Held pending individual decisions

- Every non-Super administrator, its profiles, sessions and identity links.
- Any nonadmin with an admin role/profile or role/profile inconsistencies.
- Rows with NULL/deleted/unknown authors where operational ownership cannot be
  established; already-detached history is not automatically disposable.
- Shared content containing protected or held users' data, uncertain batches,
  class content, recordings, leads and cleanup queue state.
- An account referenced by preserved `system_settings.updated_by_id` or a
  nonnullable retained actor/owner FK. Do not rewrite settings or transfer
  ownership just to make removal succeed.

## 4. Proposed operational deletion and shared-data policy

Let **S** be mandatory Super Admin IDs, **H** individually held/review IDs, and
**O** ordinary verified student/staff/community account IDs without admin role
or profile ambiguity. Preview candidates are not approval. Require `S ∩ O = ∅`
and `H ∩ O = ∅`; unknown roles are held, never swept into O by negation.

- Eligible profiles, enrollments, interviews, bookings, overrides, grants,
  sessions, Google identities/tickets, recipient delivery rows and read states
  follow **O** by explicit FK. Do not remove protected/held associations.
- A direct thread is eligible only when both participants and every authored
  contribution are operational. A protected/held/unknown contribution retains
  the entire shared conversation and its messages. Detach approved nullable
  references to removed users; remaining users retain read-only archived access.
- A support ticket with only operational requester/assignee/contributors may be
  eligible. A retained participant/contribution keeps the entire thread,
  including its attachments. Detach only approved nullable identity links.
- Batch resources have no trustworthy creator field. Hold batch deletion,
  resources, class sessions and group history until row-level ownership and
  resource retention are approved. Removing eligible enrollments does not imply
  deletion of their batch/program/resources.
- Retain protected/held-host meetings and meetings with shared/unknown content.
  Recordings are manual because their owner is not stored. A retained meeting
  whose operational host cannot be detached (`host_id` is NOT NULL) blocks that
  account's removal. No silent host reassignment.
- Preserve shared notes, messages, other users' attachments and external URLs.
  A file link is not proof the portal owns the external file.
- Audit identity links may detach only where nullable and approved. In
  particular `lab_access_audit_log.admin_id` is NOT NULL: if it references O,
  retain/reclassify that account or approve a separate retention/schema design.
  Never delete the audit event or bypass the FK to clear the account.
- Keep VM rows/tokens/configuration. Removing live grants requires a separate
  decision on rotating the shared Windows student password, remaining grants,
  agent polling, offline/failsafe state and existing RDP sessions. Preserve
  administrator/local protected Windows accounts. None of this is executed now.

The preview intentionally reports partial/held categories. “Fresh operations”
does not imply every business table must be empty at the cost of protected/shared
records. If the intended outcome requires removing held resources, obtain exact
row-level decisions and revise/retest the manifest before a future executor.

## 5. Uploads, external systems and workers

| Reference | Storage/policy |
|---|---|
| `users.photo_path` | `backend/uploads/profile_photos`; protected/held photos retained |
| `support_attachments.storage_reference` | `backend/uploads/support`; shared/retained thread attachments retained |
| `meeting_recordings.file_path` | `backend/uploads/recordings`; recording ownership manually reviewed |
| `file_cleanup_jobs.storage_kind/storage_reference` | Pending deletion instructions; reconcile and hold before restarting cleanup |
| `study_materials.file_url`, `class_videos.video_url`, class/meeting links, settings logo URL | External links/configuration; do not delete external assets through this reset |

Back up the **entire uploads tree** plus any verified mounts/external storage,
not just guessed filenames. File cleanup is deferred: preparation proposes no
unlink, new cleanup jobs, or blanket filesystem deletion. A later cleanup phase
needs a reference manifest, storage-root containment/symlink checks, no remaining
references, a verified backup, human approval and a retry/quarantine policy.
Never invoke existing DELETE endpoints for a bulk reset: some routes unlink
files before DB commit and invoke emails or other side effects.

Background loops started by `app/main.py`:

- VM expiry watchdog (updates grants/audits/password state, may notify users).
- Community email delivery worker (claims rows and can send Graph/SMTP email).
- File cleanup worker (unlinks committed queued files every 30 seconds).

Active sockets, public registration/contact submissions, VM heartbeat/events,
scheduled meeting integrations, Windows agents, Jitsi/Jibri and deployment
automation can also create/change data. Inspect additional systemd timers,
cron jobs, replicas, database jobs, triggers, logical replication and cloud jobs
on the actual installation. The portal maintenance setting only blocks some
logins; it is **not** a complete write freeze and does not stop these workers.

The inauguration subsystem uses Google Apps Script, Google Sheets, Graph ticket
email/PDF generation and timed triggers. Its registrations, ticket files,
deployment/Script Properties and triggers are **outside this PostgreSQL reset**.
Jitsi media/recording services and Windows VM local accounts are also external:
DB rollback cannot restore their state or unsend an email. Do not modify them
without separately scoped authorization.

## 6. Backup, isolated restore and rollback plan

**No approved backup process/destination/encryption evidence was found in this
repository. Selecting them, key recovery and successful restore are mandatory
execution gates. No backup was made in this task.** Never invent an approved
destination. Freeze application/background/filesystem writes in a separately
approved maintenance window before the final coordinated DB/uploads backup.

### Future backup template — not executed

Use installed PostgreSQL client tools compatible with the actual server major
version. The configured service must point to the independently verified target.
Do not display environment values or use a credential-bearing URL. Destination
below must already be an approved encrypted, access-controlled **off-host**
backup destination (or use an approved encrypted transfer/verification process).
The commands alone do not prove encryption, off-host storage or recoverability.

```bash
set -euo pipefail
umask 077
ARCK_BACKUP_BASE=/approved/encrypted-offhost-backups
test -d "$ARCK_BACKUP_BASE"
ARCK_RESET_STAMP=$(date -u +%Y%m%dT%H%M%SZ)
ARCK_RESET_BACKUP=$(mktemp -d "$ARCK_BACKUP_BASE/arckenites-pre-reset.$ARCK_RESET_STAMP.XXXXXX")
PGSERVICE=arckenites_backup_ro pg_dump --format=custom --no-password \
  --lock-wait-timeout=5s --file="$ARCK_RESET_BACKUP/portal.dump"
test -s "$ARCK_RESET_BACKUP/portal.dump"
pg_restore --list "$ARCK_RESET_BACKUP/portal.dump" > "$ARCK_RESET_BACKUP/database.toc"
test -s "$ARCK_RESET_BACKUP/database.toc"
tar --acls --xattrs -C /opt/arckenites/backend -cpf "$ARCK_RESET_BACKUP/uploads.tar" uploads
test -s "$ARCK_RESET_BACKUP/uploads.tar"
tar -tf "$ARCK_RESET_BACKUP/uploads.tar" > "$ARCK_RESET_BACKUP/uploads.toc"
(cd "$ARCK_RESET_BACKUP" && sha256sum portal.dump uploads.tar > SHA256SUMS && sha256sum -c SHA256SUMS)
```

Record exit statuses, sizes, checksums, target fingerprint, schema revision,
deployed SHA, snapshot/freeze time, encryption/key-recovery evidence, backup
location and off-host readback checksum. Protect TOCs/logs (they may reveal
metadata). Check mount coverage; tar does not magically back up external URL
resources. Do not follow arbitrary symlinks or overwrite an old backup.

`pg_dump` custom format supports inspection and selective restore, but a TOC is
not a successful restore test. Database dumps do not cover server-wide roles,
configuration, secrets or upload files. Have the DBA preserve required cluster
roles/grants and infrastructure/secret recovery through the approved protected
process; do not dump role password data into this repo.
[pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html),
[pg_restore](https://www.postgresql.org/docs/current/app-pgrestore.html).

### Future restore rehearsal — isolated target only

Provision a fresh isolated PostgreSQL instance/database with no production
credentials, routing, shared filesystem mounts, mail delivery, VM-agent or
Jitsi access. Have the operator independently verify its endpoint and isolation.
Use a protected service named e.g. `arckenites_restore_rehearsal`; never an alias
that might resolve to production. Database creation is an infrastructure task,
not a command guessed from this repository.

```bash
pg_restore --dbname='service=arckenites_restore_rehearsal' \
  --exit-on-error --single-transaction --no-owner --no-privileges \
  /approved/encrypted-offhost-backups/UNIQUE/portal.dump
```

Restore only into the independently verified **empty non-production database**.
Do not use `--clean`, `--create`, `--disable-triggers` or restore over production.
The owner/privilege overrides are for rehearsal; separately validate real
production ACL/role recovery with the DBA. Inspect archive member paths before
restoring the uploads archive into a fresh staging directory; do not extract
over production or follow existing symlinks. Verify checksums and row/file counts.

First compare a pristine restored copy against the snapshot: protected IDs,
active flags, role/grant/catalog/settings rows, all audit rows and FK integrity.
Do not print password/token values. Preservation is based on excluding these
rows from mutation; any future secret-field equality verification must return
only a boolean inside an approved private comparison, not export the values.

Then rehearse application authentication privately in an isolated application
copy. Use `TestClient` without lifespan for initial checks so restored pending
workers cannot run. A separate startup rehearsal needs denied external egress,
quarantined/read-only staging uploads, and an explicit queue/worker plan.
Do not start the restored app against production paths or let it send old queued
emails. An authorized operator can verify an existing active Super Admin login
without sharing credentials or resetting its password. Test roles/catalog/admin
navigation and mock email first; a real mail test requires an approved test sink.
Google OAuth/Jitsi/VM enforcement require separate isolated integration checks.

No isolated PostgreSQL backup/restore or real credential login was performed in
this task; the included fixtures test preparation behavior only.

### Recovery boundaries

- Before commit: rollback all database mutations; leave files untouched; keep
  write freeze until verification. Failed reset leaves protected and eligible
  rows as before. A lost connection during commit has uncertain outcome: inspect
  operation ID/audit and counts before retrying, never assume rollback.
- After commit but before file cleanup: keep files and backups; evaluate recovery
  on an isolated restored copy first. Restoring production is a new explicitly
  approved incident operation, not an automatic downgrade.
- After any external/file operation: DB restore alone is insufficient; restore
  coordinated file snapshot and separately reconcile Windows/media/email state.

## 7. Future executor design (specification, not implemented or authorized)

An executor can be developed only against an approved complete manifest. It
must have a default dry-run mode and explicit production detection; absence of
verified endpoint/environment means refusal, not a fallback. The present command
always refuses execution, so no combination of backup/approval flags can bypass
preparation-only scope.

Future input contract:

- Source/deployed commit and schema fingerprint/revision; exact target identity.
- Stable O/S/H ID sets, per-admin decisions and exact approved IDs in each table;
  counts plus digest of row IDs/relationships to detect drift.
- Signed approval/change record with authorized current Super Admin actor ID,
  DBA/reviewer, scope, expiry and run ID. Validate against the trusted approval
  system and current DB role; a JSON boolean alone is not approval.
- Verified DB/uploads backup checksums and successful isolated-restore evidence,
  encryption/off-host/recovery evidence. An unverified backup blocks execution.
- Retention/shared-file/VM/recording/queue decisions and effective write freeze.
- Explicit transaction/WAL/disk/lock budget and independent rollback owner.

Transaction specification:

1. Refuse until every gate and every manual blocker is resolved. Re-inventory
   current schema, FKs, triggers, tables, revisions and current role/profile
   classification; reject schema changes rather than guessing new deletions.
2. Acquire a maintenance advisory lock, enforce a real application/integration
   write freeze, and use one PostgreSQL transaction with bounded timeouts. Lock
   the explicit affected tables/rows in a documented stable order; advisory
   locks alone do not stop application writes.
3. Recompute manifest digests/counts under lock and verify protected allowlist,
   active usable Super Admin and `S ∩ O = ∅`. Refuse if any protected/held row is
   in the delete set or retained FK cannot be safely kept/detached.
4. Stage approved nullable detachment for shared/audit links only. Preserve
   settings, catalog, RBAC and Super Admin records exactly. No constraint
   disabling, blanket cascades, database recreation, ID recycling or bootstrap.
5. Execute **explicit ID-bounded operations** in child-before-parent order below;
   every table requires its own reviewed predicate, never “all tables except”.
6. Verify protected rows unchanged, exact affected counts, all retained references
   valid and required audits present **before commit**. Any exception rolls back
   the full transaction. No files, emails or external state changes inside it.
7. Commit once; record transaction outcome/digest in an immutable maintenance
   ledger; recheck postconditions with read-only queries before write access
   resumes. File cleanup is separately approved and recoverable.

There is currently **no `maintenance_reset` audit event** in `AuthEventType`.
Choose and separately review a correct transactional maintenance audit design
(for example an additive enum/event migration before a reset release), and an
external approval/outcome ledger. Do not invent an enum at runtime, overload an
unrelated security event, silently skip audit, or apply a migration during this
preparation. New audit support/revision would require regenerating/reviewing this
tool's pinned revision and schema inventory. Required event: actor/role, run ID,
UTC timestamp, target fingerprint, manifest/backup approval references, counts,
commit/failure outcome; no passwords, tokens, emails or SQL parameter dumps.

### Deterministic dependency order for eligible approved IDs

This is an operation-order specification, **not executable deletion SQL**.

1. Freeze and snapshot; stage retained-reference detachments approved by manifest.
2. Eligible `support_attachments` before `support_messages` before `support_tickets`.
3. Eligible `direct_messages` before `conversations`.
4. Eligible `meeting_recordings`, `meeting_messages`, `meeting_notes`,
   `meeting_participants` before `meetings` (recording/files remain manual gates).
5. Eligible `community_post_email_deliveries` before `community_posts`.
6. Eligible `batch_messages`, `batch_chat_read_state`, `batch_enrollments`,
   `class_sessions`, `study_materials`, `class_videos`; batch-linked meetings must
   already be removed or their batch retained; only then approved `batches`.
7. Eligible `program_enrollments`, `interview_schedules`, `lab_slot_bookings`,
   `lab_access_overrides`, `lab_vm_access` (with approved external access policy).
8. Approved `leads`, `contact_enquiries`, `community_registrations`.
9. Eligible `auth_sessions`, `google_login_tickets`, `user_google_identities`;
   account-scoped revocation and removal must not revoke protected/held accounts.
10. Eligible profiles and finally eligible `users` only. All retained FKs must
    resolve or have their approved nullable links detached first.
11. Audit new maintenance event and verify all preservation/deletion predicates;
    commit. Existing three audit tables are never purged. File jobs/VM inventory,
    configuration tables and migration state remain retained/held.

Do not reset sequences: gaps are normal; IDs and historical references matter.
`nextval` allocation for a new audit may leave a gap after rollback, which is not
a reason to reset a sequence. If one transaction exceeds measured maintenance
budgets, stop and design independently backed-up stages with approved manifests,
checkpoint audits and recovery boundaries. Splitting commits silently would
break all-or-nothing recovery and is not authorized by this plan.

Reserved future execution command (present version intentionally refuses):

```bash
./venv/bin/python -m maintenance.reset_prepare execute
# REFUSED: preparation only; no database is opened and no reset occurs.
```

There is no runnable destructive SQL or enabled reset command in this deliverable.
An approved executor would accept signed manifest/backup attestations; it must
not be enabled just by removing this refusal or passing an “I understand” flag.

## 8. Before/after verification and expected outcomes

Use [reset_checks.sql](backend/maintenance/reset_checks.sql) only after verifying
the PG service target. It contains read-only queries and finishes with rollback:

```bash
PGSERVICE=arckenites_inventory_ro psql -X --no-password --set ON_ERROR_STOP=1 \
  --file maintenance/reset_checks.sql
```

| Verification | Before | Required after a future approved reset |
|---|---|---|
| Target/schema/deployed source | Independently verified, expected migration head and no drift | Same approved identity/revision/source, no unreviewed deployment |
| Super Admin IDs/profiles/credentials/active state | All IDs counted; existing active login privately verified | Exact same records and intended access; existing inactive admins stay inactive |
| Non-Super admins | Individual pending/retain/remove decisions recorded | Exactly those reviewed decisions; no accidental privilege expansion |
| RBAC/programs/settings | Counts, IDs, role grants and private comparison evidence captured | Exact row equality, including all catalog IDs and original settings |
| Eligible operational rows | Approved manifest with per-table counts and IDs | No approved deleted IDs remain; held/protected IDs remain |
| Shared support/chat/meeting/resources | Retained parents/content/file references recorded | Required shared content readable; approved detached identities render safely |
| Audit records | Full historical count and ID set captured | All original event IDs/content remain; nullable detachment only; new maintenance audit accounted for |
| FK integrity | No unexpected/unvalidated constraints; baseline orphan anti-joins zero | Constraints still enabled/validated; anti-joins for all 62 reviewed FKs zero (plus any separately approved production-only FK) |
| Uploads/queue | DB/files backups verified, path/reference manifest approved | Files untouched initially; no unintended queue drain; cleanup only under a later approved file phase |
| VM/media/queues | Freeze and reconciliation decisions recorded | No lingering unauthorized grants/media access; retained inventory/credentials intact; no unintended email replay |
| Application checks | Super Admin login, dashboard, navigation, students pages, program options | Same protected access; eligible lists reset per manifest; mail verified via approved sink |
| Demo accounts | Preexisting demo IDs explicitly classified as operational or held | No newly seeded/recreated demos; no seed run |
| External systems | Inauguration/Graph/Jitsi/Windows boundaries captured | No unintended external changes |

Validated constraints alone do not prove all historical data is sound; generate
read-only left-join anti-orphan checks for **every actual FK**, honoring nullable
and composite semantics. Do not substitute guessed subsets for full integrity
verification. Compare required protected row identities/values privately; counts
alone cannot prove preservation. Post-reset auth checks can append legitimate
audit rows, so distinguish verification activity from reset changes.

## 9. Tests, remaining risks and required approvals

Preparation tests use a fresh SQLite database with FK enforcement; only fixture
creation writes are performed. **All 13 preparation tests passed** in a fresh
isolated run on 17 October 2026 after the catalog-inventory update (23.687 seconds).
The current ORM metadata was also compared with the checked-in source inventory:
45 tables, 62 FK edges, 58 model indexes, 124 model constraints,
matching table names and FK declarations, with no unclassified model tables.
The local `alembic heads` command reported `d7f9b3c5e2a1`; live `alembic current`
remains unavailable.
Preview functions issue reads only, preserve the
fixture rows, identify multiple Super Admins by slug at role ID 17 (not hardcoded
ID 1), hold other admins, reject inconsistent profiles, preserve shared threads,
flag settings/audit FK conflicts and cleanup/VM/media holds, refuse ambiguous
targets, prevent report overwrite/live evidence in Git, and refuse execution
before any connection. No reset-execution or PostgreSQL restore test was run.

The read-only verification SQL was inspected for target, migration, protected
identity, catalog, worker, audit, FK, constraint, index, trigger and sequence
checks. A static statement scan found only `BEGIN`, `SET`, `SELECT` and
`ROLLBACK`. It has not
been parsed by a PostgreSQL server or run against production. A fresh read-only
GitHub Actions query still showed run 39031264614 as the latest completed run
for `a249de1`; the workflow's success does not establish the current database
state or prove which code is serving at the time of execution.

Limitations: PostgreSQL catalog SQL, live target identity, actual row counts,
backup/restore, cluster privileges, full application authentication, production
trigger behavior, external enforcement and a reset executor are **unverified**.
SQLite preparation tests are not proof that a production reset is safe.

Before execution can be considered, resolve:

1. Production schema/inventory and independent endpoint verification.
2. Every other administrator's retain/remove decision; operational anomalies.
3. Ownership/retention of batches, resources, leads, recordings, already-detached
   history and mixed records. Keep security retention separate from “empty portal”.
4. Approved encrypted off-host backups, secret recovery and successful isolated
   PostgreSQL/uploads restore; access-controlled staging for sensitive restored data.
5. Real write-freeze procedure and its rollback, all workers/replicas/agents,
   queued file deletion/email replay, live VM/Jitsi access and external ownership.
6. Correct maintenance auditing, source/schema pinning and signed authorization.
7. A separately implemented/reviewed executor, destructive tests **only** on a
   disposable restored database, measured WAL/space/lock budgets and recovery owner.

Do not commit/push these artifacts, run deployment/seed scripts, restart
production, edit environment variables or invoke destructive endpoints as part
of preparation. **No production data was deleted and no deployment was performed.**
