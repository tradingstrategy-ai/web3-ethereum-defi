"""Read public Asseto product metadata from its web-application API.

Asseto's website exposes product information through an undocumented JSON API.
The API is useful for discovering current product descriptions, displayed TVL
and APY, but it is not a versioned developer interface. Use on-chain token
supply and ``Pricer`` contracts for canonical valuation.

The public endpoints used here are:

- ``GET /api/home/products`` — product registry
- ``GET /api/product/get`` — detailed product description
- ``GET /api/product/price/list`` — displayed historical price series

Reference: https://asseto.finance/product
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import requests
from eth_typing import HexAddress

logger = logging.getLogger(__name__)


#: Asseto web-application API origin.
ASSETO_API_BASE_URL = "https://asseto.finance"

#: Asseto API success code returned in JSON envelopes.
ASSETO_API_SUCCESS_CODE = 10_000

#: Timeout for a public Asseto API request.
DEFAULT_ASSETO_API_TIMEOUT = 20.0

#: Partner organisation names resolved from the public product API's logo URLs.
#:
#: Asseto exposes a partner role and logo URL, but not a text organisation name.
#: These mappings are deliberately exact so an unrecognised or changed logo is
#: surfaced as ``None`` instead of being guessed.
ASSETO_PARTNER_ORGANISATIONS_BY_LOGO_URL: dict[str, str] = {
    "https://static.asseto.finance/asseto/2026-02-01/dg392jckx5iud6zpxg.svg": "CMS Asset Management (HK)",
    "https://static.asseto.finance/asseto/2026-03-19/dh6virggiynbuf4t21.svg": "CMS Asset Management (HK)",
    "https://static.asseto.finance/asseto/2026-03-19/dh6vislm02h0udjoml.svg": "CMS Asset Management (HK)",
    "https://static.asseto.finance/asseto/2026-04-29/di5didybcyuypabu83.svg": "DFZQ / Orient Securities International",
    "https://static.asseto.finance/asseto/2026-03-19/dh6vej2bje9qdnvcrx.svg": "DL Holdings",
    "https://static.asseto.finance/asseto/2026-02-25/dgnvdq14496mckrjjs.svg": "Four Seasons",
    "https://static.asseto.finance/asseto/2026-02-01/dg3ccuy5kq0icjvmb1.svg": "Ogier",
    "https://static.asseto.finance/asseto/2026-02-01/dg3m1sh2qakpuofuls.svg": "Ogier",
    "https://static.asseto.finance/asseto/2026-04-28/di4ney6lbp8nncbmog.svg": "Ogier",
    "https://static.asseto.finance/asseto/2026-04-28/di4nfht721exvtcnfh.svg": "Ogier",
    "https://static.asseto.finance/asseto/2026-03-19/dh6viwd1w5vga7fyow.svg": "Guotai Haitong Securities",
    "https://static.asseto.finance/asseto/2026-03-19/dh6viy5gpe9osnj3uv.svg": "DBS",
    "https://static.asseto.finance/asseto/3_a6d22914-27ef-43de-8820-6dd6e6ded69a_1740491887.svg": "China CITIC Bank International",
    "https://static.asseto.finance/asseto/3_883aeba6-19b2-493f-afe7-196465dd3727_1740491891.svg": "Komainu",
    "https://static.asseto.finance/asseto/3_fc850198-10cb-4bf6-ac0b-443407985e31_1741070226.svg": "China CITIC Bank International",
    "https://static.asseto.finance/asseto/3_894f868c-1d82-4720-a6bf-3eaf66abf513_1741070229.svg": "Komainu",
    "https://static.asseto.finance/asseto/250918/dcvw2q0pbabijptu6r.svg": "Komainu",
    "https://static.asseto.finance/asseto/2026-04-28/di4nd2gbtx2fdogdvq.svg": "JunHe LLP",
    "https://static.asseto.finance/asseto/250918/dcvw451g68jhrw7ibq.png": "Harneys",
    "https://static.asseto.finance/asseto/2026-03-19/dh6vernck42mtc73wr.svg": "Vistra",
    "https://static.asseto.finance/asseto/2026-03-19/dh6vf4e5plwyumvdew.svg": "Howse Williams",
    "https://static.asseto.finance/asseto/2026-04-29/di5d7fnwts858eqeen.svg": "Bank of Communications Trustee Limited",
    "https://static.asseto.finance/asseto/2025-12-25/df750zdneb1gfnxhyx.png": "Mourant",
}


class AssetoAPIError(RuntimeError):
    """Raised when the public Asseto application API returns an error."""


@dataclass(slots=True, frozen=True)
class AssetoOffchainProduct:
    """An EVM Asseto product from the public product registry.

    The Asseto registry also has XRPL products. Those are intentionally omitted
    because this integration is for EVM ``VaultBase`` adapters.

    :ivar product_id:
        Asseto's off-chain product identifier.
    :ivar product_name:
        Product key required by the detail endpoint's ``productName`` header.
    :ivar contract_address:
        Token contract address used for matching an EVM vault adapter.
    """

    #: Asseto's off-chain product identifier.
    product_id: int

    #: Product key used in API request headers.
    product_name: str

    #: Human-readable product name.
    full_name: str | None

    #: Token symbol.
    symbol: str | None

    #: Asseto product category, such as ``uda`` or ``stoken``.
    product_type: str | None

    #: EVM chain id hosting the token.
    chain_id: int

    #: Human-readable EVM chain name.
    chain_name: str | None

    #: Token contract address.
    contract_address: HexAddress

    #: Product denomination token symbol.
    denomination_symbol: str | None

    #: Product denomination token address.
    denomination_address: HexAddress | None

    #: Asseto-displayed total value locked in the denomination.
    tvl: Decimal | None

    #: Asseto-displayed annual percentage yield as a percentage.
    apy: Decimal | None

    #: Short product introduction.
    introduction: str | None

    #: Product protocol and legal disclosure text.
    protocol: str | None


@dataclass(slots=True, frozen=True)
class AssetoProductDetail:
    """Detailed public metadata for one Asseto EVM product.

    :ivar product:
        Product metadata normalised from the detail response.
    :ivar description:
        Long-form product description supplied by Asseto.
    :ivar price:
        Latest displayed NAV/share. It is informational only; use the product's
        on-chain ``Pricer`` for scanner valuation.
    """

    #: Product metadata normalised from the detail response.
    product: AssetoOffchainProduct

    #: Long-form product description.
    description: str | None

    #: Informational displayed NAV/share.
    price: Decimal | None

    #: Informational displayed price change over 24 hours.
    price_24h: Decimal | None

    #: Asseto's method label for the displayed APY.
    apy_calculation_method: str | None


@dataclass(slots=True, frozen=True)
class AssetoRoleInfo:
    """One Asseto product partner role from the public application API.

    Asseto uses a role label and a logo URL for partner information. The
    application does not export a textual organisation name, so
    :attr:`organisation_name` is resolved only for known official logo assets.

    :ivar role:
        Asseto partner role, such as ``Investment Manager`` or
        ``Investment Advisor``.
    :ivar organisation_name:
        Organisation resolved from the official Asseto logo asset, or ``None``
        when the logo is unknown.
    :ivar logo_url:
        Asseto-hosted partner logo URL used for the resolution.
    """

    #: Asseto's role label for the partner.
    role: str

    #: Organisation name resolved from the logo, when known.
    organisation_name: str | None

    #: Asseto-hosted partner logo asset.
    logo_url: str


@dataclass(slots=True, frozen=True)
class AssetoPricePoint:
    """One Asseto display-price history observation.

    :ivar timestamp:
        Unix timestamp in seconds.
    :ivar value:
        Displayed NAV/share value in the product denomination.
    """

    #: Unix timestamp in seconds.
    timestamp: int

    #: Displayed NAV/share value.
    value: Decimal


def _as_decimal(value: object) -> Decimal | None:
    """Convert an optional Asseto numeric field to :class:`Decimal`.

    Asseto serialises dashboard values as strings and may emit empty values for
    products without a published metric.

    :param value:
        Raw JSON field value.
    :return:
        Parsed decimal, or ``None`` for an absent or malformed value.
    """

    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _require_success(response: requests.Response) -> dict:
    """Validate an Asseto application API response envelope.

    :param response:
        HTTP response returned by :mod:`requests`.
    :return:
        Decoded successful JSON envelope.
    :raise AssetoAPIError:
        If Asseto returns a non-success application code or malformed JSON.
    """

    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as error:
        message = "Asseto API returned invalid JSON"
        raise AssetoAPIError(message) from error

    if not isinstance(payload, dict):
        raise AssetoAPIError(f"Asseto API returned {type(payload).__name__}, expected object")

    if payload.get("code") != ASSETO_API_SUCCESS_CODE:
        message = payload.get("message", "unknown error")
        raise AssetoAPIError(f"Asseto API error {payload.get('code')}: {message}")

    return payload


def _parse_product(raw: dict) -> AssetoOffchainProduct | None:
    """Normalise a public Asseto product registry or detail row.

    :param raw:
        Raw product object from Asseto's application API.
    :return:
        EVM product metadata, or ``None`` for a non-EVM or malformed row.
    """

    raw_chain = raw.get("supportChains") or {}
    try:
        chain_id = int(raw_chain.get("chainId"))
        product_id = int(raw["id"])
    except (KeyError, TypeError, ValueError):
        return None

    contract_address = raw.get("contract") or raw.get("address")
    product_name = raw.get("name") or raw.get("productName")
    if not isinstance(contract_address, str) or not isinstance(product_name, str):
        return None

    token_address = HexAddress(contract_address.lower())
    raw_denomination_address = raw_chain.get("tokenAddr")
    denomination_address = HexAddress(raw_denomination_address.lower()) if isinstance(raw_denomination_address, str) else None

    return AssetoOffchainProduct(
        product_id=product_id,
        product_name=product_name,
        full_name=raw.get("fullName"),
        symbol=raw.get("symbol"),
        product_type=raw.get("type"),
        chain_id=chain_id,
        chain_name=raw_chain.get("name"),
        contract_address=token_address,
        denomination_symbol=raw_chain.get("tokenSymbol"),
        denomination_address=denomination_address,
        tvl=_as_decimal(raw.get("tvl")),
        apy=_as_decimal(raw.get("apy")),
        introduction=raw.get("introduction"),
        protocol=raw.get("protocol"),
    )


def fetch_asseto_products(
    api_base_url: str = ASSETO_API_BASE_URL,
    timeout: float = DEFAULT_ASSETO_API_TIMEOUT,
) -> Iterator[AssetoOffchainProduct]:
    """Fetch public EVM product metadata from Asseto's application registry.

    This is an undocumented application endpoint and its response schema may
    change without notice. It is suitable for optional discovery and
    descriptions, not for canonical TVL or NAV calculations.

    :param api_base_url:
        Asseto application API origin. Override in tests only.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        Iterator over parseable EVM product records.
    :raise AssetoAPIError:
        If the public endpoint returns an invalid response envelope.
    """

    response = requests.get(
        f"{api_base_url}/api/home/products",
        params={"tsOffset": 0},
        timeout=timeout,
    )
    payload = _require_success(response)
    product_groups = payload.get("data")
    if not isinstance(product_groups, list):
        message = "Asseto product registry data is not a list"
        raise AssetoAPIError(message)

    for group in product_groups:
        if not isinstance(group, dict):
            continue
        for raw_product in group.get("products") or []:
            if not isinstance(raw_product, dict):
                continue
            product = _parse_product(raw_product)
            if product is not None:
                yield product


def _fetch_asseto_product_data(
    product_name: str,
    api_base_url: str,
    timeout: float,
) -> dict:
    """Fetch the raw public detail object for one Asseto product.

    Both detailed product metadata and partner roles use the same
    undocumented application endpoint and product-name request header.

    :param product_name:
        Registry product key, for example ``"AoABT"``.
    :param api_base_url:
        Asseto application API origin.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        Raw API product object.
    :raise AssetoAPIError:
        If Asseto returns a malformed application response.
    """

    response = requests.get(
        f"{api_base_url}/api/product/get",
        headers={"productName": product_name},
        timeout=timeout,
    )
    payload = _require_success(response)
    raw_product = payload.get("data")
    if not isinstance(raw_product, dict):
        message = "Asseto API product detail data is not an object"
        raise AssetoAPIError(message)
    return raw_product


def fetch_asseto_product_detail(
    product_name: str,
    api_base_url: str = ASSETO_API_BASE_URL,
    timeout: float = DEFAULT_ASSETO_API_TIMEOUT,
) -> AssetoProductDetail | None:
    """Fetch the richer public description for an Asseto product.

    The application requires the registry product key in a ``productName``
    request header. Products without a parseable EVM address return ``None``.

    :param product_name:
        Registry product key, for example ``"AoABT"``.
    :param api_base_url:
        Asseto application API origin. Override in tests only.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        Public product detail, or ``None`` for a non-EVM/malformed product.
    :raise AssetoAPIError:
        If the public endpoint returns an invalid response envelope.
    """

    raw_product = _fetch_asseto_product_data(product_name, api_base_url, timeout)

    product = _parse_product(raw_product)
    if product is None:
        return None

    return AssetoProductDetail(
        product=product,
        description=raw_product.get("description"),
        price=_as_decimal(raw_product.get("price")),
        price_24h=_as_decimal(raw_product.get("price24h")),
        apy_calculation_method=raw_product.get("apyCalcMethod"),
    )


def fetch_asseto_product_roles(
    product_name: str,
    api_base_url: str = ASSETO_API_BASE_URL,
    timeout: float = DEFAULT_ASSETO_API_TIMEOUT,
) -> Iterator[AssetoRoleInfo]:
    """Fetch partner roles for a public Asseto product.

    The application API only supplies role labels and logo URLs. This helper
    resolves a textual organisation name from Asseto's known official logo
    assets, retaining unknown assets as ``None`` rather than inferring them.
    See https://asseto.finance/product for the source application.

    :param product_name:
        Registry product key, for example ``"AoABT"``.
    :param api_base_url:
        Asseto application API origin. Override in tests only.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        Iterator over well-formed public partner roles in API response order.
    :raise AssetoAPIError:
        If the public endpoint returns an invalid response envelope.
    """

    raw_product = _fetch_asseto_product_data(product_name, api_base_url, timeout)

    raw_partners = raw_product.get("partners")
    if not isinstance(raw_partners, list):
        return

    for raw_partner in raw_partners:
        if not isinstance(raw_partner, dict):
            continue
        role = raw_partner.get("name")
        logo_url = raw_partner.get("url")
        if not isinstance(role, str) or not role.strip() or not isinstance(logo_url, str) or not logo_url.strip():
            continue
        logo_url = logo_url.strip()
        yield AssetoRoleInfo(
            role=role.strip(),
            organisation_name=ASSETO_PARTNER_ORGANISATIONS_BY_LOGO_URL.get(logo_url),
            logo_url=logo_url,
        )


def fetch_asseto_price_history(
    product_id: int,
    days: int = 365,
    api_base_url: str = ASSETO_API_BASE_URL,
    timeout: float = DEFAULT_ASSETO_API_TIMEOUT,
) -> Iterator[AssetoPricePoint]:
    """Fetch Asseto's public display-price history for one product.

    This feed is informational and can be incomplete; callers must not use it
    in place of the on-chain NAV source used by :class:`AssetoVault`.

    :param product_id:
        Asseto registry product identifier.
    :param days:
        Requested lookback period. Asseto currently serves a one-year history
        for AoABT.
    :param api_base_url:
        Asseto application API origin. Override in tests only.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        Iterator over parseable price observations, sorted by API response order.
    :raise AssetoAPIError:
        If the public endpoint returns an invalid response envelope.
    """

    if product_id <= 0:
        message = "product_id must be positive"
        raise ValueError(message)
    if days <= 0:
        message = "days must be positive"
        raise ValueError(message)

    response = requests.get(
        f"{api_base_url}/api/product/price/list",
        params={"productId": product_id, "day": days},
        timeout=timeout,
    )
    payload = _require_success(response)
    raw_prices = payload.get("data")
    if not isinstance(raw_prices, list):
        message = "Asseto price history data is not a list"
        raise AssetoAPIError(message)

    for raw_price in raw_prices:
        if not isinstance(raw_price, dict):
            continue
        value = _as_decimal(raw_price.get("value"))
        try:
            timestamp = int(raw_price["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        if value is not None:
            yield AssetoPricePoint(timestamp=timestamp, value=value)
