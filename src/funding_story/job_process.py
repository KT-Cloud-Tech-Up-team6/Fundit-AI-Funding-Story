"""The supervisor owns the lease; the fresh child owns its database connections."""

import sys

from .bootstrap import application, close_pools, open_pools
from .observability import emit, event_context
from .tasks import execute_claimed


def main():
    record_id, token = sys.argv[1:]
    with event_context(run_id=record_id):
        try:
            open_pools()
            row = application.get_job(record_id)
            if row["data"].get("_lease_token") != token or row["data"]["status"] != "running":
                emit("job_ownership_lost")
                return 1
            execute_claimed(row)
            return 0
        except Exception as exc:  # noqa: BLE001 -- Do not print credentials or input DTOs.
            emit("job_process_error", error_type=type(exc).__name__)
            return 1
        finally:
            close_pools()


if __name__ == "__main__":
    sys.exit(main())
