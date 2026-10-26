# Shared reader signatures include unused static-read compatibility parameters.
# ruff: noqa: ARG002, PLR6301
"""Adapt source-backed Infinit history to the shared price writer."""

import datetime
from collections.abc import Iterator

from eth_defi.erc_4626.vault_protocol.infinit.constants import INFINIT_MANAGEMENT_FEE, INFINIT_PERFORMANCE_FEE, is_quarantined
from eth_defi.erc_4626.vault_protocol.infinit.historical_context import InfinitHistoricalContextStore
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.types import Percent
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader


class InfinitHistoricalReader(VaultHistoricalReader):
    """Read exact hourly source observations with event-matched receipt supply."""

    @property
    def uses_contextual_history(self) -> bool:
        return True

    @property
    def share_price_change_threshold(self) -> Percent:
        return 0

    @property
    def write_all_samples(self) -> bool:
        return True

    def construct_multicalls(self) -> Iterator[EncodedCall]:
        return iter(())

    def process_result(self, block_number: int, timestamp: datetime.datetime, call_results: list[EncodedCallResult]) -> VaultHistoricalRead:
        msg = "Infinit history requires prefetched public API and supply context"
        raise RuntimeError(msg)

    def fetch_contextual_historical_reads(self, start_block: int, end_block: int, step: int) -> Iterator[VaultHistoricalRead]:
        """Yield actual hourly observations, including explicit missing rows.

        :param start_block: Inclusive source block.
        :param end_block: Exclusive source block.
        :param step: Compatibility parameter; no prices are interpolated.
        :return: Shared price records with null valuation inside reviewed gaps.
        """
        with InfinitHistoricalContextStore(self.vault.historical_context_path, read_only=True) as store:
            for timestamp, source_price, block, raw_supply in store.iter_prices(self.vault.product, start_block, end_block):
                supply = self.vault.share_token.convert_to_decimals(raw_supply)
                gap = is_quarantined(timestamp)
                price = None if gap else source_price
                yield VaultHistoricalRead(
                    vault=self.vault,
                    block_number=block,
                    timestamp=timestamp,
                    share_price=price,
                    total_assets=price * supply if price is not None else None,
                    total_supply=supply,
                    performance_fee=INFINIT_PERFORMANCE_FEE,
                    management_fee=INFINIT_MANAGEMENT_FEE,
                    errors=["Infinit reviewed source-price gap"] if gap else None,
                    deposits_open=None,
                    redemption_open=None,
                )
