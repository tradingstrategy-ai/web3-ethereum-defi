"""Merge authoritative Securitize off-chain NAV values into historical reads.

DSToken contracts provide the historical share supply but not a standard fund
NAV method. This module joins reviewed NAV feeds to those on-chain supply reads
before the generic scanner serialises its raw Parquet table. It deliberately
does not read or write Parquet files itself.
"""

import bisect
import datetime
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from decimal import Decimal

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.securitize.chronicle import CHRONICLE_SECURITIZE_FEEDS, ChroniclePricePoint, fetch_chronicle_price_history
from eth_defi.securitize.redstone import REDSTONE_SECURITIZE_FEEDS, RedstonePricePoint, fetch_redstone_price_history
from eth_defi.securitize.vault import SECURITIZE_NAV_UNAVAILABLE_ERROR_PREFIX
from eth_defi.vault.base import VaultBase, VaultHistoricalRead


@dataclass(slots=True, frozen=True)
class SecuritizeSharePricePoint:
    """One normalised external Securitize NAV observation."""

    #: Source publication time as naive UTC.
    timestamp: datetime.datetime

    #: Fund NAV/share in USD.
    share_price: Decimal


class SecuritizeSharePriceEnricher:
    """Apply pre-fetched Securitize NAVs to on-chain historical supply reads."""

    def __init__(self, price_points: dict[tuple[int, HexAddress], list[SecuritizeSharePricePoint]]):
        """Create a read transformer from normalised source observations.

        :param price_points:
            Source observations keyed by EVM chain and lower-case DSToken
            address. Each list may be unsorted.
        """

        self.price_points = {key: sorted(points, key=lambda point: point.timestamp) for key, points in price_points.items()}
        self._timestamps = {key: [point.timestamp for point in points] for key, points in self.price_points.items()}

    def __call__(self, read: VaultHistoricalRead) -> VaultHistoricalRead:
        """Fill a read's NAV and TVL with the latest published source value.

        A source observation is never applied before its publication timestamp.
        TVL is always recomputed from that NAV and the same historical token
        supply already read from the DSToken contract.

        :param read:
            Completed on-chain historical supply read.
        :return:
            Original read when no source value is applicable, otherwise an
            enriched copy.
        """

        key = read.vault.chain_id, HexAddress(read.vault.address.lower())
        points = self.price_points.get(key)
        if not points:
            return read
        point_index = bisect.bisect_right(self._timestamps[key], read.timestamp) - 1
        if point_index < 0:
            return read
        point = points[point_index]
        total_assets = read.total_supply * point.share_price if read.total_supply is not None else None
        errors = [error for error in (read.errors or []) if not error.startswith(SECURITIZE_NAV_UNAVAILABLE_ERROR_PREFIX)]
        return replace(
            read,
            share_price=point.share_price,
            total_assets=total_assets,
            errors=errors or None,
        )


def fetch_block_timestamp(web3: Web3, block_number: int) -> datetime.datetime:
    """Fetch one block timestamp as a naive UTC datetime.

    :param web3:
        Connected chain client.
    :param block_number:
        Historical block number already selected by the scanner.
    :return:
        Naive UTC block timestamp.
    """

    block = web3.eth.get_block(block_number)
    return datetime.datetime.fromtimestamp(block["timestamp"], tz=datetime.UTC).replace(tzinfo=None)


def _to_price_points(points: Iterable[RedstonePricePoint | ChroniclePricePoint]) -> list[SecuritizeSharePricePoint]:
    """Convert an external NAV history to the common Securitize format.

    :param points:
        RedStone or Chronicle observations.
    :return:
        Normalised observations.
    """

    return [SecuritizeSharePricePoint(point.timestamp, point.share_price) for point in points]


def fetch_securitize_share_price_enricher(
    vaults: Iterable[VaultBase],
    web3: Web3,
    start_block: int,
    end_block: int,
) -> SecuritizeSharePriceEnricher | None:
    """Fetch source observations for the resolved scanner range.

    RedStone is the default public source for all currently priced Securitize
    products. Chronicle can optionally provide a signed STAC history through
    ``CHRONICLE_STAC_HISTORY_URL``; when set, it is preferred for STAC because
    it contains the fund's Proof of Asset verification record.

    :param vaults:
        Vaults selected for one chain scan.
    :param web3:
        Connected chain client used only to resolve the scanner's block range.
    :param start_block:
        Inclusive resolved historical scan block.
    :param end_block:
        Inclusive resolved historical scan block.
    :return:
        A pure historical-read transformer, or ``None`` when no selected vault
        has a configured external price source.
    """

    vaults = list(vaults)
    vault_keys = {(vault.chain_id, HexAddress(vault.address.lower())) for vault in vaults}
    start_at = fetch_block_timestamp(web3, start_block)
    end_at = fetch_block_timestamp(web3, end_block)
    price_points: dict[tuple[int, HexAddress], list[SecuritizeSharePricePoint]] = {}
    chronicle_history_url = os.environ.get("CHRONICLE_STAC_HISTORY_URL")
    chronicle_keys = vault_keys.intersection(CHRONICLE_SECURITIZE_FEEDS) if chronicle_history_url else set()

    for vault in vaults:
        key = vault.chain_id, HexAddress(vault.address.lower())
        if key in chronicle_keys:
            continue
        feed = REDSTONE_SECURITIZE_FEEDS.get(key)
        if feed is not None:
            price_points[key] = _to_price_points(fetch_redstone_price_history(feed, start_at, end_at))

    if chronicle_history_url:
        for key in chronicle_keys:
            feed = CHRONICLE_SECURITIZE_FEEDS[key]
            chronicle_history = list(fetch_chronicle_price_history(feed, chronicle_history_url))
            chronicle_points = [point for point in chronicle_history if start_at <= point.timestamp <= end_at]
            previous_points = [point for point in chronicle_history if point.timestamp < start_at]
            if previous_points:
                chronicle_points.insert(0, previous_points[-1])
            if chronicle_points:
                price_points[key] = _to_price_points(chronicle_points)

    price_points = {key: points for key, points in price_points.items() if points}
    if not price_points:
        return None
    return SecuritizeSharePriceEnricher(price_points)


def create_securitize_share_price_transformer_factory(
    vaults: Iterable[VaultBase],
    web3: Web3,
) -> Callable[[int, int], SecuritizeSharePriceEnricher | None]:
    """Create the scanner callback that lazily fetches incremental NAV history.

    The factory deliberately propagates provider failures. The scanner resolves
    this callback before it starts its Parquet rewrite; failing the scan keeps
    previously enriched NAV and TVL history intact instead of replacing it with
    unpriced on-chain supply rows.

    :param vaults:
        Vaults selected for one chain scan.
    :param web3:
        Connected chain client.
    :return:
        Factory compatible with ``scan_historical_prices_to_parquet``.
    """

    vaults = [vault for vault in vaults if (vault.chain_id, HexAddress(vault.address.lower())) in REDSTONE_SECURITIZE_FEEDS or (vault.chain_id, HexAddress(vault.address.lower())) in CHRONICLE_SECURITIZE_FEEDS]
    if not vaults:
        return lambda _start_block, _end_block: None

    return lambda start_block, end_block: fetch_securitize_share_price_enricher(vaults, web3, start_block, end_block)
