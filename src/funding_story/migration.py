import os
import subprocess
from pathlib import Path
from urllib.parse import urlencode

import psycopg
from psycopg.conninfo import conninfo_to_dict

from .config import settings
from .infrastructure.persistence.schema import inspect_connection

FLYWAY_IMAGE = "flyway/flyway:13.7.0"
MIGRATION_DIR = Path(__file__).resolve().parents[2] / "db" / "migration"


def run_flyway(command="migrate", *, dsn=None, network_container=None, extra=()):
    """Run the pinned Flyway image without exposing the password in process arguments."""
    values = conninfo_to_dict(dsn or settings().database_dsn)
    host = values.get("host", "localhost")
    port = values.get("port", "5432")
    docker_args = ["docker", "run", "--rm"]
    if network_container:
        docker_args += ["--network", f"container:{network_container}"]
        host, port = "localhost", "5432"
    elif host in ("localhost", "127.0.0.1"):
        docker_args += ["--add-host", "host.docker.internal:host-gateway"]
        host = "host.docker.internal"
    docker_args += [
        "-v",
        f"{MIGRATION_DIR}:/flyway/sql:ro",
        "--env",
        "FLYWAY_URL",
        "--env",
        "FLYWAY_USER",
        "--env",
        "FLYWAY_PASSWORD",
        "--env",
        "FLYWAY_LOCATIONS",
        FLYWAY_IMAGE,
        *extra,
        command,
    ]
    query = "?" + urlencode({"sslmode": values["sslmode"]}) if values.get("sslmode") else ""
    env = {
        **os.environ,
        "FLYWAY_URL": f"jdbc:postgresql://{host}:{port}/{values['dbname']}{query}",
        "FLYWAY_USER": values["user"],
        "FLYWAY_PASSWORD": values.get("password", ""),
        "FLYWAY_LOCATIONS": "filesystem:/flyway/sql",
    }
    subprocess.run(docker_args, env=env, check=True)


def inspect_schema(dsn: str) -> list[str]:
    with psycopg.connect(dsn) as conn:
        return inspect_connection(conn)


def baseline_existing_database(dsn: str, *, network_container: str | None = None) -> None:
    problems = inspect_schema(dsn)
    if problems:
        raise ValueError("기존 DB 구조가 기준과 다릅니다:\n- " + "\n- ".join(problems))
    run_flyway(
        "baseline",
        dsn=dsn,
        network_container=network_container,
        extra=("-baselineVersion=2", "-baselineDescription=existing_schema"),
    )
    run_flyway("validate", dsn=dsn, network_container=network_container)
