"""Test Asseto public off-chain product metadata client."""

from decimal import Decimal

import pytest

from eth_defi.tokenised_fund.asseto import offchain_api
from eth_defi.tokenised_fund.asseto.constants import HASHKEY_CHAIN_ID


class ResponseStub:
    """Minimal :mod:`requests` response stub for Asseto API tests."""

    def __init__(self, payload: dict):
        """Create a successful JSON response.

        :param payload:
            JSON response body.
        """

        self.payload = payload

    def raise_for_status(self) -> None:
        """Accept the test response as successful."""

    def json(self) -> dict:
        """Return the configured response envelope.

        :return:
            Asseto-style JSON envelope.
        """

        return self.payload


def test_fetch_asseto_products_parses_evm_products(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse public registry rows and ignore non-EVM products."""

    calls: list[dict] = []

    def fake_get(url: str, **kwargs) -> ResponseStub:
        """Return a representative Asseto product registry."""

        calls.append({"url": url, **kwargs})
        return ResponseStub(
            {
                "code": 10_000,
                "message": "success",
                "data": [
                    {
                        "products": [
                            {
                                "id": 2,
                                "name": "AoABT",
                                "fullName": "Orient Arbitrage Strategy",
                                "symbol": "AoABT",
                                "type": "uda",
                                "contract": "0x80C080acd48ED66a35Ae8A24BC1198672215A9bD",
                                "tvl": "1871900.2415",
                                "apy": "20.66",
                                "introduction": "Asseto product introduction",
                                "protocol": "Asseto protocol disclosure",
                                "supportChains": {
                                    "name": "HashKey Chain",
                                    "chainId": "177",
                                    "tokenSymbol": "USDT",
                                    "tokenAddr": "0xF1B50eD67A9e2CC94Ad3c477779E2d4cBfFf9029",
                                },
                            },
                            {
                                "id": 13,
                                "name": "AdDLA",
                                "supportChains": {"chainId": "xrpl_mainnet"},
                                "contract": "rh5BAuY7s4FAgYCTQNpM2mtm3MCQkSW7yt",
                            },
                        ]
                    }
                ],
            }
        )

    monkeypatch.setattr(offchain_api.requests, "get", fake_get)

    products = list(offchain_api.fetch_asseto_products(api_base_url="https://api.example"))

    assert calls == [
        {
            "url": "https://api.example/api/home/products",
            "params": {"tsOffset": 0},
            "timeout": offchain_api.DEFAULT_ASSETO_API_TIMEOUT,
        }
    ]
    assert len(products) == 1
    assert products[0].product_name == "AoABT"
    assert products[0].chain_id == HASHKEY_CHAIN_ID
    assert products[0].contract_address == "0x80c080acd48ed66a35ae8a24bc1198672215a9bd"
    assert products[0].denomination_address == "0xf1b50ed67a9e2cc94ad3c477779e2d4cbfff9029"
    assert products[0].tvl == Decimal("1871900.2415")
    assert products[0].apy == Decimal("20.66")


def test_fetch_asseto_product_detail_uses_product_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the product name header required by the public detail endpoint."""

    calls: list[dict] = []

    def fake_get(url: str, **kwargs) -> ResponseStub:
        """Return a representative public detail response."""

        calls.append({"url": url, **kwargs})
        return ResponseStub(
            {
                "code": 10_000,
                "message": "success",
                "data": {
                    "id": 2,
                    "productName": "AoABT",
                    "fullName": "Orient Arbitrage Strategy",
                    "symbol": "AoABT",
                    "type": "uda",
                    "address": "0x80C080acd48ED66a35Ae8A24BC1198672215A9bD",
                    "price": "992.2594",
                    "price24h": "17.1074",
                    "apy": "20.66",
                    "apyCalcMethod": "calcMonthlyAPY",
                    "description": "Long product description",
                    "supportChains": {
                        "name": "HashKey Chain",
                        "chainId": "177",
                        "tokenSymbol": "USDT",
                        "tokenAddr": "0xF1B50eD67A9e2CC94Ad3c477779E2d4cBfFf9029",
                    },
                },
            }
        )

    monkeypatch.setattr(offchain_api.requests, "get", fake_get)

    detail = offchain_api.fetch_asseto_product_detail("AoABT", api_base_url="https://api.example")

    assert calls == [
        {
            "url": "https://api.example/api/product/get",
            "headers": {"productName": "AoABT"},
            "timeout": offchain_api.DEFAULT_ASSETO_API_TIMEOUT,
        }
    ]
    assert detail is not None
    assert detail.product.product_name == "AoABT"
    assert detail.description == "Long product description"
    assert detail.price == Decimal("992.2594")
    assert detail.price_24h == Decimal("17.1074")
    assert detail.apy_calculation_method == "calcMonthlyAPY"


def test_fetch_asseto_product_roles_resolves_known_partner_logos(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve public Asseto partner roles without guessing unknown logos."""

    def fake_get(_url: str, **_kwargs) -> ResponseStub:
        """Return known and unknown partner logo assets."""

        return ResponseStub(
            {
                "code": 10_000,
                "message": "success",
                "data": {
                    "partners": [
                        {
                            "name": "Investment Manager",
                            "url": "https://static.asseto.finance/asseto/2026-03-19/dh6vej2bje9qdnvcrx.svg",
                        },
                        {
                            "name": "Auditor",
                            "url": "https://static.asseto.finance/asseto/unknown.svg",
                        },
                        {"name": "Legal"},
                    ]
                },
            }
        )

    monkeypatch.setattr(offchain_api.requests, "get", fake_get)

    roles = list(offchain_api.fetch_asseto_product_roles("AdDLT", api_base_url="https://api.example"))

    assert roles == [
        offchain_api.AssetoRoleInfo(
            role="Investment Manager",
            organisation_name="DL Holdings",
            logo_url="https://static.asseto.finance/asseto/2026-03-19/dh6vej2bje9qdnvcrx.svg",
        ),
        offchain_api.AssetoRoleInfo(
            role="Auditor",
            organisation_name=None,
            logo_url="https://static.asseto.finance/asseto/unknown.svg",
        ),
    ]


def test_fetch_asseto_price_history_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse valid price rows and reject invalid API application responses."""

    def fake_get(_url: str, **_kwargs) -> ResponseStub:
        """Return valid history with one malformed row."""

        return ResponseStub(
            {
                "code": 10_000,
                "message": "success",
                "data": [
                    {"timestamp": 1_753_891_200, "value": "1015.4502"},
                    {"timestamp": "invalid", "value": "1016"},
                ],
            }
        )

    monkeypatch.setattr(offchain_api.requests, "get", fake_get)

    history = list(offchain_api.fetch_asseto_price_history(2, days=365, api_base_url="https://api.example"))

    assert history == [offchain_api.AssetoPricePoint(timestamp=1_753_891_200, value=Decimal("1015.4502"))]

    with pytest.raises(ValueError, match="days must be positive"):
        list(offchain_api.fetch_asseto_price_history(2, days=0))

    def fake_error_get(_url: str, **_kwargs) -> ResponseStub:
        """Return an Asseto application-level error envelope."""

        return ResponseStub({"code": 10_001, "message": "product unavailable"})

    monkeypatch.setattr(offchain_api.requests, "get", fake_error_get)

    with pytest.raises(offchain_api.AssetoAPIError, match="product unavailable"):
        list(offchain_api.fetch_asseto_products())
