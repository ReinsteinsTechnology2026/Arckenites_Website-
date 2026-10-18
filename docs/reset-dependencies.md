# Reset source table and foreign-key inventory

Repository commit: `a249de1c664721da55cb11cd52a95a1cf7609d5b`. **45 ORM tables; 62 FK edges; 58 model indexes; 124 model constraints.** The index and constraint details are in [reset-source-inventory.json](reset-source-inventory.json).

Source declarations only. Production tables, counts, triggers and sequence state are unverified. `alembic_version` is an additional migration-management table; runtime/extension tables may also exist.

| Table | Draft classification | Outgoing foreign keys (`?` = nullable) |
|---|---|---|
| `admin_profiles` | mixed/row-policy | `user_id` -> `users.id` |
| `admin_role_permissions` | preserve | `admin_role_id` -> `admin_roles.id`; `permission_key` -> `permissions.key` |
| `admin_roles` | preserve | None |
| `auth_audit_log` | audit-retain | `user_id?` -> `users.id` |
| `auth_sessions` | mixed/row-policy | `user_id` -> `users.id` |
| `batch_chat_read_state` | mixed/row-policy | `batch_id` -> `batches.id`; `user_id` -> `users.id` |
| `batch_enrollments` | mixed/row-policy | `batch_id` -> `batches.id`; `student_id` -> `users.id` |
| `batch_messages` | mixed/row-policy | `batch_id` -> `batches.id`; `sender_id?` -> `users.id` |
| `batches` | manual-review | `program_id?` -> `programs.id`; `trainer_id?` -> `users.id` |
| `class_sessions` | manual-review | `batch_id` -> `batches.id` |
| `class_videos` | manual-review | `batch_id` -> `batches.id` |
| `community_post_email_deliveries` | mixed/row-policy | `post_id` -> `community_posts.id` (CASCADE); `user_id` -> `users.id` |
| `community_posts` | mixed/row-policy | `created_by_id` -> `users.id` |
| `community_profiles` | mixed/row-policy | `user_id` -> `users.id` |
| `community_registrations` | mixed/row-policy | None |
| `contact_enquiries` | mixed/row-policy | None |
| `conversations` | mixed/row-policy | `user_a_id?` -> `users.id`; `user_b_id?` -> `users.id` |
| `direct_messages` | mixed/row-policy | `conversation_id` -> `conversations.id`; `sender_id?` -> `users.id` |
| `file_cleanup_jobs` | preserve | None |
| `google_login_tickets` | mixed/row-policy | `user_id` -> `users.id` |
| `interview_schedules` | mixed/row-policy | `student_id` -> `users.id` |
| `lab_access_audit_log` | audit-retain | `admin_id` -> `users.id`; `student_id?` -> `users.id` |
| `lab_access_overrides` | mixed/row-policy | `student_id` -> `users.id`; `updated_by?` -> `users.id` |
| `lab_slot_bookings` | mixed/row-policy | `student_id` -> `users.id` |
| `lab_vm_access` | mixed/row-policy | `granted_by` -> `users.id`; `revoked_by?` -> `users.id`; `student_id` -> `users.id`; `vm_id` -> `lab_vms.id` |
| `lab_vm_access_audit_log` | audit-retain | `performed_by?` -> `users.id`; `student_id?` -> `users.id`; `vm_id?` -> `lab_vms.id` |
| `lab_vms` | preserve | None |
| `leads` | manual-review | `created_by_id?` -> `users.id` |
| `meeting_messages` | mixed/row-policy | `meeting_id` -> `meetings.id`; `user_id?` -> `users.id` |
| `meeting_notes` | mixed/row-policy | `meeting_id` -> `meetings.id`; `updated_by?` -> `users.id` |
| `meeting_participants` | mixed/row-policy | `meeting_id` -> `meetings.id`; `user_id?` -> `users.id` |
| `meeting_recordings` | mixed/row-policy | `meeting_id` -> `meetings.id` |
| `meetings` | mixed/row-policy | `batch_id?` -> `batches.id`; `host_id` -> `users.id` |
| `permissions` | preserve | None |
| `program_enrollments` | mixed/row-policy | `program_id` -> `programs.id`; `student_id` -> `users.id` |
| `programs` | preserve | None |
| `staff_profiles` | mixed/row-policy | `user_id` -> `users.id` |
| `student_profiles` | mixed/row-policy | `program_id?` -> `programs.id`; `user_id` -> `users.id` |
| `study_materials` | manual-review | `batch_id` -> `batches.id` |
| `support_attachments` | mixed/row-policy | `message_id` -> `support_messages.id`; `ticket_id` -> `support_tickets.id` |
| `support_messages` | mixed/row-policy | `sender_id?` -> `users.id`; `ticket_id` -> `support_tickets.id` |
| `support_tickets` | mixed/row-policy | `assigned_to?` -> `users.id`; `requester_id?` -> `users.id` |
| `system_settings` | preserve | `updated_by_id?` -> `users.id` |
| `user_google_identities` | mixed/row-policy | `user_id` -> `users.id` |
| `users` | mixed/row-policy | `admin_role_id?` -> `admin_roles.id` |

Categories describe review policy, not permission to delete a whole table. Mixed tables require the row-level rules in the reset plan. `file_cleanup_jobs` and `lab_vms` are held pending worker/VM approval; mandatory configuration is preserved byte-for-byte. All three audit tables are retained. No database schema was queried to generate this document.
