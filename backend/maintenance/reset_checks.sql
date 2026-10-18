-- READ-ONLY operator checks. No secrets, contact details or message bodies.
-- Run only after independent target verification through an approved PG service.
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '60s';
SET LOCAL lock_timeout = '3s';
SET LOCAL row_security = off; -- Errors on filtered results; does not bypass RLS.

SELECT current_database() AS database, inet_server_addr() AS server_address,
       inet_server_port() AS server_port, current_user AS database_user,
       (SELECT oid FROM pg_database WHERE datname=current_database()) AS database_oid;
SELECT version_num FROM public.alembic_version;

-- Protected IDs/active state only. Slug and role are existing RBAC authority.
SELECT u.id, u.admin_role_id, u.is_active
FROM public.users u JOIN public.admin_roles r ON r.id=u.admin_role_id
WHERE u.role::text='admin' AND r.slug='super_admin' ORDER BY u.id;

-- Other admins require individual decisions. No admin becomes a reset candidate
-- simply because it is absent from the Super Admin query.
SELECT u.id, u.admin_role_id, u.is_active
FROM public.users u LEFT JOIN public.admin_roles r ON r.id=u.admin_role_id
WHERE u.role::text='admin' AND r.slug IS DISTINCT FROM 'super_admin' ORDER BY u.id;
SELECT role::text, count(*) FROM public.users GROUP BY role ORDER BY role::text;

-- Preserve all catalog entries, not merely the three approved option names.
SELECT id, slug, name, status FROM public.programs ORDER BY id;
SELECT 'admin_roles' AS table_name, count(*) FROM public.admin_roles
UNION ALL SELECT 'permissions', count(*) FROM public.permissions
UNION ALL SELECT 'admin_role_permissions', count(*) FROM public.admin_role_permissions
UNION ALL SELECT 'system_settings', count(*) FROM public.system_settings;

-- Worker/media/lab review indicators. These are not deletion instructions.
SELECT count(*) AS pending_file_cleanup_jobs FROM public.file_cleanup_jobs;
SELECT status::text, count(*) FROM public.community_post_email_deliveries GROUP BY status;
SELECT status::text, count(*) FROM public.lab_vm_access GROUP BY status;
SELECT status::text, count(*) FROM public.meetings GROUP BY status;
SELECT 'auth_audit_log' AS table_name, count(*) FROM public.auth_audit_log
UNION ALL SELECT 'lab_access_audit_log', count(*) FROM public.lab_access_audit_log
UNION ALL SELECT 'lab_vm_access_audit_log', count(*) FROM public.lab_vm_access_audit_log;

-- Existing constraints must remain validated; no disabling or revalidation DDL.
SELECT ns.nspname AS schema, rel.relname AS table_name, con.conname AS constraint_name
FROM pg_constraint con JOIN pg_class rel ON rel.oid=con.conrelid
JOIN pg_namespace ns ON ns.oid=rel.relnamespace
WHERE con.contype='f' AND NOT con.convalidated
  AND ns.nspname NOT IN ('pg_catalog','information_schema');

SELECT n.nspname AS schema, c.relname AS table_name, t.tgname AS trigger_name, t.tgenabled
FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
JOIN pg_namespace n ON n.oid=c.relnamespace WHERE NOT t.tgisinternal ORDER BY 1,2,3;
SELECT n.nspname AS schema, c.relname AS table_name, con.conname AS constraint_name,
       con.contype AS kind, con.convalidated AS validated,
       pg_get_constraintdef(con.oid) AS definition
FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid
JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname NOT IN ('pg_catalog','information_schema') ORDER BY 1,2,3;
SELECT schemaname, tablename, indexname, indexdef
FROM pg_indexes WHERE schemaname NOT IN ('pg_catalog','information_schema')
ORDER BY 1,2,3;
SELECT schemaname, sequencename FROM pg_sequences ORDER BY 1,2;
ROLLBACK;
