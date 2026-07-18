"""Historical reader for Ondo tokenised fund products."""

# Reader classes intentionally mirror :class:`VaultHistoricalReader` signatures.
# ruff: noqa: FBT001

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
    from eth_defi.tokenised_fund.ondo.vault import OndoVault


class OndoVaultReaderState(VaultReaderState):
    """Persist Ondo reader state for already USD-denominated NAV."""

    @cached_property
    def exchange_rate(self) -> Decimal:
        """Return the USD exchange rate used by Ondo NAV results."""

        return Decimal(1)


class OndoVaultHistoricalReader(VaultHistoricalReader):
    """Read ERC-20 supply and issuer-published Ondo NAV/share."""

    def __init__(self, vault: "OndoVault", stateful: bool):
        """Create a historical reader.

        :param vault: Ondo tokenised fund adapter.
        :param stateful: Whether to retain adaptive reader state.
        """

        super().__init__(vault)
        self.reader_state = OndoVaultReaderState(vault) if stateful else None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Construct total-supply and issuer NAV calls."""

        yield EncodedCall.from_contract_call(
            self.vault.share_token.contract.functions.totalSupply(),
            extra_data={"function": "totalSupply", "vault": self.vault.address},
            first_block_number=self.first_block,
        )
        yield EncodedCall.from_contract_call(
            self.vault.fetch_oracle_call(),
            extra_data={"function": self.vault.product.oracle_method, "vault": self.vault.address},
            first_block_number=max(self.first_block or self.vault.product.oracle_first_seen_at_block, self.vault.product.oracle_first_seen_at_block),
        )

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        """Convert issuer oracle results to a USD historical vault row."""

        total_supply: Decimal | None = None
        share_price: Decimal | None = None
        state_result: EncodedCallResult | None = None
        errors: list[str] = []
        for result in call_results:
            function = result.call.extra_data.get("function")
            if not result.success:
                errors.append(f"Ondo {function} call failed")
                continue
            if function == "totalSupply":
                total_supply = self.vault.share_token.convert_to_decimals(convert_int256_bytes_to_int(result.result))
            elif function == self.vault.product.oracle_method:
                share_price = Decimal(convert_int256_bytes_to_int(result.result)) / Decimal(10**18)
                state_result = result

        total_assets = share_price * total_supply if share_price is not None and total_supply is not None else None
        if self.reader_state is not None and state_result is not None and total_assets is not None:
            self.reader_state.on_called(state_result, total_assets=total_assets, share_price=share_price)

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
