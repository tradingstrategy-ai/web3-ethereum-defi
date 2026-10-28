"""Adapt sparse Antarctic subscription events to the common price writer."""

import datetime
from collections.abc import Iterator

from eth_defi.erc_4626.vault_protocol.antarctic.historical_context import AntarcticHistoricalContextStore
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.types import Percent
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader


class AntarcticHistoricalReader(VaultHistoricalReader):
    """Read handler-settled subscription prices from prefetched context.

    Amount ratios are exact Decimal values until common Parquet export. Staking
    income and redemption execution ratios are deliberately excluded.
    """

    @property
    def uses_contextual_history(self) -> bool:
        return True

    @property
    def share_price_change_threshold(self) -> Percent:
        return 0

    def construct_multicalls(self) -> Iterator[EncodedCall]:  # noqa: PLR6301
        return iter(())

    def process_result(self, block_number: int, timestamp: datetime.datetime, call_results: list[EncodedCallResult]) -> VaultHistoricalRead:  # noqa: ARG002, PLR6301
        msg = "Antarctic prices require prefetched settlement context"
        raise RuntimeError(msg)

    def fetch_contextual_historical_reads(self, start_block: int, end_block: int, step: int) -> Iterator[VaultHistoricalRead]:  # noqa: ARG002
        """Yield one actual subscription observation per vault/block.

        A zero bootstrap TVL is unknown historical assets, not a measured empty
        vault. Supply is not paired with event TVL because no historical supply
        observation accompanies the valuation.

        :param start_block: Inclusive source boundary.
        :param end_block: Exclusive source boundary.
        :param step: Ignored; no hourly observations are manufactured.
        :return: Common historical rows at actual event timestamps.
        """
        denomination = self.vault.denomination_token
        shares = self.vault.share_token
        assert denomination is not None
        with AntarcticHistoricalContextStore(self.vault.historical_context_path) as store:
            for event in store.iter_settlements(self.vault.address, start_block, end_block, canonical_only=True):
                yield VaultHistoricalRead(vault=self.vault, block_number=event.block_number, timestamp=datetime.datetime.fromtimestamp(event.block_timestamp, datetime.UTC).replace(tzinfo=None), share_price=denomination.convert_to_decimals(event.raw_usdt) / shares.convert_to_decimals(event.raw_shares), total_assets=denomination.convert_to_decimals(event.raw_tvl) if event.raw_tvl else None, total_supply=None, performance_fee=None, management_fee=None, errors=None, deposits_open=None, redemption_open=None)
