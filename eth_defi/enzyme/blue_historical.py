"""Historical share-price and TVL reader for Enzyme Blue funds."""

# ruff: noqa: FBT001

import datetime
from collections.abc import Iterable
from decimal import Decimal
from typing import TYPE_CHECKING

from eth_defi.erc_4626.vault import VaultReaderState
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

if TYPE_CHECKING:
    from eth_defi.enzyme.blue_vault import EnzymeBlueVault


class EnzymeBlueVaultHistoricalReader(VaultHistoricalReader):
    """Read Blue GAV, share supply, and derived denomination-token price.

    Blue's ``ComptrollerProxy.calcGav()`` returns the gross asset value in the
    denomination-token's raw units.  Combining this with VaultProxy supply in
    one Multicall request gives TVL and the gross share value without requiring
    an ERC-4626 interface.  Historical fee configurations are deliberately
    TODO: Blue fees may be changed during a release migration.
    """

    def __init__(self, vault: "EnzymeBlueVault", stateful: bool):
        """Create a Blue historical reader with optional adaptive state."""

        super().__init__(vault)
        self.reader_state = VaultReaderState(vault) if stateful else None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Construct GAV and VaultProxy supply calls for the shared batch."""

        yield EncodedCall.from_contract_call(
            self.vault.comptroller_contract.functions.calcGav(),
            extra_data={"function": "gav", "vault": self.vault.address},
            first_block_number=self.first_block,
        )
        yield EncodedCall.from_contract_call(
            self.vault.vault_contract.functions.totalSupply(),
            extra_data={"function": "total_supply", "vault": self.vault.address},
            first_block_number=self.first_block,
        )

    def process_result(self, block_number: int, timestamp: datetime.datetime, call_results: list[EncodedCallResult]) -> VaultHistoricalRead:
        """Decode one sampled Blue GAV/supply response into a vault row.

        :param block_number: Sampled chain block.
        :param timestamp: Naive UTC time of ``block_number``.
        :param call_results: The two grouped Multicall responses.
        :return: Derived share price, TVL, supply, and individual call errors.
        """

        raw_gav = raw_total_supply = None
        successful_result = None
        errors = []
        for result in call_results:
            function = result.call.extra_data["function"]
            if not result.success:
                errors.append(f"{function} call failed")
                continue
            successful_result = successful_result or result
            if function == "gav":
                raw_gav = convert_int256_bytes_to_int(result.result)
            elif function == "total_supply":
                raw_total_supply = convert_int256_bytes_to_int(result.result)

        total_assets = share_price = total_supply = None
        if raw_gav is not None:
            total_assets = Decimal(raw_gav) / Decimal(10**self.vault.denomination_token.decimals)
        if raw_total_supply is not None:
            total_supply = Decimal(raw_total_supply) / Decimal(10**self.vault.share_token.decimals)
        if total_assets is not None and total_supply not in {None, Decimal(0)}:
            share_price = total_assets / total_supply

        state = getattr(self, "reader_state", None)
        if state and successful_result and share_price is not None:
            state.on_called(successful_result, total_assets=total_assets, share_price=share_price)
        elif state and successful_result:
            state.on_unpriced_call(successful_result)

        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            # TODO: resolve FeeManager and ProtocolFeeTracker configuration at
            # every historical release/migration boundary before exporting it.
            performance_fee=None,
            management_fee=None,
            errors=errors or None,
        )
