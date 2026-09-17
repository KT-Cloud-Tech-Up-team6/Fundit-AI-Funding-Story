import hashlib
import json

from langgraph.checkpoint.postgres.base import MIGRATIONS

EXPECTED_COUNT = 10
EXPECTED_SHA256 = "f1a06ba21be55813fd71eee928a27c70a6181d08dbc61da79470551148cbe60f"

payload = json.dumps(MIGRATIONS, separators=(",", ":"), ensure_ascii=True).encode()
actual_hash = hashlib.sha256(payload).hexdigest()
if len(MIGRATIONS) != EXPECTED_COUNT or actual_hash != EXPECTED_SHA256:
    raise SystemExit(
        "langgraph-checkpoint-postgres migration이 변경되었습니다. "
        "db/migration checkpoint 기준을 검토하고 새 Flyway migration을 추가하세요. "
        f"count={len(MIGRATIONS)} sha256={actual_hash}"
    )
