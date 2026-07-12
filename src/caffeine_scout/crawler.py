"""Reusable progressive crawler with a strict, non-bypassable access policy."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from caffeine_scout.config import CrawlerConfig
from caffeine_scout.models import (
    CrawlResult,
    FetchResult,
    RawOffer,
    RobotsDecision,
    SourceDiagnostic,
)

HtmlExtractor = Callable[[str, str], list[RawOffer]]
JsonExtractor = Callable[[Any, str, str], list[RawOffer]]
PlaywrightLoader = Callable[[str], Awaitable[tuple[str, list[tuple[str, Any]]]]]


class EthicalPageCrawler:
    """Run permitted public-page extraction stages without access-control evasion."""

    def __init__(
        self,
        config: CrawlerConfig,
        *,
        client: httpx.AsyncClient | None = None,
        cache_dir: Path | None = None,
        playwright_loader: PlaywrightLoader | None = None,
    ) -> None:
        self.config = config
        self.cache_dir = cache_dir or Path(".caffeine_scout_cache")
        self._client = client or httpx.AsyncClient(
            headers={"User-Agent": config.effective_user_agent},
            timeout=config.request_timeout_seconds,
            follow_redirects=True,
        )
        self._owns_client = client is None
        self._playwright_loader = playwright_loader or self._load_with_playwright
        self._robots_cache: dict[str, RobotsDecision] = {}
        self._domain_semaphores: dict[str, asyncio.Semaphore] = {}
        self._domain_rate_locks: dict[str, asyncio.Lock] = {}
        self._last_request_started: dict[str, float] = {}
        self._blocked_until: dict[str, float] = {}

    async def __aenter__(self) -> EthicalPageCrawler:
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def crawl(
        self,
        *,
        source: str,
        url: str,
        static_extractor: HtmlExtractor,
        embedded_extractor: HtmlExtractor,
        rendered_extractor: HtmlExtractor | None = None,
        public_json_extractor: JsonExtractor | None = None,
    ) -> CrawlResult:
        diagnostics: list[SourceDiagnostic] = []
        robots = await self.evaluate_robots(source, url, diagnostics)
        fetch = FetchResult(
            requested_url=url,
            robots_decision=robots,
            fetched_at=datetime.now(UTC),
            diagnostics=diagnostics,
        )
        diagnostics = fetch.diagnostics
        if robots.decision == "disallowed":
            return self._failed(fetch, "explicitly_disallowed_by_robots")
        if robots.decision == "unknown" and not self.config.allow_when_robots_unavailable:
            return self._failed(fetch, "robots_unavailable")

        cached = self._load_cache(url)
        html: str
        if cached is not None:
            html, metadata, snapshot_path = cached
            fetch.final_url = str(metadata.get("final_url") or url)
            fetch.status_code = int(metadata.get("status_code") or 200)
            fetch.cached = True
            fetch.raw_snapshot_path = str(snapshot_path)
            diagnostics.append(
                self._diagnostic(source, "static_http", True, "cache_hit", cached=True)
            )
        else:
            response = await self._fetch_public_page(
                source,
                url,
                diagnostics,
                allow_retries=robots.decision != "unknown",
            )
            if isinstance(response, tuple):
                reason, status_code = response
                fetch.status_code = status_code
                fetch.blocked_reason = reason
                return self._failed(fetch, reason)
            fetch.final_url = str(response.url)
            fetch.status_code = response.status_code
            fetch.fetched_at = datetime.now(UTC)
            content_type = response.headers.get("content-type", "").split(";", 1)[0]
            diagnostics.append(
                self._diagnostic(
                    source,
                    "static_http",
                    True,
                    "public_page_fetched",
                    status=response.status_code,
                    content_type=content_type,
                    response_bytes=len(response.content),
                )
            )
            if content_type and not (
                content_type.startswith("text/html") or content_type.startswith("application/xhtml")
            ):
                fetch.blocked_reason = "unsupported_content_type"
                return self._failed(fetch, "unsupported_content_type")
            html = response.text
            blocked = self._detect_blocked_page(html)
            if blocked:
                diagnostics.append(self._diagnostic(source, "static_http", False, blocked))
                fetch.blocked_reason = blocked
                return self._failed(fetch, blocked)
            fetch.raw_snapshot_path = str(self._store_cache(url, response, html))

        offers = self._extract(
            source, "static_structured_data", static_extractor, html, url, diagnostics
        )
        if offers:
            fetch.method_used = "static"
            return CrawlResult(fetch=fetch, offers=offers)

        offers = self._extract(
            source, "embedded_application_data", embedded_extractor, html, url, diagnostics
        )
        if offers:
            fetch.method_used = "embedded_json"
            return CrawlResult(fetch=fetch, offers=offers)

        try:
            rendered_html, public_json = await self._playwright_loader(url)
        except Exception as exc:
            diagnostics.append(
                self._diagnostic(
                    source,
                    "playwright",
                    False,
                    "rendering_unavailable",
                    error_type=type(exc).__name__,
                )
            )
            return self._failed(fetch, "unsupported_rendering_structure")

        rendered_block = self._detect_blocked_page(rendered_html)
        if rendered_block:
            diagnostics.append(self._diagnostic(source, "playwright", False, rendered_block))
            fetch.blocked_reason = rendered_block
            return self._failed(fetch, rendered_block)

        extractor = rendered_extractor or static_extractor
        offers = self._extract(source, "playwright_dom", extractor, rendered_html, url, diagnostics)
        if not offers:
            offers = self._extract(
                source,
                "playwright_embedded_data",
                embedded_extractor,
                rendered_html,
                url,
                diagnostics,
            )
        if offers:
            fetch.method_used = "playwright_dom"
            return CrawlResult(fetch=fetch, offers=offers)

        if self.config.observe_public_product_json and public_json_extractor:
            for endpoint, payload in public_json:
                try:
                    offers.extend(public_json_extractor(payload, url, endpoint))
                except (KeyError, TypeError, ValueError) as exc:
                    diagnostics.append(
                        self._diagnostic(
                            source,
                            "public_first_party_json",
                            False,
                            "parse_failure",
                            endpoint=self._safe_url(endpoint),
                            error_type=type(exc).__name__,
                        )
                    )
            diagnostics.append(
                self._diagnostic(
                    source,
                    "public_first_party_json",
                    bool(offers),
                    "offers_extracted" if offers else "no_public_price_data",
                    responses_observed=len(public_json),
                    offers_found=len(offers),
                )
            )
            if offers:
                fetch.method_used = "public_first_party_json"
                return CrawlResult(fetch=fetch, offers=offers)

        return self._failed(fetch, "no_public_price_data_found")

    async def evaluate_robots(
        self, source: str, url: str, diagnostics: list[SourceDiagnostic]
    ) -> RobotsDecision:
        parts = urlsplit(url)
        domain = parts.netloc.casefold()
        cached = self._robots_cache.get(domain)
        if cached is not None:
            diagnostics.append(
                self._diagnostic(
                    source,
                    "robots",
                    cached.decision != "disallowed",
                    f"robots_{cached.decision}_cached",
                    decision=cached.decision,
                )
            )
            return cached

        robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        checked_at = datetime.now(UTC)
        try:
            response = await self._get_with_retry(robots_url)
        except httpx.HTTPError as exc:
            decision = RobotsDecision(
                domain=domain,
                robots_url=robots_url,
                status_code=None,
                decision="unknown",
                checked_at=checked_at,
                explanation=f"robots.txt unavailable ({type(exc).__name__})",
            )
        else:
            if response.status_code == 200:
                parser = RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(response.text.splitlines())
                allowed = parser.can_fetch(self.config.effective_user_agent, url)
                decision = RobotsDecision(
                    domain=domain,
                    robots_url=robots_url,
                    status_code=200,
                    decision="allowed" if allowed else "disallowed",
                    matched_rule=self._matched_rule(response.text, parts.path, allowed),
                    checked_at=checked_at,
                    explanation=(
                        "exact product path is permitted by robots.txt"
                        if allowed
                        else "exact product path is explicitly disallowed by robots.txt"
                    ),
                )
            else:
                decision = RobotsDecision(
                    domain=domain,
                    robots_url=robots_url,
                    status_code=response.status_code,
                    decision="unknown",
                    checked_at=checked_at,
                    explanation=(f"robots.txt returned {response.status_code}; policy is unknown"),
                )
        self._robots_cache[domain] = decision
        diagnostics.append(
            self._diagnostic(
                source,
                "robots",
                decision.decision != "disallowed",
                f"robots_{decision.decision}",
                status=decision.status_code,
                decision=decision.decision,
                matched_rule=decision.matched_rule,
            )
        )
        return decision

    async def _fetch_public_page(
        self,
        source: str,
        url: str,
        diagnostics: list[SourceDiagnostic],
        *,
        allow_retries: bool,
    ) -> httpx.Response | tuple[str, int | None]:
        try:
            response = await self._get_with_retry(url, max_attempts=3 if allow_retries else 1)
        except httpx.HTTPError as exc:
            diagnostics.append(
                self._diagnostic(
                    source,
                    "static_http",
                    False,
                    "network_error",
                    error_type=type(exc).__name__,
                )
            )
            return "network_error", None
        status = response.status_code
        if status == 401:
            reason = "authentication_required"
        elif status == 403:
            reason = "product_page_refused_access"
        elif status == 429:
            reason = "rate_limited"
            retry_after = self._retry_after_seconds(response)
            if retry_after is not None:
                self._blocked_until[urlsplit(url).netloc.casefold()] = (
                    time.monotonic() + retry_after
                )
                diagnostics.append(
                    self._diagnostic(
                        source,
                        "static_http",
                        False,
                        reason,
                        status=status,
                        retry_after_seconds=retry_after,
                    )
                )
                return reason, status
        elif status in {404, 410}:
            reason = "product_page_not_found"
        elif status >= 400:
            reason = "product_page_http_error"
        else:
            return response
        diagnostics.append(self._diagnostic(source, "static_http", False, reason, status=status))
        return reason, status

    async def _get_with_retry(self, url: str, *, max_attempts: int = 3) -> httpx.Response:
        response: httpx.Response | None = None
        for attempt in range(max_attempts):
            response = await self._throttled_get(url)
            if not 500 <= response.status_code <= 599 or attempt == max_attempts - 1:
                return response
            await asyncio.sleep(2**attempt)
        if response is None:  # pragma: no cover - loop always executes
            raise RuntimeError("request loop did not execute")
        return response

    async def _throttled_get(self, url: str) -> httpx.Response:
        domain = urlsplit(url).netloc.casefold()
        semaphore = self._domain_semaphores.setdefault(
            domain, asyncio.Semaphore(self.config.per_domain_concurrency)
        )
        rate_lock = self._domain_rate_locks.setdefault(domain, asyncio.Lock())
        async with semaphore:
            async with rate_lock:
                now = time.monotonic()
                wait_until = max(
                    self._last_request_started.get(domain, 0)
                    + self.config.minimum_request_interval_seconds,
                    self._blocked_until.get(domain, 0),
                )
                if wait_until > now:
                    await asyncio.sleep(wait_until - now)
                self._last_request_started[domain] = time.monotonic()
            return await self._client.get(url)

    def _extract(
        self,
        source: str,
        stage: str,
        extractor: HtmlExtractor,
        html: str,
        url: str,
        diagnostics: list[SourceDiagnostic],
    ) -> list[RawOffer]:
        try:
            offers = extractor(html, url)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            diagnostics.append(
                self._diagnostic(
                    source,
                    stage,
                    False,
                    "parse_failure",
                    error_type=type(exc).__name__,
                )
            )
            return []
        diagnostics.append(
            self._diagnostic(
                source,
                stage,
                bool(offers),
                "offers_extracted" if offers else "no_public_price_data",
                offers_found=len(offers),
            )
        )
        return offers

    async def _load_with_playwright(self, url: str) -> tuple[str, list[tuple[str, Any]]]:
        observed: list[tuple[str, Any]] = []
        tasks: list[asyncio.Task[None]] = []
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page(user_agent=self.config.effective_user_agent)

                async def capture(response: Any) -> None:
                    if not self.config.observe_public_product_json:
                        return
                    if not self._is_first_party(url, str(response.url)):
                        return
                    headers = response.headers
                    content_type = headers.get("content-type", "").casefold()
                    request_headers = response.request.headers
                    if (
                        response.status != 200
                        or "json" not in content_type
                        or "authorization" in request_headers
                    ):
                        return
                    content_length = headers.get("content-length")
                    if content_length and int(content_length) > 5_000_000:
                        return
                    try:
                        payload = await response.json()
                    except Exception:
                        return
                    observed.append((self._safe_url(str(response.url)), payload))

                def schedule(response: Any) -> None:
                    tasks.append(asyncio.create_task(capture(response)))

                page.on("response", schedule)
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=int(self.config.playwright_timeout_seconds * 1000),
                )
                with suppress(PlaywrightTimeoutError):
                    await page.wait_for_load_state(
                        "networkidle",
                        timeout=int(self.config.playwright_timeout_seconds * 1000),
                    )
                html = await page.content()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                return html, observed
            finally:
                await browser.close()

    def _load_cache(self, url: str) -> tuple[str, dict[str, Any], Path] | None:
        key = hashlib.sha256(url.encode()).hexdigest()
        body_path = self.cache_dir / f"{key}.html"
        metadata_path = self.cache_dir / f"{key}.json"
        if not body_path.exists() or not metadata_path.exists():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            fetched_at = datetime.fromisoformat(str(metadata["fetched_at"]))
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return None
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=UTC)
        if datetime.now(UTC) - fetched_at > timedelta(hours=self.config.response_cache_hours):
            return None
        try:
            return body_path.read_text(encoding="utf-8"), metadata, body_path
        except OSError:
            return None

    def _store_cache(self, url: str, response: httpx.Response, html: str) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(url.encode()).hexdigest()
        body_path = self.cache_dir / f"{key}.html"
        metadata_path = self.cache_dir / f"{key}.json"
        body_path.write_text(html, encoding="utf-8")
        metadata_path.write_text(
            json.dumps(
                {
                    "requested_url": url,
                    "final_url": str(response.url),
                    "status_code": response.status_code,
                    "fetched_at": datetime.now(UTC).isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return body_path

    @staticmethod
    def _detect_blocked_page(html: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        for element in soup(["script", "style", "noscript"]):
            element.decompose()
        title = soup.title.get_text(" ", strip=True).casefold() if soup.title else ""
        heading = " ".join(
            element.get_text(" ", strip=True) for element in soup.select("h1, h2")[:3]
        ).casefold()
        visible = f"{title} {heading} {soup.get_text(' ', strip=True)[:5000].casefold()}"
        if any(
            marker in visible
            for marker in (
                "verify you are human",
                "complete the captcha",
                "captcha challenge",
                "hcaptcha",
                "recaptcha challenge",
            )
        ):
            return "captcha_encountered"
        if any(
            marker in f"{title} {heading}"
            for marker in ("access denied", "request blocked", "forbidden", "not authorized")
        ):
            return "product_page_refused_access"
        if any(
            marker in f"{title} {heading}"
            for marker in ("sign in to continue", "login required", "authentication required")
        ):
            return "authentication_required"
        return None

    @staticmethod
    def _matched_rule(robots_text: str, path: str, allowed: bool) -> str | None:
        directive = "allow" if allowed else "disallow"
        candidates: list[str] = []
        for line in robots_text.splitlines():
            stripped = line.split("#", 1)[0].strip()
            if not stripped.casefold().startswith(f"{directive}:"):
                continue
            rule = stripped.split(":", 1)[1].strip()
            if rule and path.startswith(rule.rstrip("*")):
                candidates.append(f"{directive.title()}: {rule}")
        return max(candidates, key=len) if candidates else None

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> float | None:
        value = response.headers.get("retry-after")
        if value is None:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            return None

    @staticmethod
    def _is_first_party(page_url: str, response_url: str) -> bool:
        page_host = (urlsplit(page_url).hostname or "").casefold()
        response_host = (urlsplit(response_url).hostname or "").casefold()
        page_root = ".".join(page_host.split(".")[-2:])
        return bool(page_root) and (
            response_host == page_host or response_host.endswith(f".{page_root}")
        )

    @staticmethod
    def _safe_url(url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    @staticmethod
    def _diagnostic(
        source: str,
        stage: str,
        success: bool,
        reason: str,
        **details: str | int | float | bool | None,
    ) -> SourceDiagnostic:
        return SourceDiagnostic(
            source=source,
            stage=stage,
            success=success,
            reason=reason,
            details=details,
        )

    @staticmethod
    def _failed(fetch: FetchResult, reason: str) -> CrawlResult:
        if fetch.blocked_reason is None and reason in {
            "explicitly_disallowed_by_robots",
            "robots_unavailable",
            "product_page_refused_access",
            "captcha_encountered",
            "authentication_required",
            "rate_limited",
        }:
            fetch.blocked_reason = reason
        return CrawlResult(fetch=fetch, failure_reason=reason)
