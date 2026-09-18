"""Composition root: bind application ports to infrastructure adapters."""

from .application import FundingStoryApplication
from .content_insights import ContentInsightsApplication
from .infrastructure.persistence import checkpoint_pool, close_pools, repository
from .infrastructure.persistence import open_pools as _open_pools
from .infrastructure.persistence.database import new_checkpointer

application = FundingStoryApplication(repository())
content_insights_application = ContentInsightsApplication(repository())


def open_pools(*, checkpoints: bool = False) -> None:
    _open_pools(checkpoints=checkpoints)
    ok, reason = application.readiness()
    if not ok:
        close_pools()
        raise RuntimeError(reason)


__all__ = [
    "application",
    "checkpoint_pool",
    "close_pools",
    "content_insights_application",
    "new_checkpointer",
    "open_pools",
]
