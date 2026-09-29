"""Generic HTML connector.

Kept deliberately configuration-driven rather than site-specific: CSS selectors
live in ``competitor.connector_config`` so onboarding a new site is a database
row, not a code change.

Fetching a URL that an operator supplies is a server-side request forgery risk,
so this connector is closed by default. It will only fetch a host that appears
in ``DSS_HTTP_CONNECTOR_ALLOWLIST``, and it re-validates after DNS resolution so
an allowlisted name that resolves to a private address is still refused.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import socket
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.parser import HTMLParser

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.enums import StockState
from app.models.market import Competitor
from app.services.connectors.base import (
    ConnectorError,
    ConnectorResult,
    RawListing,
    RawObservation,
)

logger = get_logger(__name__)

#: Refuse a response larger than this; a scraper should never need more.
MAX_RESPONSE_BYTES = 5 * 1024 * 1024

_PRICE_CHARS = set("0123456789.")
_OUT_OF_STOCK_HINTS = ("out of stock", "sold out", "unavailable", "backorder", "pre-order")
_IN_STOCK_HINTS = ("in stock", "available", "add to cart", "buy now")


def _is_public_address(host: str) -> bool:
    """False for loopback, private, link-local and reserved addresses."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            return False
    return True


def assert_fetchable(url: str, allowlist: tuple[str, ...]) -> str:
    """Validate a URL against the allowlist and DNS. Returns the hostname."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ConnectorError(f"Refusing scheme {parsed.scheme!r}; only http(s) is allowed.")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ConnectorError(f"URL has no hostname: {url!r}")
    if not allowlist:
        raise ConnectorError(
            "The HTTP connector is disabled. Set DSS_HTTP_CONNECTOR_ALLOWLIST to the "
            "hostnames you are authorised to crawl."
        )
    if host not in allowlist:
        raise ConnectorError(f"Host {host!r} is not in DSS_HTTP_CONNECTOR_ALLOWLIST.")
    if not _is_public_address(host):
        raise ConnectorError(f"Host {host!r} resolves to a non-public address; refusing to fetch.")
    return host


def parse_price(text: str) -> Decimal | None:
    """Pull a decimal out of '$1,299.00' / '1 299,00 EUR' style text."""
    if not text:
        return None
    cleaned = text.strip().replace("\xa0", " ")
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")  # European decimal comma
    else:
        cleaned = cleaned.replace(",", "")
    digits = "".join(ch for ch in cleaned if ch in _PRICE_CHARS)
    # A trailing/leading dot, or several, means we grabbed more than one number.
    digits = digits.strip(".")
    if not digits or digits.count(".") > 1:
        return None
    try:
        value = Decimal(digits)
    except InvalidOperation:
        return None
    return value if value > 0 else None


def parse_stock(text: str) -> StockState:
    lowered = text.lower()
    if any(hint in lowered for hint in _OUT_OF_STOCK_HINTS):
        return StockState.OUT_OF_STOCK
    if any(hint in lowered for hint in _IN_STOCK_HINTS):
        return StockState.IN_STOCK
    return StockState.UNKNOWN


class HttpConnector:
    """Scrapes a listing page using CSS selectors from the competitor config."""

    name = "http"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.settings = get_settings()
        self._client = client

    def _get(self, url: str) -> str:
        allowlist = self.settings.allowed_connector_hosts
        host = assert_fetchable(url, allowlist)

        client = self._client or httpx.Client(
            timeout=self.settings.http_connector_timeout_seconds,
            # Redirects are followed manually so each hop is re-validated; an
            # allowlisted host must not be able to bounce us onto an internal one.
            follow_redirects=False,
            headers={"User-Agent": "DSS-PriceMonitor/0.1 (+research project)"},
        )
        try:
            current = url
            for _ in range(5):
                response = client.get(current)
                if response.is_redirect:
                    current = urljoin(current, response.headers.get("location", ""))
                    assert_fetchable(current, allowlist)
                    continue
                response.raise_for_status()
                content = response.content[: MAX_RESPONSE_BYTES + 1]
                if len(content) > MAX_RESPONSE_BYTES:
                    raise ConnectorError(f"{host} returned more than {MAX_RESPONSE_BYTES} bytes.")
                return content.decode(response.encoding or "utf-8", errors="replace")
            raise ConnectorError(f"Too many redirects starting at {url!r}.")
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Fetching {url!r} failed: {exc}") from exc
        finally:
            if self._client is None:
                client.close()

    @staticmethod
    def _text(node: Any, selector: str | None) -> str:
        if not selector:
            return ""
        found = node.css_first(selector)
        return found.text(strip=True) if found else ""

    def fetch(self, competitor: Competitor, *, now: dt.datetime) -> ConnectorResult:
        config = competitor.connector_config or {}
        list_url = str(config.get("list_url", "")).strip()
        item_selector = str(config.get("item_selector", "")).strip()
        if not list_url or not item_selector:
            raise ConnectorError(
                f"Competitor '{competitor.slug}' needs connector_config "
                "'list_url' and 'item_selector'."
            )

        tree = HTMLParser(self._get(list_url))
        result = ConnectorResult()

        for index, node in enumerate(tree.css(item_selector)):
            title = self._text(node, config.get("title_selector")) or node.text(strip=True)[:200]
            price = parse_price(self._text(node, config.get("price_selector")))
            if price is None:
                result.errors.append(f"item {index}: no parsable price, skipped")
                continue

            link_node = node.css_first(str(config.get("url_selector") or "a"))
            href = link_node.attributes.get("href") if link_node else None
            url = urljoin(list_url, href) if href else list_url
            external_id = str(config.get("id_prefix", "")) + (
                self._text(node, config.get("id_selector"))
                or urlparse(url).path.strip("/")
                or f"item-{index}"
            )

            result.listings.append(
                RawListing(
                    external_id=external_id,
                    title=title,
                    brand=self._text(node, config.get("brand_selector")),
                    gtin=self._text(node, config.get("gtin_selector")) or None,
                    url=url,
                )
            )
            result.observations.append(
                RawObservation(
                    external_id=external_id,
                    observed_at=now,
                    price=price,
                    shipping_cost=parse_price(self._text(node, config.get("shipping_selector")))
                    or Decimal("0"),
                    currency=str(config.get("currency", "USD")),
                    stock_state=parse_stock(
                        self._text(node, config.get("stock_selector")) or node.text()
                    ),
                    raw_payload={"source": self.name, "url": url},
                )
            )

        logger.info("HTTP connector scraped %s: %s", list_url, result.summary())
        return result
