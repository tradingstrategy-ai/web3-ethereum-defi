"""Test Securitize off-chain NAV parsing and in-memory historical enrichment."""

import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from eth_defi.securitize import chronicle, redstone, share_price
from eth_defi.securitize.description import ACRED_ETHEREUM, HLSCOPE_ETHEREUM, STAC_ETHEREUM, VBILL_ETHEREUM
from eth_defi.securitize.share_price import SecuritizeSharePriceEnricher, SecuritizeSharePricePoint
from eth_defi.securitize.vault import SECURITIZE_NAV_UNAVAILABLE_ERROR_PREFIX
from eth_defi.vault.base import VaultHistoricalRead


def utc_datetime(year: int, month: int, day: int, hour: int = 0) -> datetime.datetime:
    """Create a naive UTC datetime accepted by the vault historical schema.

    :param year:
        Calendar year.
    :param month:
        Calendar month.
    :param day:
        Calendar day.
    :param hour:
        UTC hour.
    :return:
        Naive UTC datetime.
    """

    return datetime.datetime(year, month, day, hour, tzinfo=datetime.UTC).replace(tzinfo=None)


class ResponseStub:
    """Minimal successful HTTP response for public source-client tests."""

    def __init__(self, payload: object):
        """Create a response with a JSON payload.

        :param payload:
            Value returned from :meth:`json`.
        """

        self.payload = payload

    def raise_for_status(self) -> None:
        """Accept the response as successful."""

    def json(self) -> object:
        """Return the configured JSON payload.

        :return:
            Configured JSON object.
        """

        return self.payload


def test_fetch_redstone_price_at_uses_point_in_time_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read the latest RedStone observation at an explicit UTC checkpoint."""

    calls: list[dict] = []

    def fake_get(url: str, **kwargs: object) -> ResponseStub:
        """Capture the request and return one signed feed observation."""

        calls.append({"url": url, **kwargs})
        return ResponseStub([{"timestamp": 1_735_689_600_000, "value": "1001.25"}])

    monkeypatch.setattr(redstone.requests, "get", fake_get)
    feed = redstone.REDSTONE_SECURITIZE_FEEDS[ACRED_ETHEREUM.chain_id, ACRED_ETHEREUM.token]
    at = utc_datetime(2025, 1, 2)

    point = redstone.fetch_redstone_price_at(feed, at, api_url="https://api.example")

    assert point == redstone.RedstonePricePoint(utc_datetime(2025, 1, 1), Decimal("1001.25"))
    assert calls == [
        {
            "url": "https://api.example",
            "params": {
                "symbol": "ACRED_FUNDAMENTAL",
                "provider": "redstone",
                "limit": 1,
                "toTimestamp": 1_735_776_000_000,
            },
            "timeout": redstone.DEFAULT_REDSTONE_API_TIMEOUT,
        }
    ]


def test_fetch_redstone_price_history_uses_daily_checkpoints_and_deduplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Query the anchor, daily checkpoints and end boundary once each."""

    checkpoints: list[datetime.datetime] = []
    feed = redstone.REDSTONE_SECURITIZE_FEEDS[ACRED_ETHEREUM.chain_id, ACRED_ETHEREUM.token]
    first_point = redstone.RedstonePricePoint(utc_datetime(2025, 1, 1, 10), Decimal("1000"))
    final_point = redstone.RedstonePricePoint(utc_datetime(2025, 1, 3, 9), Decimal("1001"))

    def fake_fetch(_feed: redstone.RedstoneSecuritizeFeed, at: datetime.datetime, **_kwargs: object) -> redstone.RedstonePricePoint:
        """Return duplicate publications around two source changes."""

        checkpoints.append(at)
        return first_point if at < utc_datetime(2025, 1, 3) else final_point

    monkeypatch.setattr(redstone, "fetch_redstone_price_at", fake_fetch)

    points = list(redstone.fetch_redstone_price_history(feed, utc_datetime(2025, 1, 1, 12), utc_datetime(2025, 1, 3, 18)))

    assert points == [first_point, final_point]
    assert checkpoints == [
        utc_datetime(2025, 1, 1, 12),
        utc_datetime(2025, 1, 2),
        utc_datetime(2025, 1, 3),
        utc_datetime(2025, 1, 3, 18),
    ]


def test_fetch_chronicle_price_history_parses_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse Chronicle signed NAV records without coupling to Parquet storage."""

    def fake_get(_url: str, **_kwargs: object) -> ResponseStub:
        """Return a representative dashboard-history export."""

        return ResponseStub(
            {
                "data": [
                    {"timestamp": 1_735_689_600, "nav": "1002.50", "totalValueLocked": "10025000"},
                    {"timestamp": 1_735_776_000, "nav": "1003.00"},
                ]
            }
        )

    monkeypatch.setattr(chronicle.requests, "get", fake_get)
    feed = chronicle.CHRONICLE_SECURITIZE_FEEDS[1, "0x51c2d74017390cbbd30550179a16a1c28f7210fc"]

    points = list(chronicle.fetch_chronicle_price_history(feed, "https://chronicle.example/history"))

    assert points == [
        chronicle.ChroniclePricePoint(utc_datetime(2025, 1, 1), Decimal("1002.50"), Decimal("10025000")),
        chronicle.ChroniclePricePoint(utc_datetime(2025, 1, 2), Decimal("1003.00"), None),
    ]


def test_securitize_share_price_enricher_updates_tvl_without_parquet_io() -> None:
    """Join source NAV to the same historical total supply in memory."""

    vault = SimpleNamespace(chain_id=1, address=ACRED_ETHEREUM.token)
    read = VaultHistoricalRead(
        vault=vault,
        block_number=123,
        timestamp=utc_datetime(2025, 1, 2, 12),
        share_price=None,
        total_assets=None,
        total_supply=Decimal("200"),
        performance_fee=None,
        management_fee=None,
        errors=["No on-chain NAV source configured for Securitize DSToken 0x17418038ecf73ba4026c4f428547bf099706f27b"],
    )
    enricher = SecuritizeSharePriceEnricher(
        {
            (1, ACRED_ETHEREUM.token): [
                SecuritizeSharePricePoint(utc_datetime(2025, 1, 2), Decimal("1001.50")),
            ]
        }
    )

    enriched = enricher(read)

    assert enriched is not read
    assert enriched.share_price == Decimal("1001.50")
    assert enriched.total_assets == Decimal("200300.00")
    assert enriched.total_supply == Decimal("200")
    assert enriched.errors is None


def test_securitize_share_price_enricher_keeps_unavailable_nav_before_publication() -> None:
    """Do not apply an NAV published after the historical supply read."""

    vault = SimpleNamespace(chain_id=1, address=ACRED_ETHEREUM.token)
    unavailable_nav_error = f"{SECURITIZE_NAV_UNAVAILABLE_ERROR_PREFIX} {ACRED_ETHEREUM.token}"
    read = VaultHistoricalRead(
        vault=vault,
        block_number=123,
        timestamp=utc_datetime(2025, 1, 1),
        share_price=None,
        total_assets=None,
        total_supply=Decimal("200"),
        performance_fee=None,
        management_fee=None,
        errors=[unavailable_nav_error],
    )
    enricher = SecuritizeSharePriceEnricher({(1, ACRED_ETHEREUM.token): [SecuritizeSharePricePoint(utc_datetime(2025, 1, 2), Decimal("1001.50"))]})

    assert enricher(read) is read


def test_configured_chronicle_source_can_enrich_without_redstone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Support a Chronicle-only product and prefer it when it is configured."""

    vault = SimpleNamespace(chain_id=STAC_ETHEREUM.chain_id, address=STAC_ETHEREUM.token)
    web3 = SimpleNamespace(eth=SimpleNamespace(get_block=lambda _block: {"timestamp": 1_735_776_000}))
    chronicle_point = chronicle.ChroniclePricePoint(utc_datetime(2025, 1, 2), Decimal("1002.50"), None)
    monkeypatch.setenv("CHRONICLE_STAC_HISTORY_URL", "https://chronicle.example/history")
    monkeypatch.setattr(share_price, "REDSTONE_SECURITIZE_FEEDS", {})
    monkeypatch.setattr(share_price, "fetch_chronicle_price_history", lambda *_args: iter([chronicle_point]))

    enricher = share_price.fetch_securitize_share_price_enricher([vault], web3, 1, 2)

    assert enricher is not None
    assert enricher.price_points[STAC_ETHEREUM.chain_id, STAC_ETHEREUM.token] == [SecuritizeSharePricePoint(utc_datetime(2025, 1, 2), Decimal("1002.50"))]


def test_reviewed_redstone_products_have_matching_source_configuration() -> None:
    """Keep product notes, metadata and the executable feed registry aligned."""

    products = (ACRED_ETHEREUM, HLSCOPE_ETHEREUM, STAC_ETHEREUM, VBILL_ETHEREUM)

    assert {product.nav_source for product in products} == {
        "redstone_acred_fundamental",
        "redstone_hlscope_fundamental",
        "redstone_stac_fundamental",
        "redstone_vbill_ethereum_fundamental",
    }
    assert {(product.chain_id, product.token) for product in products} == set(redstone.REDSTONE_SECURITIZE_FEEDS)


def test_price_transformer_factory_preserves_parquet_when_provider_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Propagate provider failures before the historical scanner can rewrite data."""

    vault = SimpleNamespace(chain_id=ACRED_ETHEREUM.chain_id, address=ACRED_ETHEREUM.token)
    factory = share_price.create_securitize_share_price_transformer_factory([vault], SimpleNamespace())

    def raise_provider_error(*_args: object, **_kwargs: object) -> None:
        """Simulate an unavailable external NAV provider."""

        message = "provider unavailable"
        raise redstone.RedstoneAPIError(message)

    monkeypatch.setattr(share_price, "fetch_securitize_share_price_enricher", raise_provider_error)

    with pytest.raises(redstone.RedstoneAPIError, match="provider unavailable"):
        factory(1, 2)
