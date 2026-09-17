from psycopg.rows import tuple_row

REQUIRED_COLUMNS = {
    "ai_records": {
        "id": ("text", "NO"),
        "project_id": ("text", "NO"),
        "kind": ("text", "NO"),
        "revision": ("integer", "NO"),
        "data": ("jsonb", "NO"),
        "created_at": ("timestamp with time zone", "NO"),
        "updated_at": ("timestamp with time zone", "NO"),
    },
    "ai_requests": {
        "project_id": ("text", "NO"),
        "request_key": ("text", "NO"),
        "fingerprint": ("text", "NO"),
        "record_id": ("text", "NO"),
    },
    "checkpoint_migrations": {"v": ("integer", "NO")},
    "checkpoints": {
        "thread_id": ("text", "NO"),
        "checkpoint_ns": ("text", "NO"),
        "checkpoint_id": ("text", "NO"),
        "parent_checkpoint_id": ("text", "YES"),
        "type": ("text", "YES"),
        "checkpoint": ("jsonb", "NO"),
        "metadata": ("jsonb", "NO"),
    },
    "checkpoint_blobs": {
        "thread_id": ("text", "NO"),
        "checkpoint_ns": ("text", "NO"),
        "channel": ("text", "NO"),
        "version": ("text", "NO"),
        "type": ("text", "NO"),
        "blob": ("bytea", "YES"),
    },
    "checkpoint_writes": {
        "thread_id": ("text", "NO"),
        "checkpoint_ns": ("text", "NO"),
        "checkpoint_id": ("text", "NO"),
        "task_id": ("text", "NO"),
        "idx": ("integer", "NO"),
        "channel": ("text", "NO"),
        "type": ("text", "YES"),
        "blob": ("bytea", "NO"),
        "task_path": ("text", "NO"),
    },
}
REQUIRED_DEFAULTS = {
    ("ai_records", "revision"),
    ("ai_records", "created_at"),
    ("ai_records", "updated_at"),
    ("checkpoints", "checkpoint_ns"),
    ("checkpoints", "metadata"),
    ("checkpoint_blobs", "checkpoint_ns"),
    ("checkpoint_writes", "checkpoint_ns"),
    ("checkpoint_writes", "task_path"),
}
REQUIRED_INDEXES = {
    "ai_records_project",
    "checkpoints_thread_id_idx",
    "checkpoint_blobs_thread_id_idx",
    "checkpoint_writes_thread_id_idx",
}


def inspect_connection(connection) -> list[str]:
    problems = []
    with connection.cursor(row_factory=tuple_row) as conn:
        rows = conn.execute(
            "SELECT table_name,column_name,data_type,is_nullable,column_default "
            "FROM information_schema.columns "
            "WHERE table_schema='public'"
        ).fetchall()
        actual: dict[str, dict[str, tuple[str, str, str | None]]] = {}
        for table, column, data_type, is_nullable, default in rows:
            actual.setdefault(table, {})[column] = (data_type, is_nullable, default)
        for table, required in REQUIRED_COLUMNS.items():
            missing = sorted(set(required) - set(actual.get(table, {})))
            if missing:
                problems.append(f"{table}: missing {', '.join(missing)}")
            for column, expected in required.items():
                if column in actual.get(table, {}) and actual[table][column][:2] != expected:
                    problems.append(f"{table}.{column}: expected {expected}, got {actual[table][column][:2]}")
        for table, column in REQUIRED_DEFAULTS:
            if column in actual.get(table, {}) and actual[table][column][2] is None:
                problems.append(f"{table}.{column}: missing default")
        if "checkpoint_migrations" in actual:
            versions = {row[0] for row in conn.execute("SELECT v FROM checkpoint_migrations")}
            if versions != set(range(10)):
                problems.append(f"checkpoint_migrations: expected 0..9, got {sorted(versions)}")
        primary_key_tables = {
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.table_constraints "
                "WHERE table_schema='public' AND constraint_type='PRIMARY KEY'"
            )
        }
        for table in REQUIRED_COLUMNS:
            if table not in primary_key_tables:
                problems.append(f"{table}: missing primary key")
        foreign_key = conn.execute(
            "SELECT 1 FROM information_schema.table_constraints "
            "WHERE table_schema='public' AND table_name='ai_requests' "
            "AND constraint_type='FOREIGN KEY'"
        ).fetchone()
        if foreign_key is None:
            problems.append("ai_requests: missing record foreign key")
        index_names = {
            row[0] for row in conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname='public'")
        }
        missing_indexes = sorted(REQUIRED_INDEXES - index_names)
        if missing_indexes:
            problems.append("indexes: missing " + ", ".join(missing_indexes))
    return problems
