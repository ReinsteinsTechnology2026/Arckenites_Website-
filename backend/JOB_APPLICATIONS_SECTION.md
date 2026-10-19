# Job Applications section control

The section switch lives in **Admin Portal → Settings → Job Applications**. An
admin needs the explicit `job_applications.section_manage` permission to see or
change it. Existing `settings.view` and `settings.edit` grants do not confer
this permission. The repository has no built-in Batch Admin role; a Super Admin
can assign the new permission to a custom Batch Admin role through the existing
Roles & Permissions UI after `python seed.py --rbac-only` synchronizes the
permission catalog in the approved deployment process. No default
admin role receives the new grant.

The new `system_settings.job_applications_section_enabled` field defaults to
`true` on migration so existing student access continues until an authorized
admin disables it. The value is stored in PostgreSQL and is independent of each
announcement's publication and enabled status. Disabling the section denies
student list, detail, and reaction requests; it retains all posts and reactions.
Student navigation is shown only after the authenticated availability check.

Published, enabled posts with a deadline before the server's current date are
hidden from student endpoints. They remain visible to authorized admins. The
earlier Job Applications specification requires archiving and preserving
history, and defines no deadline-based deletion schedule, so this change does
not delete expired posts or reactions.
