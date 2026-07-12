"""Documented starting point for a future retailer adapter."""

from caffeine_scout.models import RawOffer, RetailerSource, SearchRequest, SourceStatus


class RetailerTemplateSource(RetailerSource):
    """Copy this adapter, then keep retailer-specific parsing inside the new module.

    Respect robots rules and terms, use documented/public endpoints, set timeouts,
    add polite pacing, and never bypass authentication, CAPTCHAs, or rate limits.
    Return RawOffer instances; shared code owns filtering and normalization.
    """

    name = "retailer_template"

    async def search(self, request: SearchRequest) -> list[RawOffer]:
        del request
        raise NotImplementedError("implement the retailer's permitted public/API workflow")

    async def healthcheck(self) -> SourceStatus:
        return SourceStatus(
            name=self.name, healthy=False, detail="template only; not a configured source"
        )
