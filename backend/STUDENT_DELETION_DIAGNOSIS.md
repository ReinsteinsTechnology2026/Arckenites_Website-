# Live student deletion diagnosis — 17 October 2026

## Finding

**The main Delete button was connected to the old safe-deactivation endpoint,
even for Super Admin.** Commit `a526761` added a separate Permanent delete button
but left the original trash-can Delete action unchanged. Clicking the original
button therefore continued to deactivate students with related records. The
reported message is the expected response from that original endpoint.

This is a frontend integration error. The new backend action is a separate
route and does not fall back to safe deactivation. A missing migration would
cause an error/rollback on that new route, not this deactivation response.

## Evidence and access limits

At the start of diagnosis the working tree was clean, on `main` at
`a526761dfde4f90ae6f3263145f64428b551a977` (“Super admin all access”). There was
no uncommitted implementation waiting to be pushed. That commit contains the
deletion service, authorization, migration, upload cleanup and session changes.

Read-only public requests established:

- The production page references `js/admin-students.js?v=3`.
- Both that exact URL and a fresh diagnostic URL returned the implementation
  from `a526761`, including **both** old and permanent actions. After normalizing
  CRLF to LF, its SHA-256 matches the local pre-fix file:
  `eec9266cc8469dbd4e2570a633e66a51fe2f1eec3a9cde167031280cfd893d7d`.
- The JavaScript response has `Cache-Control: max-age=14400` (four hours).
  The exact `?v=3` request returned Cloudflare `MISS`. This establishes what the
  public server returned to this diagnostic client; it does not inspect the
  user's browser cache or stored role snapshot. An older browser copy remains
  possible, but it is unnecessary to explain the observed behavior: the
  deployed current file itself still routes Delete to the old endpoint.
- Public API introspection was unavailable: `/api/openapi.json` returned 404;
  `/openapi.json` and a subsequent `/api/health` request returned 403. These
  results do not establish backend revision or endpoint absence. No
  authenticated production request or deletion was attempted.

GitHub API/CLI independently show deployment run
[39030254798](https://github.com/ReinsteinsTechnology2026/Arckenites_Website-/actions/runs/39030254798)
completed successfully for `a526761`. Its log records:

- Run created at **17:21:00 IST** (11:51:00 UTC).
- Application and frontend resets to `a526761` at approximately 17:21:12 IST.
- Alembic upgrade `c6e8a2b4d9f1 → d7f9b3c5e2a1` at 17:21:14 IST.
- Backend restart and successful health checks; deployment finished at
  approximately **17:21:22 IST**.

This is deployment-log evidence, not direct inspection of the current server
process, current database revision, Caddy root, or an authenticated browser.
No SSH session or production database access was used. Nothing was deployed,
no real student was deleted, and no production migration was run in diagnosis.

## Request trace

| Stage | Original main Delete | Permanent deletion |
|---|---|---|
| Frontend | `admin-students.js`: `data-action="delete"` | `data-action="permanent-delete"` |
| Confirmation | Ordinary `window.confirm` | Exact login-ID prompt plus irreversible confirmation |
| API client | `ArckAPI.request('/admin/students/{id}', {method: 'DELETE'})` | `POST /admin/students/{id}/permanent-delete` with both confirmation fields |
| Production URL | `DELETE /api/admin/students/{id}` | `POST /api/admin/students/{id}/permanent-delete` |
| Backend | `routes_admin_students.delete_student` | `routes_admin_students.permanently_delete_student` |
| Authorization | `require_permission('students.delete')` | `require_super_admin`, using current DB user and existing Super Admin slug |
| Dependencies | `_student_has_related_records` | `stage_student_deletion` in `core/student_deletion.py` |
| Related history | Sets inactive and revokes sessions | Deletes owned rows, detaches shared identity links, deletes account and audits in one transaction |

The exact old alert is displayed in the `data-action === 'delete'` handler:

```javascript
window.alert(result.detail || 'Student had related records, so the account was deactivated instead of deleted.');
```

Its long message comes from `DeleteStudentResponse(action="deactivated", ...)`
in `routes_admin_students.py`, not the permanent-deletion service. The original
backend route has no Super Admin branch that redirects it to that service.
The separate permanent route invokes `stage_student_deletion` and returns
`action="permanently_deleted"`; it never calls `_student_has_related_records`.

## Local correction

- `arckenites-orange/js/admin-students.js`: Super Admin's main trash-can Delete
  now renders `data-action="permanent-delete"` and an explicit permanent-delete
  title/accessibility label. It reuses the existing typed-confirmation handler.
  The redundant separate permanent-delete button is removed. Regular Admin's
  trash-can action still renders `delete` and uses existing permissions/policy.
- `arckenites-orange/admin-students.html`: script version increased to `v=4`
  to request a fresh asset after release.
- `backend/tests/test_student_permanent_deletion.py`: isolated HTTP/ORM tests
  cover the permanent backend route and transaction policy.
- `backend/tests/admin_program_ui.test.cjs`: tests derive the main Delete action
  from actual rendered markup, then execute the real handler. They check typed
  confirmation, endpoint/method, cancellation, errors, no fallback, and regular
  Admin behavior.
- `backend/STUDENT_PERMANENT_DELETION.md`: operating guide updated.

The backend deletion policy and production deployment scripts were not changed
by this correction. No additional migration is required for this frontend fix.

## Deployment workflow review

`.github/workflows/deploy.yml` runs automatically on a push to `main`, or by
manual dispatch, on the self-hosted runner. It copies the **existing server's**
`/opt/arckenites/deploy.sh` to `/tmp/deploy.sh` and runs the copy. It does not
check out the workflow's triggering commit or obtain a fresh deployment script
before starting. A deployment-script change may therefore need a separately
reviewed bootstrap; the present correction does not alter that script.

`deploy.sh` defaults to:

| Item | Configured path/value |
|---|---|
| Application | `/opt/arckenites` |
| Frontend | `/opt/arckenites/arckenites-orange` |
| Backend | `/opt/arckenites/backend` |
| Python environment | `/opt/arckenites/backend/venv/bin` |
| Service | `arckenites-backend` |

It fetches `origin/main` in both application and frontend directories, stops the
backend, resets each location to its fetched tip, installs requirements,
upgrades Alembic, syncs RBAC only, restarts, and checks health. Git commands in
the frontend subdirectory may resolve to the application repository rather
than a separate checkout. The log's two resets to the same SHA are consistent
with the expected source, but do not prove the actual static-server root.

Operational limitations:

- It deploys fetched `origin/main`, not necessarily the triggering workflow SHA
  if another push arrives. Avoid overlapping releases and check logged SHA.
- If both paths resolve to one Git root and `main` changes between the two
  fetches, the second reset can replace the first reset's code while the final
  application summary still prints the first SHA. Both logged resets in the
  inspected run are `a526761`, so this did not explain this incident. Verify
  actual checkout SHAs after a future release, not just the summary line.
- It assumes those directories can be reset safely; check server modifications
  and backups before release. Do not blindly run extra `git reset --hard` commands.
- Public health is liveness only; it does not prove DB readiness or a deletion.
- There is no test gate in the current workflow. Run the isolated tests before
  approving a merge. Deployment-script pinning/CI improvements are separate work.

## Migration requirements

The permanent implementation requires **`d7f9b3c5e2a1`**, after
`c6e8a2b4d9f1`. It makes eight shared-history identity columns nullable and
creates `file_cleanup_jobs`; it does not remove historical rows or FK constraints.
The successful deployment log records this upgrade. Directly confirm current
revision/schema on the server before relying on it. Do not downgrade or disable
constraints. The original deactivation response is not evidence of a missing
migration because that path never runs the permanent service.

## Tests and confidence

| Check | Result |
|---|---|
| Full isolated Python suite | **156 tests: 142 passed, 14 skipped, zero failures** (270.113 seconds). Includes all 18 new permanent-deletion tests. |
| Skipped cases | Existing live PostgreSQL integration tests; the configured test URL deliberately points to unused local port 1. |
| Simulated-DOM JavaScript regressions | Passed approved-program cases plus Super Admin/regular Admin Delete routing, typed confirmation, cancellation, failure recovery and no fallback. |
| Negative control against `a526761` JavaScript | Failed as expected: actual main-button action `delete`, expected `permanent-delete`. Confirms the new test detects this exact defect. |
| JavaScript syntax and `git diff --check` | Passed. |

Temporary SQLite fixtures enforce FKs and use test-only timezone/JSONB adaptations. They
prove HTTP authorization, data retention and rollback under those conditions;
they do not prove PostgreSQL row-lock concurrency, live migration execution,
real Windows enforcement, or real-browser behavior.

The new cases cover owned records, preserved support/chat/meeting content and
other users' attachments/resources, detached security/lab audit links, actor/
target/timestamp audit fields, auth sessions, cleanup retries/shared references,
regular Admin with a delete grant plus forged role fields, student/staff/anonymous
callers, revoked Super Admin sessions, exact confirmation, missing/nonstudent
targets, shared-ownership blockers, repeated deletion, password reset/revocation,
audit failure, commit failure, and an actual additional FK preventing final
account deletion after other changes have been staged. Failed operations leave
the account active, owned/shared rows, files, session state and VM password intact.

Reproduce safely from `backend/` (never point old integration tests at production):

```powershell
$env:DATABASE_URL='postgresql+psycopg://unused:unused@127.0.0.1:1/unused?connect_timeout=1'
$env:JWT_SECRET='student-tests-only-secret-not-for-production'
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

From the repository root:

```powershell
node --check arckenites-orange/js/admin-students.js
node backend/tests/admin_program_ui.test.cjs
git diff --check
```

## Safest production release steps — operator instructions, not executed

1. Review this diagnosis and the diff, run isolated tests, and retain coordinated
   database/uploads backups. Keep the release separate from unrelated changes.
2. Create a review branch and stage only these files:

   ```powershell
   git switch -c fix/super-admin-delete-routing
   git add -- arckenites-orange/admin-students.html arckenites-orange/js/admin-students.js backend/tests/admin_program_ui.test.cjs backend/tests/test_student_permanent_deletion.py backend/STUDENT_PERMANENT_DELETION.md backend/STUDENT_DELETION_DIAGNOSIS.md
   git diff --cached --stat
   git diff --cached
   git commit -m "Route Super Admin Delete through confirmed permanent deletion"
   git push -u origin fix/super-admin-delete-routing
   ```

   Open a PR to `main` for review. Pushing this feature branch does not match the
   current deploy workflow trigger. **Merging/pushing to `main` deploys
   automatically**, so merge only after release approval. No force push or
   production reset is needed.
3. After the approved merge, record the merged commit and inspect its run:

   ```powershell
   git ls-remote origin refs/heads/main
   gh run list --repo ReinsteinsTechnology2026/Arckenites_Website- --workflow deploy.yml --limit 5 --json databaseId,headSha,status,conclusion,url
   gh run view <RUN_ID> --repo ReinsteinsTechnology2026/Arckenites_Website- --log
   ```

   Verify the run's head SHA **and its logged Application SHA** equal the reviewed
   release. If no push-triggered run exists, manual dispatch is available only
   after approval and verification of `main`:

   ```powershell
   gh workflow run deploy.yml --repo ReinsteinsTechnology2026/Arckenites_Website- --ref main
   ```

   Do not trigger a duplicate deployment while one is running. Avoid another
   push during this release because the script follows `origin/main`.
4. On the server, use read-only checks to confirm paths, code, service and schema:

   ```bash
   git -C /opt/arckenites status --short
   git -C /opt/arckenites rev-parse HEAD
   git -C /opt/arckenites/arckenites-orange rev-parse --show-toplevel
   git -C /opt/arckenites/arckenites-orange rev-parse HEAD
   systemctl show arckenites-backend -p WorkingDirectory -p FragmentPath -p MainPID
   systemctl is-active arckenites-backend
   cd /opt/arckenites/backend
   ./venv/bin/alembic current
   ./venv/bin/alembic heads
   ```

   Expected migration head for this release: `d7f9b3c5e2a1`. Confirm Caddy's
   configured static root really serves the expected frontend directory and
   its `/api` proxy reaches this backend service. Inspect locally without
   sharing environment secrets. If any path/revision differs, resolve it before
   considering the release successful. No new migration is needed by this fix.
5. Compare served assets with reviewed files and inspect backend schema locally:

   ```bash
   sha256sum /opt/arckenites/arckenites-orange/js/admin-students.js
   curl -fsS 'https://arckenites.com/js/admin-students.js?v=4' | sha256sum
   curl -fsS 'https://arckenites.com/admin-students.html?view=database' | rg 'admin-students.js'
   curl -fsS 'http://127.0.0.1:8000/openapi.json' | ./venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["paths"]["/api/admin/students/{student_id}/permanent-delete"].keys())'
   ```

   The asset hashes should match, the page should reference `v=4`, and the
   backend schema should list `post`. Investigate cache/CDN/static-root mismatch
   if they do not. Do not assume a health response verifies this route.
6. In the real browser, disable cache in DevTools, reload, and sign in again to
   refresh the role snapshot. Verify `/auth/me` (API client `/api/auth/me`) shows
   `admin_role.slug = super_admin`. Click Delete and **cancel the typed prompt**:
   this verifies the corrected entry point without modifying any production
   record. Cancellation must send no deletion request. Regular Admin should
   retain its existing safe behavior.
7. Prove a complete permanent deletion only on staging with a disposable test
   student containing related records. DevTools must show
   `POST /api/admin/students/{id}/permanent-delete`, never the old DELETE or an
   automatic fallback. Check disappearance of the account, audit, retained
   shared content and eventual cleanup. Do not use a real production student
   as a diagnostic test.
