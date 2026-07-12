from __future__ import annotations

import asyncio
import json
import time
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from bs4 import BeautifulSoup
from pydantic import ValidationError

from caffeine_scout.config import CrawlerConfig
from caffeine_scout.crawler import EthicalPageCrawler
from caffeine_scout.models import RawOffer, SourceDiagnostic
from caffeine_scout.sources.jsonld import (
    extract_embedded_product_data,
    extract_public_product_json,
    parse_jsonld_product_page,
)

FIXTURES = Path(__file__).parent / "fixtures"
PRODUCT_URL = "https://shop.example.test/products/energy"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def raw_offer(url: str, price: str = "19.99") -> RawOffer:
    return RawOffer(
        source="fixture",
        retailer="Fixture retailer",
        product_name="Ghost Energy Cherry Limeade 16 oz 12 Pack",
        listed_price=Decimal(price),
        url=url,
    )


def crawler_config(**updates: object) -> CrawlerConfig:
    values: dict[str, object] = {
        "minimum_request_interval_seconds": 0,
        "request_timeout_seconds": 2,
        "playwright_timeout_seconds": 2,
    }
    values.update(updates)
    return CrawlerConfig.model_validate(values)


@pytest.mark.asyncio
async def test_explicit_robots_disallow_stops_before_product_request(tmp_path: Path) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, text=fixture("robots_disallowed.txt"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    crawler = EthicalPageCrawler(crawler_config(), client=client, cache_dir=tmp_path / "cache")
    try:
        result = await crawler.crawl(
            source="fixture",
            url="https://shop.example.test/products/private/energy",
            static_extractor=lambda _html, url: [raw_offer(url)],
            embedded_extractor=lambda _html, _url: [],
        )
    finally:
        await client.aclose()
    assert result.failure_reason == "explicitly_disallowed_by_robots"
    assert result.fetch.robots_decision.decision == "disallowed"
    assert paths == ["/robots.txt"]


@pytest.mark.asyncio
async def test_unavailable_robots_is_unknown_and_page_403_stops_pipeline(
    tmp_path: Path,
) -> None:
    paths: list[str] = []
    playwright_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(403, text=fixture("robots_unavailable.json"))
        return httpx.Response(403, text=fixture("product_403.html"))

    async def playwright_loader(_url: str) -> tuple[str, list[tuple[str, object]]]:
        nonlocal playwright_called
        playwright_called = True
        return "", []

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    crawler = EthicalPageCrawler(
        crawler_config(),
        client=client,
        cache_dir=tmp_path / "cache",
        playwright_loader=playwright_loader,
    )
    try:
        result = await crawler.crawl(
            source="fixture",
            url=PRODUCT_URL,
            static_extractor=lambda _html, _url: [],
            embedded_extractor=lambda _html, _url: [],
        )
    finally:
        await client.aclose()
    assert result.fetch.robots_decision.decision == "unknown"
    assert result.fetch.robots_decision.status_code == 403
    assert result.fetch.status_code == 403
    assert result.failure_reason == "product_page_refused_access"
    assert paths == ["/robots.txt", "/products/energy"]
    assert playwright_called is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fixture_name", "expected_reason"),
    [
        ("captcha.html", "captcha_encountered"),
        ("login_required.html", "authentication_required"),
    ],
)
async def test_soft_block_page_prevents_playwright_fallback(
    tmp_path: Path, fixture_name: str, expected_reason: str
) -> None:
    playwright_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=fixture("robots_allowed.txt"))
        return httpx.Response(
            200,
            text=fixture(fixture_name),
            headers={"content-type": "text/html"},
        )

    async def playwright_loader(_url: str) -> tuple[str, list[tuple[str, object]]]:
        nonlocal playwright_called
        playwright_called = True
        return "", []

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    crawler = EthicalPageCrawler(
        crawler_config(),
        client=client,
        cache_dir=tmp_path / "cache",
        playwright_loader=playwright_loader,
    )
    try:
        result = await crawler.crawl(
            source="fixture",
            url=PRODUCT_URL,
            static_extractor=lambda _html, _url: [],
            embedded_extractor=lambda _html, _url: [],
        )
    finally:
        await client.aclose()
    assert result.failure_reason == expected_reason
    assert playwright_called is False


@pytest.mark.asyncio
async def test_playwright_runs_only_after_static_and_embedded_fail(tmp_path: Path) -> None:
    stages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=fixture("robots_allowed.txt"))
        return httpx.Response(
            200,
            text=fixture("no_public_price.html"),
            headers={"content-type": "text/html"},
        )

    def static_extractor(_html: str, _url: str) -> list[RawOffer]:
        stages.append("static")
        return []

    def embedded_extractor(_html: str, _url: str) -> list[RawOffer]:
        stages.append("embedded")
        return []

    def rendered_extractor(html: str, url: str) -> list[RawOffer]:
        stages.append("rendered")
        soup = BeautifulSoup(html, "html.parser")
        price = soup.select_one('[data-testid="product-price"]')
        assert price is not None
        return [raw_offer(url, price.get_text(strip=True).lstrip("$"))]

    async def playwright_loader(_url: str) -> tuple[str, list[tuple[str, object]]]:
        stages.append("playwright")
        return fixture("rendered_price.html"), []

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    crawler = EthicalPageCrawler(
        crawler_config(),
        client=client,
        cache_dir=tmp_path / "cache",
        playwright_loader=playwright_loader,
    )
    try:
        result = await crawler.crawl(
            source="fixture",
            url=PRODUCT_URL,
            static_extractor=static_extractor,
            embedded_extractor=embedded_extractor,
            rendered_extractor=rendered_extractor,
        )
    finally:
        await client.aclose()
    assert result.fetch.method_used == "playwright_dom"
    assert stages == ["static", "embedded", "playwright", "rendered"]


@pytest.mark.asyncio
async def test_public_first_party_json_is_used_without_replaying_request(
    tmp_path: Path,
) -> None:
    payload = json.loads(fixture("public_product_response.json"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=fixture("robots_allowed.txt"))
        return httpx.Response(
            200,
            text=fixture("no_public_price.html"),
            headers={"content-type": "text/html"},
        )

    async def playwright_loader(_url: str) -> tuple[str, list[tuple[str, object]]]:
        return fixture("no_public_price.html"), [
            ("https://shop.example.test/api/public/product", payload)
        ]

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    crawler = EthicalPageCrawler(
        crawler_config(),
        client=client,
        cache_dir=tmp_path / "cache",
        playwright_loader=playwright_loader,
    )
    try:
        result = await crawler.crawl(
            source="fixture",
            url=PRODUCT_URL,
            static_extractor=lambda _html, _url: [],
            embedded_extractor=lambda _html, _url: [],
            public_json_extractor=lambda data, page_url, endpoint: extract_public_product_json(
                data, page_url, "Fixture retailer", endpoint
            ),
        )
    finally:
        await client.aclose()
    assert result.fetch.method_used == "public_first_party_json"
    assert result.offers[0].listed_price == Decimal("18.99")
    assert any("/api/public/product" in note for note in result.offers[0].notes)


@pytest.mark.asyncio
async def test_rate_limit_honors_retry_after_without_retrying(tmp_path: Path) -> None:
    product_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal product_requests
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=fixture("robots_allowed.txt"))
        product_requests += 1
        return httpx.Response(429, headers={"retry-after": "60"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    crawler = EthicalPageCrawler(crawler_config(), client=client, cache_dir=tmp_path / "cache")
    try:
        result = await crawler.crawl(
            source="fixture",
            url=PRODUCT_URL,
            static_extractor=lambda _html, _url: [],
            embedded_extractor=lambda _html, _url: [],
        )
    finally:
        await client.aclose()
    assert result.failure_reason == "rate_limited"
    assert product_requests == 1
    assert any(item.details.get("retry_after_seconds") == 60 for item in result.fetch.diagnostics)


@pytest.mark.asyncio
async def test_successful_page_cache_is_reused(tmp_path: Path) -> None:
    product_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal product_requests
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=fixture("robots_allowed.txt"))
        product_requests += 1
        return httpx.Response(
            200,
            text=fixture("product_offer.html"),
            headers={"content-type": "text/html"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    crawler = EthicalPageCrawler(crawler_config(), client=client, cache_dir=tmp_path / "cache")

    def extractor(html: str, url: str) -> list[RawOffer]:
        return parse_jsonld_product_page(html, url)

    try:
        first = await crawler.crawl(
            source="fixture",
            url=PRODUCT_URL,
            static_extractor=extractor,
            embedded_extractor=lambda _html, _url: [],
        )
        second = await crawler.crawl(
            source="fixture",
            url=PRODUCT_URL,
            static_extractor=extractor,
            embedded_extractor=lambda _html, _url: [],
        )
    finally:
        await client.aclose()
    assert first.fetch.cached is False
    assert second.fetch.cached is True
    assert product_requests == 1


@pytest.mark.asyncio
async def test_per_domain_request_starts_are_throttled(tmp_path: Path) -> None:
    starts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=fixture("robots_allowed.txt"))
        starts.append(time.monotonic())
        return httpx.Response(
            200,
            text=fixture("product_offer.html"),
            headers={"content-type": "text/html"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    crawler = EthicalPageCrawler(
        crawler_config(minimum_request_interval_seconds=0.05),
        client=client,
        cache_dir=tmp_path / "cache",
    )
    diagnostics: list[SourceDiagnostic] = []
    await crawler.evaluate_robots("fixture", PRODUCT_URL, diagnostics)
    try:
        await asyncio.gather(
            crawler.crawl(
                source="fixture",
                url="https://shop.example.test/products/one",
                static_extractor=lambda _html, url: [raw_offer(url)],
                embedded_extractor=lambda _html, _url: [],
            ),
            crawler.crawl(
                source="fixture",
                url="https://shop.example.test/products/two",
                static_extractor=lambda _html, url: [raw_offer(url)],
                embedded_extractor=lambda _html, _url: [],
            ),
        )
    finally:
        await client.aclose()
    assert len(starts) == 2
    assert starts[1] - starts[0] >= 0.045


def test_nested_product_group_and_price_specification_are_parsed() -> None:
    offers = parse_jsonld_product_page(fixture("product_group_offer.html"), PRODUCT_URL, "GNC")
    assert len(offers) == 2
    assert {offer.listed_price for offer in offers} == {
        Decimal("29.99"),
        Decimal("28.99"),
    }
    assert all(offer.canonical_brand == "Ghost" for offer in offers)


def test_next_data_fixture_is_parsed_without_unrelated_state() -> None:
    offers = extract_embedded_product_data(
        fixture("next_product.html"), PRODUCT_URL, "Fixture retailer"
    )
    assert len(offers) == 1
    assert offers[0].source_product_id == "NEXT-ALANI-12"
    assert offers[0].listed_price == Decimal("19.99")


def test_mandatory_safety_controls_cannot_be_disabled() -> None:
    with pytest.raises(ValidationError):
        CrawlerConfig.model_validate(
            {
                "respect_robots_txt": False,
                "stop_on_captcha": False,
                "stop_on_access_denied": False,
            }
        )
