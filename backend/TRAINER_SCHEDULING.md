# Trainer scheduling: deployment and rollback notes

Migrations (in order, on top of `c365dd2994ef`):

1. `9c1e5a7b3d20` adds six values to the `auth_event_type` enum.
2. `9d2f6b8c4e31` creates `trainer_availability_rules`, `trainer_availability_entries`,
   `trainer_expertise`, `batch_trainer_assignments`, `scheduling_notices` and inserts the
   `scheduling.view|manage|override` permissions (granted to the `admin` role; `support_admin`
   gets view; override is Super Admin only). No existing table or column is altered.

Rollback (`alembic downgrade -2`) is safe for the previous application version but
**permanently deletes all availability, expertise, assignment history and scheduling notices**,
and removes the three permission rows and their grants. Batch `trainer_id` values are untouched.
Postgres cannot drop enum values, so the six `auth_event_type` values remain in the type, but the
downgrade first re-labels any audit rows using them (`batch_updated` / `trainer_updated`; the detail,
module and target columns keep the real meaning) so the previous application can still load the log.
Re-upgrading (`alembic upgrade head`) creates the scheduling schema again, empty, but does **not**
restore the original event types on audit rows that were relabeled: they stay `batch_updated` /
`trainer_updated`. Only the `detail`, `module` and `target` text still identifies them as scheduling events.

## Account deletion

- **Trainer:** availability rules, availability entries, expertise and the trainer's scheduling
  notices are deleted together with the account. Notices the trainer merely created or received
  elsewhere are detached from them.
- **Assignment history is retained** (`batch_trainer_assignments`; its user columns are
  `ON DELETE SET NULL`). Deleting a trainer or an admin who appears in it is refused with a 409
  ("has related records"); deactivate the account instead.
- **Admin:** the existing admin delete refuses on any related record, so besides assignment history,
  an admin who created a scheduling notice (for example an availability request) is also refused and
  must be deactivated.
- Whether history is kept or removed in an authorized production reset is governed by the existing
  reset and retention policy (`backend/maintenance/reset_policy.py`, `PRODUCTION_RESET_PLAN.md`):
  it is held for manual review and the retention decision is still open.

Configuration (all optional): `SCHEDULING_TIMEZONE` (default Asia/Kolkata),
`SCHEDULING_MAX_WEEKLY_HOURS` (40), `SCHEDULING_DEFAULT_SESSION_MINUTES` (60).

Scheduling rules also guard the existing batch create/update and class-session
create/update routes (trainer overlap and leave/unavailable time, no override).
