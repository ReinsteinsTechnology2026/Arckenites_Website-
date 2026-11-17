# Portal file storage for production commit abcb5c9

This update is based on abcb5c9a67ec1f82099c4cb211c26a07b6f095a3, the commit reported by the live server. It supersedes the earlier uploaded-project storage ZIP, which was based on a different/newer code tree. Do not install that earlier ZIP over production.

## Scope

- Add private uploaded study-material collections to batch workspaces and enrolled students' Materials panels.
- Add uploaded MP4 class recordings to batch workspaces and students' Class Videos panels. These are recordings trainers/admins already have as files; this does not capture or automatically record live classes.
- Add private conversation-level document collections to direct chat and batch chat, including read access to archived conversations for remaining participants.
- Store new support-ticket attachments in the same library when portal cloud storage is enabled. Existing local attachments and existing external material/video links continue working.
- Keep metadata, messages, memberships and permissions in PostgreSQL. No database backup, email transport, tenant grant or existing server-storage configuration is changed.
- PDF, Word, text and supported image documents: 10 MB maximum with content signatures checked. MP4 recordings: 250 MB maximum, bounded temporary-disk staging and MP4 container-header validation (not a full media decode).
- Files are verified by size and QuickXorHash before publication. No storage share links or Microsoft credentials are exposed to portal users. Every download and recording range request rechecks current access.
- Removing a file immediately blocks portal access. Durable background cleanup removes its remote object and retries failures. Failed/abandoned uploads and files whose owning resource was deleted are cleaned up, with a 24-hour grace period for active/ready uploads.

Existing profile photos and legacy Jitsi meeting recordings keep their current storage behavior. Existing files/links are not automatically migrated. Live class-capture infrastructure is outside this update. A downloaded or recorded copy cannot be revoked after a user has already received it.

## Schema and deployment

One additive migration: 9d2f6b8c4e31 -> c6d8a2f4e107. It creates portal_files and its scope index. It does not change existing tables, run feedback/recording branches, migrate old files or delete data. Do not downgrade production: that would discard new file metadata without deleting cloud objects.

Review the draft PR and require its CI quality job to pass before merging. This PR adds pull_request testing to the existing workflow, whose deployment job remains restricted to pushes to main. Merging into main will run the existing deployment workflow; opening/testing the draft PR does not deploy. That workflow performs the existing backup and migration steps before restart. Keep .env and persistent upload/backup directories outside source control.

New uploads remain OFF by default. No restart, migration or change to the live environment was performed while preparing this code.

After the reviewed code is deployed, from /opt/arckenites/backend:

```bash
./venv/bin/alembic current
./venv/bin/python -m maintenance.check_portal_storage
./venv/bin/python -m maintenance.check_portal_storage --write-test
```

Expected migration: c6d8a2f4e107. The first storage check is read-only. The explicit --write-test creates one UUID-named text file in ArckenitesPortalFiles/Diagnostics, exercises the real resumable-upload/hash-verification client, checks its downloaded contents, and removes only that test file. It runs while portal uploads are disabled. Do not share Microsoft access tokens or .env contents.

## Configuration after successful checks

Keep the existing Graph credentials and verified destination drive ID. Add these to the live .env, initially false:

```dotenv
PORTAL_ONEDRIVE_ENABLED=false
PORTAL_ONEDRIVE_FOLDER_PATH=ArckenitesPortalFiles
ONEDRIVE_DRIVE_ID=b!2CscVGVKB0WYcHyiRFlQAZVWFxoksi5LiCtqWMhMNEKaWYDURdEHQblNr0X_UITr
```

After the code, migration and live resumable-client check succeed, change only PORTAL_ONEDRIVE_ENABLED=true and restart arckenites-backend.service in the agreed deployment window. Keep the earlier ONEDRIVE_UPLOAD_ENABLED=false and RECORDING_ENABLED=false entries unchanged; they do not activate live capture in this version. Notes, uploaded recordings, chat files and new support attachments use the independent portal switch.

Folders Materials, Recordings, Chats, BatchChat and Support are created beneath ArckenitesPortalFiles as needed. Diagnostics is for the explicit test file. The SharePoint site/library grant was already completed separately.

## Live acceptance checks

Use a trainer/admin and an enrolled student: upload a document and a real playable MP4; download the document and seek within the video. Confirm another batch and an unauthenticated browser are denied. Remove enrollment and confirm previously issued playback links stop working. Verify direct-chat participants, batch-chat members and support-ticket ownership/internal-note restrictions. Test deletion, failed uploads, long filenames and a phone/tablet.

The proxy in front of FastAPI must allow intended multipart sizes (up to 250 MB plus multipart overhead for recordings) and sufficient upload duration. Set a bounded request-size limit there rather than accepting arbitrarily large bodies. Never expose backend upload directories as public static files.

Disabling PORTAL_ONEDRIVE_ENABLED stops new document uploads while existing cloud files remain readable through authorization checks; support attachments revert to the existing local-upload path. It does not change backup storage.

## Verification performed while preparing

- 59 targeted backend regression tests passed against isolated SQLite and simulated cloud storage, including 16 file-storage tests. No production SQL, files or Microsoft APIs were used by those tests.
- Generated and inspected PostgreSQL migration SQL for exactly 9d2f6b8c4e31:c6d8a2f4e107. Real PostgreSQL migration/recovery and the full backend suite are assigned to the PR's disposable PostgreSQL CI job.
- Frontend JavaScript syntax checks passed; all six frontend contract checks required by the existing CI passed.
- Shared-file widget load/upload/refresh/removal/long-name rendering checked in headless Chromium with fake API responses at phone through desktop widths.
- An optional pre-existing portal_login_ui.test.cjs check still fails because it expects a Staff Login link/text that already differs in the unmodified live login.html; this PR does not change login.html. It is not one of the existing CI frontend checks.

Still needed: live portal end-to-end tests, actual Microsoft resumable uploads/QuickXorHash validation, physical devices, Safari/Firefox and real browser zoom. The user's earlier simple Microsoft upload/download/delete test proves the site's app grant, not these new runtime workflows.
