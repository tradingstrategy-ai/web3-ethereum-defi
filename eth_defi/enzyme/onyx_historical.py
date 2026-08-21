"""Historical price reader for Enzyme Onyx vaults."""

# ruff: noqa: FBT001

import datetime
from collections.abc import Iterable
from decimal import Decimal
from typing import TYPE_CHECKING

from eth_abi import decode

from eth_defi.erc_4626.vault import VaultReaderState
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

if TYPE_CHECKING:
    from eth_defi.enzyme.onyx_vault import EnzymeVault


class EnzymeVaultHistoricalReader(VaultHistoricalReader):
    """Read Enzyme Onyx share price, supply, and value-asset TVL.

    The current Onyx ``sharePrice`` field and Shares ERC-20 token use
    18-decimal fixed-point values. The reader reports the stored share value
    in the vault's declared value asset and calculates total value as
    ``share_price * total_supply``. For the Base ``USD`` value-asset label,
    the adapter deliberately exports these values in USDC terms so generic
    lifetime metrics remain comparable; this is not proof that USDC was the
    active deposit token. Historical deposit and redemption
    availability is unsupported: Shares delegates these actions to mutable,
    potentially account-restricted handlers and cannot enumerate those
    handlers. The reader therefore leaves ``deposits_open`` and
    ``redemption_open`` as ``None`` rather than deriving them from price or
    supply observations.
    """

    def __init__(self, vault: "EnzymeVault", stateful: bool):
        """Create a historical reader, optionally with adaptive reader state."""

        super().__init__(vault)
        self.reader_state = VaultReaderState(vault) if stateful else None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Construct calls for the stored share price and ERC-20 supply."""

        yield EncodedCall.from_contract_call(
            self.vault.shares_contract.functions.sharePrice(),
            extra_data={"function": "share_price", "vault": self.vault.address},
            first_block_number=self.first_block,
        )
        yield EncodedCall.from_contract_call(
            self.vault.shares_contract.functions.totalSupply(),
            extra_data={"function": "total_supply", "vault": self.vault.address},
            first_block_number=self.first_block,
        )

    def process_result(self, block_number: int, timestamp: datetime.datetime, call_results: list[EncodedCallResult]) -> VaultHistoricalRead:
        """Convert Onyx price and supply calls to a historical vault row.

        Update the shared reader state after every usable response.  This keeps
        incremental scans anchored at their previous end block even when an
        Onyx vault's reported NAV remains unchanged for a long period.

        :param block_number:
            Block whose ``sharePrice`` and ``totalSupply`` responses are being
            decoded.
        :param timestamp:
            Naive UTC block timestamp.
        :param call_results:
            Multicall responses for this vault's price and supply calls.
        :return:
            The decoded price, supply, calculated value-asset TVL, and any
            per-call failures.
        """

        share_price = total_supply = None
        share_price_result = total_supply_result = None
        errors = []
        for result in call_results:
            function = result.call.extra_data["function"]
            if not result.success:
                errors.append(f"{function} call failed")
                continue
            if function == "share_price":
                share_price_result = result
                raw_price, _reported_at = decode(["uint256", "uint256"], result.result)
                share_price = Decimal(raw_price) / Decimal(10**18)
            elif function == "total_supply":
                total_supply_result = result
                total_supply = Decimal(convert_int256_bytes_to_int(result.result)) / Decimal(10**18)

        total_assets = share_price * total_supply if share_price is not None and total_supply is not None else None
        state = getattr(self, "reader_state", None)
        if state and share_price_result and share_price is not None:
            state.on_called(
                share_price_result,
                total_assets=total_assets,
                share_price=share_price,
            )
        elif state and total_supply_result:
            # A just-deployed Shares contract can have no reported NAV yet,
            # or a test deployment can omit its value asset altogether.
            # Record progress without inventing a price, while allowing a
            # future incremental scan to retry once its manager reports NAV.
            state.on_unpriced_call(total_supply_result)

        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            # TODO: Historical fee rates require FeeHandler/tracker replacement,
            # RateSet, EntranceFeeSet and ExitFeeSet event handling; current
            # metadata reads query the active components only.
            performance_fee=None,
            management_fee=None,
            errors=errors or None,
        )
