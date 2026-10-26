"""Read Infinit's public application history without authenticating.

The application exposes read-only Next.js actions, not a versioned REST API.
Action identities are resolved from each build's public bundles. Response
errors never become an empty successful history. Source: https://app.infinit.tech/.
"""

import datetime
import json
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from eth_defi.erc_4626.vault_protocol.infinit.constants import APEX_PRODUCT, INFINIT_APP, InfinitProduct
from eth_defi.hyperliquid.session import create_hyperliquid_session

logger = logging.getLogger(__name__)
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


@dataclass(slots=True, frozen=True)
class InfinitPricePoint:
    """One original, unrounded application observation."""

    #: Naive UTC source timestamp.
    timestamp: datetime.datetime
    #: USDC per receipt token; includes raw quarantined placeholders.
    price: Decimal


@dataclass(slots=True, frozen=True)
class InfinitHistory:
    """Validated response and its retained audit evidence."""

    #: Application identity, matched to the reviewed contract mapping.
    application_id: str
    #: Sorted exact source observations.
    points: tuple[InfinitPricePoint, ...]
    #: Public bundle action identity for provenance.
    action_id: str
    #: Original complete response, retained without rounding JSON numbers.
    raw_response: str = field(repr=False)


def create_infinit_session() -> requests.Session:
    """Create a bounded public-read session.

    Retrying POST is safe here because only named read-only actions are used.

    :return: Caller-owned HTTP session with finite retries.
    """
    session = requests.Session()
    retry = Retry(total=2, backoff_factor=0.5, status_forcelist=(429, 502, 503, 504), allowed_methods=("GET", "POST"), respect_retry_after_header=False)
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _fetch_text(session: requests.Session, method: str, url: str, **kwargs: object) -> str:
    """Read one size-limited response with finite inactivity timeouts.

    :param session: Caller-owned session.
    :param method: Public GET or a read-only action POST.
    :param url: Public application URL.
    :param kwargs: Request headers and body.
    :return: UTF-8 response text.
    """
    with session.request(method, url, timeout=(10, 30), stream=True, **kwargs) as response:
        response.raise_for_status()
        chunks = []
        size = 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                msg = "Infinit response exceeds the size limit"
                raise ValueError(msg)
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8")


@lru_cache(maxsize=8)
def _fetch_action_for_build(bundle_urls: tuple[str, ...], action_name: str) -> str:
    """Resolve one action from immutable build-specific bundle URLs.

    Cache keys change when Next.js replaces the application build.

    :param bundle_urls: Script URLs in page order.
    :param action_name: Explicitly selected public read action.
    :return: Deployed action identifier.
    """
    # Do not cross a closing call into a neighbouring server reference: the
    # product-detail and performance actions occur next to each other.
    expression = re.compile(r'createServerReference\)\("([a-f0-9]+)",[^()\n]{0,180}"' + re.escape(action_name) + r'"\)')
    with create_infinit_session() as session:
        for url in reversed(bundle_urls):
            text = _fetch_text(session, "GET", url)
            match = expression.search(text)
            if match:
                return match.group(1)
    raise ValueError(f"Infinit public action {action_name} is absent from this build")


def parse_history_response(text: str, product: InfinitProduct = APEX_PRODUCT, action_id: str = "fixture") -> InfinitHistory:
    """Validate a complete Flight response containing published unit prices.

    Duplicate identical observations are harmless; conflicting ones are errors.
    Quarantined values are retained here and excluded only by the reader.

    :param text: Original text/x-component action response.
    :param product: Reviewed product whose UUID must match.
    :param action_id: Public action used to retrieve this payload.
    :return: Validated, sorted source observations.
    """
    payloads = []
    for line in text.splitlines():
        if re.match(r"^[0-9a-f]+:\{", line):
            value = json.loads(line.split(":", 1)[1], parse_float=Decimal)
            if "vaultId" in value and "data" in value:
                payloads.append(value)
    if len(payloads) != 1 or payloads[0]["vaultId"] != product.application_id:
        msg = "Infinit history has no unique matching product identity"
        raise ValueError(msg)
    data = payloads[0]["data"]
    if not isinstance(data, list) or not data:
        msg = "Infinit history is empty or malformed"
        raise ValueError(msg)
    points = {}
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("timestamp"), str) or "unitPrice" not in item:
            message = "Infinit history observation is malformed"
            raise ValueError(message)
        timestamp = datetime.datetime.fromisoformat(item["timestamp"])
        if timestamp.tzinfo is None:
            msg = "Infinit source timestamps must specify UTC"
            raise ValueError(msg)
        timestamp = timestamp.astimezone(datetime.UTC).replace(tzinfo=None)
        try:
            price = Decimal(str(item["unitPrice"]))
        except InvalidOperation as error:
            message = "Infinit source price is malformed"
            raise ValueError(message) from error
        if not price.is_finite() or price < 0:
            msg = "Infinit source price is negative or non-finite"
            raise ValueError(msg)
        if timestamp in points and points[timestamp] != price:
            msg = "Infinit source contains conflicting duplicate timestamps"
            raise ValueError(msg)
        points[timestamp] = price
    return InfinitHistory(product.application_id, tuple(InfinitPricePoint(t, p) for t, p in sorted(points.items())), action_id, text)


def fetch_infinit_history(product: InfinitProduct = APEX_PRODUCT) -> InfinitHistory:
    """Retrieve the complete history currently retained by the public app.

    No wallet, account credential, or transaction action is involved. The API
    is undocumented and a changed response fails before persistent writes.

    :param product: Reviewed product to fetch.
    :return: Validated raw observations and request provenance.
    """
    url = f"{INFINIT_APP}/vaults/{product.slug}"
    with create_infinit_session() as session:
        page = _fetch_text(session, "GET", url)
        bundles = tuple(dict.fromkeys(urljoin(INFINIT_APP, src) for src in re.findall(r'<script[^>]+src="([^"]+)"', page)))
        if not bundles:
            msg = "Infinit page has no public application bundles"
            raise ValueError(msg)
        action = _fetch_action_for_build(bundles, "getVaultPerformanceHistory")
        text = _fetch_text(session, "POST", url, headers={"Next-Action": action, "Accept": "text/x-component", "Origin": INFINIT_APP, "Content-Type": "text/plain;charset=UTF-8"}, data=json.dumps([product.slug, "all"]))
    history = parse_history_response(text, product, action)
    logger.info("Infinit %s: fetched %d observations, %s to %s", product.slug, len(history.points), history.points[0].timestamp, history.points[-1].timestamp)
    return history


def fetch_infinit_trading_equity(product: InfinitProduct = APEX_PRODUCT) -> Decimal:
    """Read the reviewed account's native and HIP-3 account equity.

    ``marginSummary`` includes isolated margin. ``crossMarginSummary`` alone
    would undercount this strategy. Source: Hyperliquid's public info endpoint,
    https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint.

    :param product: Reviewed account mapping.
    :return: Combined USDC account equity, for validation rather than pricing.
    """
    total = Decimal(0)
    with create_hyperliquid_session() as session:
        for dex in ("", "xyz"):
            with session.post_info({"type": "clearinghouseState", "user": product.trading_account, "dex": dex}, timeout=20) as response:
                response.raise_for_status()
                amount = Decimal(response.json()["marginSummary"]["accountValue"])
                if not amount.is_finite():
                    msg = "Infinit Hyperliquid account equity is non-finite"
                    raise ValueError(msg)
                total += amount
    return total
