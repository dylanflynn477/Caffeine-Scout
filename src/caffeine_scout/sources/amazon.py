"""Amazon Creators API boundary (disabled until credentials and client are configured)."""

from caffeine_scout.config import AmazonSourceConfig
from caffeine_scout.models import RawOffer, RetailerSource, SearchRequest, SourceStatus


class AmazonSetupError(RuntimeError):
    pass


class AmazonSource(RetailerSource):
    """Credential-gated skeleton for Amazon's official Creators API.

    PA-API 5.0 was deprecated on 2026-05-15. This boundary intentionally targets
    its successor and performs no page scraping. A future implementation should
    use Amazon's approved Creators API client/contract and map responses to RawOffer.
    """

    name = "amazon"

    def __init__(self, config: AmazonSourceConfig) -> None:
        self.config = config

    def _require_setup(self) -> None:
        if not self.config.has_credentials:
            raise AmazonSetupError(
                "Amazon is enabled but AMAZON_CREATORS_CREDENTIAL_ID, "
                "AMAZON_CREATORS_CREDENTIAL_SECRET, and AMAZON_PARTNER_TAG are required"
            )

    async def search(self, request: SearchRequest) -> list[RawOffer]:
        del request
        self._require_setup()
        raise AmazonSetupError(
            "Amazon Creators API credentials are present, but the approved API client "
            "is not implemented in this initial release"
        )

    async def healthcheck(self) -> SourceStatus:
        if not self.config.has_credentials:
            return SourceStatus(
                name=self.name,
                healthy=False,
                detail="disabled until official Creators API credentials are configured",
            )
        return SourceStatus(
            name=self.name,
            healthy=False,
            detail="credentials detected; approved Creators API client not implemented",
        )
