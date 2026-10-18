"""Read-only schema inventory/reset preview. Deliberately has NO executor.

Run from backend: python -m maintenance.reset_prepare --help
No FastAPI app import, worker startup, file unlink, SQL DML, seed, or migration.
"""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
from datetime import datetime, timezone

# Loading model metadata must not require or connect to a real database.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://unused:unused@127.0.0.1:1/unused")
os.environ.setdefault("JWT_SECRET", "metadata-only-not-used-for-authentication")
from app.database import Base
import app.models  # noqa: F401 — register model metadata, not app startup
from sqlalchemy.dialects import postgresql
from maintenance.reset_policy import ALL_TABLES, AUDIT, MANUAL, OWNED, PRESERVE, preview

REPOSITORY = Path(__file__).resolve().parents[2]


def source_inventory():
    tables, foreign_keys, indexes, constraints = {}, [], [], []
    for table in Base.metadata.sorted_tables:
        columns = []
        for column in table.columns:
            columns.append({"name": column.name, "type": str(column.type), "nullable": column.nullable,
                            "postgresql_type": column.type.compile(dialect=postgresql.dialect()),
                            "primary_key": column.primary_key})
            for fk in column.foreign_keys:
                foreign_keys.append({"source_schema": "public", "source_table": table.name,
                                     "source_column": column.name, "target_schema": "public",
                                     "target_table": fk.column.table.name, "target_column": fk.column.name,
                                     "nullable": column.nullable, "ondelete": fk.ondelete or "NO ACTION"})
        category = "preserve" if table.name in PRESERVE else ("audit-retain" if table.name in AUDIT else
                   ("manual-review" if table.name in MANUAL else "mixed/row-policy"))
        tables[table.name] = {"category": category, "columns": columns}
        for index in table.indexes:
            indexes.append({"table": table.name, "name": index.name, "unique": index.unique,
                            "columns": [column.name for column in index.columns]})
        for constraint in table.constraints:
            constraints.append({"table": table.name, "name": constraint.name,
                                "kind": type(constraint).__name__,
                                "columns": [column.name for column in constraint.columns]})
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPOSITORY, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unverified"
    return {"basis": "repository-models-only", "commit": commit, "table_count": len(tables),
            "tables": tables, "foreign_keys": sorted(foreign_keys, key=lambda f: (f["source_table"], f["source_column"])),
            "model_indexes": sorted(indexes, key=lambda item: (item["table"], item["name"] or "")),
            "model_constraints": sorted(constraints, key=lambda item: (item["table"], item["kind"], item["name"] or "")),
            "production_schema_verified": False, "execution_enabled": False,
            "unclassified_model_tables": sorted(set(tables) - ALL_TABLES)}


def write_report(path, report, *, live=False):
    path = Path(path).resolve()
    if live and path.is_relative_to(REPOSITORY):
        raise ValueError("Production evidence must be stored outside the repository")
    # Caller creates an approved protected directory; never overwrite a report.
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, default=str)
        stream.write("\n")


def validate_expected_target(expected):
    if expected.get("environment") != "production" or expected.get("verified_by_operator") is not True:
        raise ValueError("Target needs explicit independently verified production identity")
    for key in ("database", "database_user"):
        if not isinstance(expected.get(key), str) or not expected[key].strip() or "<" in expected[key]:
            raise ValueError("Target identity is incomplete")
    if type(expected.get("database_oid")) is not int or expected["database_oid"] <= 0:
        raise ValueError("Target database OID is not verified")
    if type(expected.get("server_port")) is not int or not 1 <= expected["server_port"] <= 65535:
        raise ValueError("Target port is not verified")
    ipaddress.ip_address(expected.get("server_address", ""))


def validate_target(actual, expected):
    validate_expected_target(expected)
    for key in ("database", "server_address", "server_port", "database_oid", "database_user"):
        if expected.get(key) is None or str(expected[key]) != str(actual.get(key)):
            raise ValueError("Production identity mismatch")


def collect_postgres(connection, expected, max_rows=100000):
    """Called only with an explicit PG service. No credential URLs are logged.

    The same repeatable-read, read-only snapshot supplies catalog/count/projection
    data. Views/foreign tables are inventoried but never queried for data.
    """
    from psycopg import sql
    from psycopg.rows import dict_row

    model = source_inventory()
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute("SET LOCAL statement_timeout = '60s'")
        connection.execute("SET LOCAL lock_timeout = '3s'")
        connection.execute("SET LOCAL row_security = off")  # Fail, not silently omit rows under an RLS policy.
        with connection.cursor(row_factory=dict_row) as cur:
            def query(statement, params=None):
                cur.execute(statement, params)
                return cur.fetchall()

            target = query("""SELECT current_database() AS database, current_user AS database_user,
                inet_server_addr()::text AS server_address, inet_server_port() AS server_port,
                (SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS database_oid,
                current_setting('server_version') AS server_version,
                current_setting('transaction_read_only') AS transaction_read_only""")[0]
            validate_target(target, expected)
            if target["transaction_read_only"] != "on":
                raise ValueError("Read-only transaction not established")
            relations = query("""SELECT n.nspname AS schema, c.relname AS name, c.relkind AS kind,
                c.relrowsecurity AS rls, c.relforcerowsecurity AS force_rls,
                c.relispartition AS is_partition, pg_get_userbyid(c.relowner) AS owner,
                CASE WHEN c.relkind IN ('r','p','m') THEN pg_total_relation_size(c.oid) ELSE NULL END AS bytes
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%%'
                  AND n.nspname NOT LIKE 'pg_temp_%%' AND c.relkind IN ('r','p','v','m','f')
                ORDER BY n.nspname,c.relname""")
            columns = query("""SELECT n.nspname AS schema, c.relname AS table, a.attname AS column,
                format_type(a.atttypid,a.atttypmod) AS type, NOT a.attnotnull AS nullable
                FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE a.attnum>0 AND NOT a.attisdropped AND c.relkind IN ('r','p')
                  AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%%'
                ORDER BY n.nspname,c.relname,a.attnum""")
            foreign_keys = query("""SELECT sn.nspname AS source_schema, s.relname AS source_table,
                sa.attname AS source_column, tn.nspname AS target_schema, t.relname AS target_table,
                ta.attname AS target_column, NOT sa.attnotnull AS nullable,
                f.conname AS constraint_name, f.confdeltype AS delete_code, f.convalidated AS validated,
                f.condeferrable AS deferrable, f.condeferred AS initially_deferred,
                cardinality(f.conkey) AS key_columns
                FROM pg_constraint f JOIN pg_class s ON s.oid=f.conrelid
                JOIN pg_namespace sn ON sn.oid=s.relnamespace
                JOIN pg_class t ON t.oid=f.confrelid JOIN pg_namespace tn ON tn.oid=t.relnamespace
                CROSS JOIN LATERAL unnest(f.conkey,f.confkey) AS keys(source_att,target_att)
                JOIN pg_attribute sa ON sa.attrelid=s.oid AND sa.attnum=keys.source_att
                JOIN pg_attribute ta ON ta.attrelid=t.oid AND ta.attnum=keys.target_att
                WHERE f.contype='f' AND sn.nspname NOT IN ('pg_catalog','information_schema')
                ORDER BY sn.nspname,s.relname,f.conname,sa.attname""")
            constraints = query("""SELECT n.nspname AS schema, c.relname AS table, con.conname AS name,
                con.contype AS kind, con.convalidated AS validated, con.condeferrable AS deferrable,
                con.condeferred AS initially_deferred, pg_get_constraintdef(con.oid) AS definition
                FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname NOT IN ('pg_catalog','information_schema')
                ORDER BY n.nspname,c.relname,con.conname""")
            indexes = query("""SELECT schemaname AS schema, tablename AS table, indexname AS name,
                indexdef AS definition FROM pg_indexes
                WHERE schemaname NOT IN ('pg_catalog','information_schema')
                ORDER BY schemaname,tablename,indexname""")
            triggers = query("""SELECT n.nspname AS schema, c.relname AS table, t.tgname AS name,
                t.tgenabled AS enabled, pn.nspname AS function_schema, p.proname AS function,
                p.prosecdef AS security_definer
                FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace pn ON pn.oid=p.pronamespace
                WHERE NOT t.tgisinternal ORDER BY n.nspname,c.relname,t.tgname""")
            sequences = query("""SELECT ps.schemaname AS schema, ps.sequencename AS name, ps.sequenceowner AS owner,
                tn.nspname AS owned_table_schema, tab.relname AS owned_table, att.attname AS owned_column
                FROM pg_sequences ps JOIN pg_namespace ns ON ns.nspname=ps.schemaname
                JOIN pg_class seq ON seq.relnamespace=ns.oid AND seq.relname=ps.sequencename
                LEFT JOIN pg_depend dep ON dep.classid='pg_class'::regclass AND dep.objid=seq.oid
                  AND dep.refclassid='pg_class'::regclass AND dep.refobjsubid>0 AND dep.deptype IN ('a','i')
                LEFT JOIN pg_class tab ON tab.oid=dep.refobjid
                LEFT JOIN pg_namespace tn ON tn.oid=tab.relnamespace
                LEFT JOIN pg_attribute att ON att.attrelid=tab.oid AND att.attnum=dep.refobjsubid
                ORDER BY ps.schemaname,ps.sequencename""")
            event_triggers = query("SELECT evtname AS name, evtevent AS event, evtenabled AS enabled FROM pg_event_trigger ORDER BY evtname")
            rules = query("""SELECT schemaname AS schema, tablename AS table, rulename AS name
                FROM pg_rules WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1,2,3""")
            extensions = query("SELECT extname AS name, extversion AS version FROM pg_extension ORDER BY extname")
            blockers, counts, projections = [], {}, {}
            actual_public = {r["name"] for r in relations if r["schema"] == "public" and r["kind"] in ("r", "p")}
            expected_public = set(model["tables"]) | {"alembic_version"}
            if actual_public != expected_public:
                blockers.append("Model/database table drift: review missing or extra relations")
            def normalized_type(value):
                return value.lower().replace("character varying", "varchar")
            expected_columns = {("public", name, col["name"], col["nullable"], normalized_type(col["postgresql_type"]))
                                for name, table in model["tables"].items() for col in table["columns"]}
            actual_columns = {(col["schema"], col["table"], col["column"], col["nullable"], normalized_type(col["type"]))
                              for col in columns if col["schema"] == "public" and col["table"] != "alembic_version"}
            if actual_columns != expected_columns:
                blockers.append("Column/type/nullability drift needs DBA review")
            if triggers or event_triggers or rules:
                blockers.append("Review all custom triggers/event triggers/rules before any reset")
            if any(r["schema"] != "public" or r["kind"] != "r" or r["rls"] or r["is_partition"] for r in relations):
                blockers.append("Review non-public/partitioned/RLS/view/foreign relations and external ownership")
            delete_codes = {"NO ACTION": "a", "CASCADE": "c", "RESTRICT": "r", "SET NULL": "n", "SET DEFAULT": "d"}
            model_edges = {(f["source_schema"], f["source_table"], f["source_column"], f["target_schema"], f["target_table"], f["target_column"], f["nullable"], delete_codes.get(f["ondelete"], "unknown")) for f in model["foreign_keys"]}
            actual_edges = {(f["source_schema"], f["source_table"], f["source_column"], f["target_schema"], f["target_table"], f["target_column"], f["nullable"], f["delete_code"]) for f in foreign_keys}
            if actual_edges != model_edges or any(not f["validated"] or f["key_columns"] != 1 for f in foreign_keys):
                blockers.append("Unexpected/unvalidated/composite FK or nullability drift")
            read_rows = 0
            for relation in relations:
                qualified = sql.Identifier(relation["schema"], relation["name"])
                if relation["kind"] not in ("r", "p"):
                    continue
                count = query(sql.SQL("SELECT count(*) AS count FROM {}").format(qualified))[0]["count"]
                counts[f"{relation['schema']}.{relation['name']}"] = count
                if relation["schema"] != "public" or relation["name"] not in model["tables"]:
                    continue
                table = relation["name"]
                primary_key = next(col["name"] for col in model["tables"][table]["columns"] if col["primary_key"])
                projected = {primary_key} | {f["source_column"] for f in model["foreign_keys"] if f["source_table"] == table}
                if table == "users":
                    projected |= {"role", "admin_role_id", "is_active"}
                if table == "admin_roles":
                    projected.add("slug")
                if table in ("lab_vm_access", "meetings", "community_post_email_deliveries"):
                    projected.add("status")
                if read_rows + count > max_rows:
                    raise ValueError("Projection size exceeds reviewed limit; prepare staged maintenance design")
                read_rows += count
                # No names, addresses, message bodies, passwords, tokens or file paths.
                selected = [sql.Identifier(c) for c in sorted(projected)]
                if primary_key != "id":
                    selected.append(sql.SQL("{} AS id").format(sql.Identifier(primary_key)))
                projections[table] = query(sql.SQL("SELECT {} FROM {} ORDER BY {}").format(
                    sql.SQL(",").join(selected), qualified, sql.Identifier(primary_key)))
            revision = query('SELECT version_num FROM public.alembic_version') if "alembic_version" in actual_public else []
            plan = preview(projections, foreign_keys)
            if [r["version_num"] for r in revision] != ["a9d4e7f1c2b6"]:
                blockers.append("Production migration revision differs from reviewed source head")
            plan["blockers"] = sorted(set(plan["blockers"] + blockers))
            return {"basis": "read-only-postgresql-snapshot", "collected_at_utc": datetime.now(timezone.utc).isoformat(),
                    "target": target, "target_fingerprint": hashlib.sha256(json.dumps(target, sort_keys=True).encode()).hexdigest(),
                    "source_commit": model["commit"], "relations": relations, "columns": columns,
                    "foreign_keys": foreign_keys, "constraints": constraints, "indexes": indexes,
                    "custom_triggers": triggers, "event_triggers": event_triggers,
                    "rules": rules, "sequences": sequences, "extensions": extensions,
                    "counts": counts, "alembic_revision": revision, "preview": plan,
                    "execution_enabled": False, "backup_verified": False, "approval_recorded": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    source = sub.add_parser("source", help="Offline model inventory; never connects to a database")
    source.add_argument("--output", required=True)
    inventory = sub.add_parser("dry-run", help="Explicitly targeted read-only PostgreSQL inventory and draft preview")
    inventory.add_argument("--pg-service", required=True)
    inventory.add_argument("--expected-target", required=True, help="Protected operator-verified JSON identity file")
    inventory.add_argument("--output", required=True, help="New protected path outside Git")
    inventory.add_argument("--max-projected-rows", type=int, default=100000)
    sub.add_parser("execute", help="Reserved: always refuses in this preparation-only version")
    args = parser.parse_args(argv)
    if args.mode == "execute":
        parser.exit(2, "REFUSED: preparation only. No executor is provided; no database was opened. A separately reviewed, approved reset implementation is required.\n")
    try:
        if args.mode == "source":
            report = source_inventory()
            write_report(args.output, report)
        else:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.pg_service) or args.max_projected_rows < 1:
                raise ValueError("Invalid service name or projection limit")
            expected = json.loads(Path(args.expected_target).read_text(encoding="utf-8"))
            validate_expected_target(expected)
            # Refuse repository output before opening a database connection.
            if Path(args.output).resolve().is_relative_to(REPOSITORY):
                raise ValueError("Production evidence belongs outside Git")
            import psycopg
            with psycopg.connect(service=args.pg_service, connect_timeout=5,
                                 application_name="arckenites-reset-readonly-preparation",
                                 options="-c default_transaction_read_only=on") as connection:
                report = collect_postgres(connection, expected, args.max_projected_rows)
            write_report(args.output, report, live=True)
    except Exception as exc:
        # Do not echo SQL, conninfo, credentials, personal data or server error details.
        parser.exit(1, f"Preparation failed safely ({type(exc).__name__}); inspect target/permissions/schema privately. No reset was executed.\n")
    print("Preparation report created. Execution is disabled. Protect evidence outside source control.")


if __name__ == "__main__":
    main()
