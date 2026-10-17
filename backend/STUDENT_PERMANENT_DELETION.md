# Permanent student deletion

## Authorization and confirmation

Super Admin is identified using `RoleEnum.admin` and the existing
`SUPER_ADMIN_SLUG`, never an account name, email, or numeric user ID.
The same predicate is used by the existing permission helpers and the new
`require_super_admin` dependency. Regular Admin grants are unchanged.

In Admin → Students, Super Admin has a separate **Permanent delete** action.
It requires typing the exact login ID and confirming the irreversible action.
The existing delete/deactivate action retains its original behavior.
Create, edit, activation/deactivation and password reset retain existing RBAC.
Password resets now revoke existing sessions. Super Admin also has a standalone
**Revoke all sessions** action, without changing the password or active status.

`POST /api/admin/students/{student_id}/permanent-delete`

```json
{
  "confirm_username": "student@example.com",
  "confirmation": "PERMANENTLY DELETE"
}
```

The backend verifies authentication, current Super Admin role, target role
`student`, and exact confirmation. Ordinary admins cannot access this action
even if they have `students.delete`. Nonstudent accounts cannot be removed
through it. A successful response has `action: "permanently_deleted"`, target
ID, affected row counts, and `upload_cleanup: "queued"`. A second request for
the removed account returns 404. There is no fallback to deactivation.

## Approved retention policy

The user selected preservation of shared history with identity detached.

| Records | Policy |
|---|---|
| User and student profile | Delete. Any other profile keyed to this same user is also exclusive and deleted. |
| Authentication sessions, Google identity links and login exchange tickets | Revoke sessions, then delete credentials/session rows. |
| Program/batch enrollments, interview appointments, lab bookings, lab overrides, VM grants, batch read state, email delivery rows for this user | Delete student-owned rows. |
| Programs, batches, trainers, learning materials/videos, class schedules, VM inventory | Preserve. No parent cascade deletion. |
| Support tickets and all participants' messages | Preserve; detach requester/sender/assignee links to the student. Admin views and exports display “Deleted student.” Notifications to a removed requester are skipped. |
| Support attachments uploaded in messages authored by the student, profile photo | Delete metadata and queue exclusive physical files for cleanup. A storage reference used by another message/account is retained. Other users' attachments remain. |
| Direct conversations/messages | Preserve; detach participant/sender links. A surviving participant can open archived history read only. The history endpoint checks conversation membership, not just the supplied ID. |
| Batch messages | Preserve with sender detached and fallback display label. Unread calculations still include detached messages. |
| Meetings, recordings, notes and messages | Preserve. Detach student participant/message/note-author links. Active or pending participation becomes removed with a departure timestamp; prior completed participation states remain. |
| Security, lab and VM audit events | Preserve event content and detach student identity links. Text already present in messages/audits, including account identifiers, is retained; this is not a personal-data erasure tool. |
| Nullable attribution links in leads, settings, other lab grants/overrides or batches | Detach only references to this account; retain the shared record. |

The deletion service inspects every mapped FK to `users.id`. Unhandled
dependencies fail with 409 and roll back. For example, a former staff/admin
account now marked student might still be a meeting host, community-post author,
lab audit admin, or grant issuer. Such nonstudent ownership must be reassigned
before removal; the error identifies the relevant table and column. No shared
ownership is silently transferred to the actor. Database constraints also
protect dependencies introduced outside the ORM inventory.

## Transaction and access behavior

The route locks the target user, stages dependency changes, file cleanup jobs,
account deletion and the `student_deleted` audit event, then commits once.
The audit contains actor identity/role, timestamp, target ID/login, and permanent
action. Database errors roll back the whole change and return a sanitized
failure; exception logs contain only target ID and error type.

VM inventory remains intact. For a deleted student's active VM grant, the
rotating Windows password is changed within the transaction. Any other active
grant using that shared VM receives a matching encrypted password snapshot.
The agent enforces the new desired state on its next heartbeat; account removal
does not prove an offline VM applied it, or terminate a Jitsi media session.

Portal HTTP authentication rejects missing users and deleted sessions. Existing
chat WebSockets revalidate account/session state before processing messages,
before each delivery, and on a 15-second idle check. Message sends lock user rows
to serialize against deletion. The existing in-process socket registry remains;
each delivery checks durable database state even in a different API process.

## File cleanup and migration

Migration **`d7f9b3c5e2a1`**, following `c6e8a2b4d9f1`, makes eight retained-history
FK columns nullable and adds `file_cleanup_jobs`. It deletes no existing data.
Apply the migration before starting this backend version:

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m alembic upgrade head
```

This is an operating command, not a migration executed during implementation.
The existing production deployment script already runs Alembic before restart.

The API startup launches a cleanup loop every 30 seconds. It claims at most 100
jobs with `FOR UPDATE SKIP LOCKED`, checks remaining file references, validates
storage-root containment, and unlinks only individual exclusive files. Missing
files are a successful no-op. Failures retain the job for retry; failed jobs
rotate behind unattempted jobs. Logs identify the job ID without disclosing paths.
Database rollback cannot unlink a file because the worker sees only committed
jobs. File removal is eventual and separate from account-removal success.

To inspect pending cleanup, use a read-only database query:

```sql
SELECT id, storage_kind, created_at, last_attempt_at
FROM file_cleanup_jobs
ORDER BY created_at;
```

Investigate repeated failures and storage permissions; never manually remove
arbitrary paths. Keep database and uploads backups coordinated. Downgrade
refuses pending cleanup or detached history rather than deleting records or
inventing replacement users.

## Implementation status

Existing deletion regression tests were inspected. No tests were added or run
for this change. The migration, browser interactions, live PostgreSQL rollback
behavior, and external VM/media enforcement have not been verified. No real
student account was deleted, no migration was applied, and this change has not
been pushed or deployed.

When validation is requested, use an isolated database to cover: Super Admin vs
regular Admin/student/staff authorization; confirmation errors; absent/nonstudent
targets; mixed shared support/chat/meeting history; every FK group; rollback on
dependency/audit/commit failure; owned vs shared uploads and retry failures;
revoked HTTP and existing sockets; other students' resources/access; and the
unchanged default delete/deactivate behavior. Do not run the old PostgreSQL
integration fixtures against production.
