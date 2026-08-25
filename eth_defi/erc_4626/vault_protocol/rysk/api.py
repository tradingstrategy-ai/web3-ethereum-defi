"""Rysk Premium public pool catalogue client.

The scheduled vault pipeline discovers and prices Rysk pools from onchain
events.  The application catalogue remains useful for operator tools that need
the current user-facing product set without scanning a chain first.
"""

from dataclasses import dataclass

import requests
from eth_typing import HexAddress
from eth_utils import is_hex_address

RYSK_PREMIUM_POOLS_URL = "https://premium.rysk.finance/api/pools"
RYSK_PREMIUM_API_TIMEOUT = 20.0


class RyskPremiumAPIError(RuntimeError):
    """Raised when the Rysk Premium catalogue response is unavailable or invalid."""


@dataclass(slots=True, frozen=True)
class RyskPremiumPool:
    """Describe one product returned by the Rysk Premium application.

    The catalogue is an operator-tool convenience rather than a source of
    scanner classification or share-price history.
    """

    #: EVM chain containing the pool.
    chain_id: int
    #: Pool and ERC-20 LP share-token address.
    address: HexAddress
    #: Curator-facing product name.
    name: str
    #: Curator-facing product description, when supplied.
    description: str | None
    #: Advertised option-writing direction.
    option_type: str


def _as_int(value: object, field: str) -> int:
    """Parse a required integer-like catalogue field.

    Rysk serialises some numeric values as decimal strings. Invalid values are
    translated into one integration-specific exception for callers.

    :param value:
        Raw JSON value.
    :param field:
        Field name used in diagnostics.
    :return:
        Parsed integer.
    """

    try:
        return int(str(value))
    except (TypeError, ValueError) as error:
        raise RyskPremiumAPIError(f"Rysk Premium API field {field} is not an integer: {value!r}") from error


def _as_address(value: object, field: str) -> HexAddress:
    """Normalise a required EVM address from the catalogue.

    The application currently publishes lower-case hexadecimal addresses. The
    parser validates the shape before the identity reaches operator tooling.

    :param value:
        Raw JSON value.
    :param field:
        Field name used in diagnostics.
    :return:
        Lower-case EVM address.
    """

    text = str(value or "").lower()
    if not is_hex_address(text):
        raise RyskPremiumAPIError(f"Rysk Premium API field {field} is not an EVM address: {value!r}")
    return HexAddress(text)


def is_rysk_premium_test_pool(pool: RyskPremiumPool) -> bool:
    """Identify catalogue products labelled as internal or test-only.

    The public endpoint includes operational test products. Operator tools use
    the issuer's labels to keep those products out of user-facing reports.

    :param pool:
        Catalogue product to inspect.
    :return:
        ``True`` for an issuer-labelled internal or test product.
    """

    return pool.name.lower().startswith("rysk internal") or pool.description == "For test purposes only"


def fetch_rysk_premium_pools(*, session: requests.Session | None = None) -> tuple[RyskPremiumPool, ...]:
    """Fetch the current Rysk Premium application catalogue.

    This unauthenticated endpoint is an application API, not a versioned
    developer API. The scheduled scanner does not depend on it; backfill and
    examination tools use it only to enumerate current public products. See
    the official `Premium application <https://app.rysk.finance/premium/>`__.

    :param session:
        Optional persistent HTTP session.
    :return:
        Validated catalogue products across all published EVM chains.
    :raise RyskPremiumAPIError:
        If transport, JSON decoding or response validation fails.
    """

    client = session or requests
    try:
        response = client.get(RYSK_PREMIUM_POOLS_URL, timeout=RYSK_PREMIUM_API_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as error:
        raise RyskPremiumAPIError(f"Rysk Premium catalogue request failed: {error}") from error
    if not isinstance(payload, list):
        raise RyskPremiumAPIError(f"Rysk Premium pool catalogue must be a list, got {type(payload).__name__}")

    pools = []
    for raw in payload:
        if not isinstance(raw, dict):
            raise RyskPremiumAPIError(f"Rysk Premium pool entry must be an object, got {type(raw).__name__}")
        option_type = str(raw.get("type", "")).lower()
        if option_type not in {"call", "put"}:
            raise RyskPremiumAPIError(f"Rysk Premium pool has unsupported option type: {raw.get('type')!r}")
        pools.append(
            RyskPremiumPool(
                chain_id=_as_int(raw.get("chainId"), "chainId"),
                address=_as_address(raw.get("address"), "address"),
                name=str(raw.get("name") or "Rysk Premium pool"),
                description=str(raw["description"]) if raw.get("description") else None,
                option_type=option_type,
            )
        )
    return tuple(pools)
