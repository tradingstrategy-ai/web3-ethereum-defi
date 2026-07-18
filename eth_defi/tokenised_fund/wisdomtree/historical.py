"""Historical reader for WisdomTree tokenised funds."""

# ruff: noqa: ARG002, FBT001

import datetime
from collections.abc import Iterable
from decimal import Decimal
from functools import cached_property
from typing import TYPE_CHECKING

from eth_defi.erc_4626.vault import VaultReaderState
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

if TYPE_CHECKING:
    from eth_defi.tokenised_fund.wisdomtree.vault import WisdomTreeVault


class WisdomTreeVaultReaderState(VaultReaderState):
    """Persist WisdomTree history state using issuer USD NAV accounting."""

    @cached_property
    def exchange_rate(self) -> Decimal:
        """Return the quote conversion for USD NAV values.

        :return: One because DataSpan reports USD NAV per share.
        """

        return Decimal(1)


class WisdomTreeVaultHistoricalReader(VaultHistoricalReader):
    """Read on-chain token supply and official off-chain NAV history."""

    def __init__(self, vault: "WisdomTreeVault", stateful: bool):
        """Create a read-only historical reader.

        :param vault: WisdomTree tokenised-fund adapter.
        :param stateful: Whether to persist adaptive scan state.
        """

        super().__init__(vault)
        self.reader_state = WisdomTreeVaultReaderState(vault) if stateful else None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Construct the ERC-20 supply read.

        :return: One ``totalSupply`` multicall.
        """

        yield EncodedCall.from_contract_call(
            self.vault.share_token.contract.functions.totalSupply(),
            extra_data={"function": "totalSupply", "vault": self.vault.address},
            first_block_number=self.first_block,
        )

    def process_result(self, block_number: int, timestamp: datetime.datetime, call_results: list[EncodedCallResult]) -> VaultHistoricalRead:
        """Create a historical row, failing only this row if NAV is unavailable.

        :param block_number: Archive block number.
        :param timestamp: Naive UTC block timestamp.
        :param call_results: Supply read results.
        :return: Supply, official NAV and calculated total assets where available.
        """

        total_supply: Decimal | None = None
        supply_result: EncodedCallResult | None = None
        errors: list[str] = []
        for result in call_results:
            if result.call.extra_data.get("function") == "totalSupply":
                if result.success:
                    total_supply = self.vault.share_token.convert_to_decimals(convert_int256_bytes_to_int(result.result))
                    supply_result = result
                else:
                    errors.append("WisdomTree totalSupply call failed")
        try:
            share_price = self.vault.fetch_share_price_at(timestamp)
        except RuntimeError as error:
            share_price = None
            errors.append(str(error))
        total_assets = share_price * total_supply if share_price is not None and total_supply is not None else None
        if self.reader_state is not None and supply_result is not None and total_assets is not None:
            self.reader_state.on_called(supply_result, total_assets=total_assets, share_price=share_price)
        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            performance_fee=self.vault.get_performance_fee(block_number),
            management_fee=self.vault.get_management_fee(block_number),
            errors=errors or None,
            deposits_open=False,
            redemption_open=False,
        )
