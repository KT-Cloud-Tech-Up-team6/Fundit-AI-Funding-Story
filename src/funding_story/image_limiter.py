"""Project/model-wide pacing and adaptive concurrency backed by PostgreSQL."""

import copy
import hashlib
import random
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from uuid import uuid4

from psycopg.types.json import Jsonb

from .config import settings
from .infrastructure.persistence import connection


class MemoryStore:
    """Process-local adapter for tests; never used by dev/prod workers."""

    def __init__(self):
        self.state = {}
        self.lock = threading.Lock()

    @contextmanager
    def transaction(self):
        with self.lock:
            yield self.state, time.monotonic()


class PostgresStore:
    def __init__(self, key):
        self.key = key
        self.record_id = "image-rate:" + key

    @contextmanager
    def transaction(self):
        # Provider calls and sleeps happen outside this short transaction lock.
        with connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (self.record_id,))
            row = conn.execute(
                "SELECT data FROM ai_records WHERE id=%s FOR UPDATE",
                (self.record_id,),
            ).fetchone()
            now = float(
                conn.execute("SELECT EXTRACT(EPOCH FROM clock_timestamp()) AS now").fetchone()["now"]
            )
            state = copy.deepcopy(row["data"]) if row else {}
            yield state, now
            conn.execute(
                "INSERT INTO ai_records(id,project_id,kind,data) "
                "VALUES(%s,%s,'image_rate_limit',%s) "
                "ON CONFLICT(id) DO UPDATE SET data=EXCLUDED.data,"
                "revision=ai_records.revision+1,updated_at=clock_timestamp()",
                (self.record_id, "_system", Jsonb(state)),
            )


@dataclass(frozen=True)
class Permit:
    token: str
    epoch: int


class ImageLimiter:
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store

    def _state(self, state, now):
        if not state or (
            not state.get("leases")
            and now - state.get("updated", now) > 600
            and now >= max(state.get("blocked_until", 0), state.get("next_start", 0))
        ):
            state.clear()
            state.update(
                interval=self.cfg.image_request_interval_seconds,
                limit=self.cfg.image_generation_concurrency,
                blocked_until=0,
                next_start=0,
                healthy=0,
                level=0,
                epoch=0,
                leases={},
            )
        state["leases"] = {k: v for k, v in state["leases"].items() if v > now}
        state["updated"] = now
        return state

    def acquire(self):
        with self.store.transaction() as (state, now):
            state = self._state(state, now)
            delay = max(state["next_start"], state["blocked_until"]) - now
            if delay > 0:
                return None, delay
            if len(state["leases"]) >= min(state["limit"], self.cfg.image_generation_concurrency):
                return None, 0.1
            permit = Permit(uuid4().hex, state["epoch"])
            # Provider HTTP timeout is 180s; recover a crashed worker's permit after 210s.
            state["leases"][permit.token] = now + 210
            state["next_start"] = now + state["interval"]
            return permit, 0

    def renew(self, permits):
        with self.store.transaction() as (state, now):
            state = self._state(state, now)
            for permit in permits:
                if permit.token in state["leases"]:
                    state["leases"][permit.token] = now + 210

    def finish(self, permit, *, success=False, throttled=False, retry_after=0):
        with self.store.transaction() as (state, now):
            state = self._state(state, now)
            state["leases"].pop(permit.token, None)
            if throttled:
                # In-flight failures form one overload wave, not N backoff escalations.
                if permit.epoch == state["epoch"]:
                    state["epoch"] += 1
                    state["level"] = min(state["level"] + 1, 6)
                    state["limit"] = max(1, state["limit"] // 2)
                    state["interval"] = min(
                        self.cfg.image_request_max_interval_seconds, state["interval"] * 1.5
                    )
                    base = min(
                        self.cfg.image_retry_max_delay_seconds,
                        self.cfg.image_retry_delay_seconds * 2 ** (state["level"] - 1),
                    )
                    delay = max(retry_after, random.uniform(base / 2, base), state["interval"])
                    state["blocked_until"] = max(state["blocked_until"], now + delay)
                    state["healthy"] = 0
                else:
                    state["blocked_until"] = max(state["blocked_until"], now + retry_after)
            elif success and permit.epoch == state["epoch"] and now >= state["blocked_until"]:
                state["healthy"] += 1
                if state["healthy"] >= 3:
                    state["healthy"] = 0
                    state["level"] = max(0, state["level"] - 1)
                    state["interval"] = max(self.cfg.image_request_interval_seconds, state["interval"] * 0.9)
                    state["limit"] = min(self.cfg.image_generation_concurrency, state["limit"] + 1)
            elif not success:
                state["healthy"] = 0
            return {key: state[key] for key in ("interval", "limit", "level")}


@lru_cache(maxsize=32)
def _shared_limiter(provider, account, location, model, app_env, config_values):
    from types import SimpleNamespace

    cfg = SimpleNamespace(**dict(config_values))
    digest = hashlib.sha256(f"{provider}|{account}|{location}|{model}".encode()).hexdigest()[:32]
    store = MemoryStore() if app_env == "test" else PostgresStore(digest)
    return ImageLimiter(cfg, store)


def limiter():
    cfg = settings()
    fields = (
        "image_generation_concurrency",
        "image_request_interval_seconds",
        "image_request_max_interval_seconds",
        "image_retry_delay_seconds",
        "image_retry_max_delay_seconds",
    )
    if cfg.image_provider == "openai":
        account = (
            cfg.openai_service_account_id
            if cfg.openai_auth_mode == "eks_wif"
            else cfg.openai_project or cfg.openai_organization or "default"
        )
    else:
        account = cfg.google_cloud_project
    location = "global" if cfg.image_provider == "openai" else cfg.google_cloud_location
    return _shared_limiter(
        cfg.image_provider,
        account,
        location,
        cfg.image_model,
        cfg.app_env,
        tuple((key, getattr(cfg, key)) for key in fields),
    )
