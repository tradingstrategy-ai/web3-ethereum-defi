"""Test Securitize external NAV source adapters."""

import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from eth_defi.securitize import chronicle, redstone
from eth_defi.securitize.description import ACRED_ETHEREUM, BCAP_ETHEREUM, HLSCOPE_ETHEREUM, MI4_MANTLE, STAC_ETHEREUM, VBILL_ETHEREUM


def utc_datetime(year: int, month: int, day: int, hour: int = 0) -> datetime.datetime:
    """Create a naive UTC datetime accepted by the vault schema.

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
    """Minimal successful HTTP response for Chronicle client tests."""

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


def test_fetch_redstone_price_at_reads_archive_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read and scale a Chainlink-compatible observation at one archive block."""

    calls: list[int] = []

    class LatestRoundDataCall:
        """Capture the requested archive block."""

        @staticmethod
        def call(*, block_identifier: int) -> tuple[int, int, int, int, int]:
            """Return one valid RedStone observation.

            :param block_identifier:
                Requested archive block.
            :return:
                Chainlink ``latestRoundData()`` tuple.
            """

            calls.append(block_identifier)
            return 1, 100_125_000_000, 1_735_689_600, 1_735_689_600, 1

    contract = SimpleNamespace(functions=SimpleNamespace(latestRoundData=LatestRoundDataCall))
    monkeypatch.setattr(redstone, "fetch_redstone_feed_contract", lambda *_args: contract)
    feed = redstone.REDSTONE_SECURITIZE_FEEDS[ACRED_ETHEREUM.chain_id, ACRED_ETHEREUM.token]

    point = redstone.fetch_redstone_price_at(SimpleNamespace(), feed, 22_000_000)

    assert point == redstone.RedstonePricePoint(utc_datetime(2025, 1, 1), Decimal("1001.25"))
    assert calls == [22_000_000]


def test_fetch_redstone_price_at_rejects_block_before_first_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail clearly without calling a feed before its first valid value."""

    feed = redstone.REDSTONE_SECURITIZE_FEEDS[ACRED_ETHEREUM.chain_id, ACRED_ETHEREUM.token]
    monkeypatch.setattr(redstone, "fetch_redstone_feed_contract", lambda *_args: pytest.fail("feed contract must not be called"))

    with pytest.raises(redstone.RedstoneFeedError, match=str(feed.first_block)):
        redstone.fetch_redstone_price_at(SimpleNamespace(), feed, feed.first_block - 1)


def test_fetch_chronicle_price_history_parses_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse Chronicle signed NAV records without coupling to Parquet storage."""

    def fake_get(_url: str, **_kwargs: object) -> ResponseStub:
        """Return a representative dashboard-history export.

        :return:
            Stubbed response.
        """

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


def test_reviewed_redstone_products_have_matching_source_configuration() -> None:
    """Keep product metadata and executable push-feed registry aligned."""

    products = (ACRED_ETHEREUM, HLSCOPE_ETHEREUM, STAC_ETHEREUM, VBILL_ETHEREUM, BCAP_ETHEREUM, MI4_MANTLE)

    assert all(product.nav_source.startswith("redstone_") for product in products)
    assert {(product.chain_id, product.token) for product in products} == set(redstone.REDSTONE_SECURITIZE_FEEDS)
    assert all(feed.oracle_address == feed.oracle_address.lower() for feed in redstone.REDSTONE_SECURITIZE_FEEDS.values())
