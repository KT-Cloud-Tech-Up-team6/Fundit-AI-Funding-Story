"""Composition root: bind application ports to infrastructure adapters."""

from .application import FundingStoryApplication
from .content_insights import ContentInsightsApplication
from .infrastructure.persistence import close_pools, repository
from .infrastructure.persistence import open_pools as _open_pools
from .infrastructure.ttl_state import ttl_repository

application = FundingStoryApplication(ttl_repository())
content_insights_application = ContentInsightsApplication(repository())


def open_pools(*, checkpoints: bool = False) -> None:
    _open_pools(checkpoints=checkpoints)
    funding_ok, funding_reason = application.readiness()
    content_ok, content_reason = content_insights_application.readiness()
    if not funding_ok or not content_ok:
        close_pools()
        raise RuntimeError(funding_reason if not funding_ok else content_reason)


__all__ = [
    "application",
    "close_pools",
    "content_insights_application",
    "open_pools",
]
