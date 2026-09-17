import atexit
from contextlib import contextmanager
from threading import Lock

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ...config import settings

_lock = Lock()
_pool: ConnectionPool | None = None
_checkpoint_pool: ConnectionPool | None = None


def _new_pool() -> ConnectionPool:
    cfg = settings()
    return ConnectionPool(
        conninfo=cfg.database_dsn,
        min_size=cfg.db_pool_min_size,
        max_size=cfg.db_pool_max_size,
        timeout=cfg.db_pool_timeout_seconds,
        max_idle=cfg.db_pool_max_idle_seconds,
        max_lifetime=cfg.db_pool_max_lifetime_seconds,
        kwargs={"row_factory": dict_row},
        open=True,
        name="funding-story-runtime",
    )


def _new_checkpoint_pool() -> ConnectionPool:
    cfg = settings()
    return ConnectionPool(
        conninfo=cfg.database_dsn,
        min_size=0,
        max_size=cfg.db_checkpoint_pool_max_size,
        timeout=cfg.db_pool_timeout_seconds,
        max_idle=cfg.db_pool_max_idle_seconds,
        max_lifetime=cfg.db_pool_max_lifetime_seconds,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=True,
        name="funding-story-checkpoints",
    )


def open_pools(*, checkpoints: bool = False) -> None:
    global _pool, _checkpoint_pool
    with _lock:
        if _pool is None:
            _pool = _new_pool()
        if checkpoints and _checkpoint_pool is None:
            _checkpoint_pool = _new_checkpoint_pool()


def runtime_pool() -> ConnectionPool:
    open_pools()
    assert _pool is not None
    return _pool


def checkpoint_pool() -> ConnectionPool:
    open_pools(checkpoints=True)
    assert _checkpoint_pool is not None
    return _checkpoint_pool


@contextmanager
def connection():
    with runtime_pool().connection() as conn:
        yield conn


def close_pools() -> None:
    global _pool, _checkpoint_pool
    with _lock:
        checkpoint, runtime = _checkpoint_pool, _pool
        _checkpoint_pool = None
        _pool = None
    if checkpoint is not None:
        checkpoint.close()
    if runtime is not None:
        runtime.close()


atexit.register(close_pools)


def new_checkpointer():
    return PostgresSaver(checkpoint_pool())
