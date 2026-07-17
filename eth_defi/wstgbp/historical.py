"""Historical reader for Wren Staked tGBP."""

# Reader classes intentionally mirror :class:`VaultHistoricalReader` signatures.
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
    from eth_defi.wstgbp.vault import WSTGBPVault


class WSTGBPVaultHistoricalReader(VaultHistoricalReader):
    """Read historical supply and NAV/share for Wren Staked tGBP.

    wstGBP does not implement ERC-4626 ``convertToAssets()`` or
    ``totalAssets()``. Its canonical on-chain share price is
    ``navprice()``. Historical TVL is therefore ``totalSupply() * navprice()``
    in the ERC-20 denomination returned by ``gem()``.
    """

    def __init__(self, vault: "WSTGBPVault", stateful: bool):
        """Create a Wren Staked tGBP historical reader.

        :param vault:
            Wren Staked tGBP vault adapter.
        :param stateful:
            Whether to attach adaptive reader state used by the shared
            historical multicaller.
        """

        super().__init__(vault)
        if stateful:
            self.reader_state = VaultReaderState(vault)
        else:
            self.reader_state = None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Construct Wren Staked tGBP historical multicalls.

        :return:
            Multicall batch reading ERC-20 ``totalSupply()`` and wstGBP
            ``navprice()``.
        """

        yield EncodedCall.from_contract_call(
            self.vault.share_token.contract.functions.totalSupply(),
            extra_data={
                "function": "totalSupply",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield EncodedCall.from_contract_call(
            self.vault.wstgbp_contract.functions.navprice(),
            extra_data={
                "function": "navprice",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield EncodedCall.from_contract_call(
            self.vault.wstgbp_contract.functions.mintable(),
            extra_data={
                "function": "mintable",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield EncodedCall.from_contract_call(
            self.vault.wstgbp_contract.functions.burnable(),
            extra_data={
                "function": "burnable",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        """Convert Wren Staked tGBP multicall results to a vault price row.

        :param block_number:
            Historical block number.
        :param timestamp:
            Naive UTC block timestamp.
        :param call_results:
            Multicall results for calls from :meth:`construct_multicalls`.
        :return:
            Historical NAV/share, supply, TVL and observed error information.
        """

        total_supply: Decimal | None = None
        share_price: Decimal | None = None
        deposits_open: bool | None = None
        redemption_open: bool | None = None
        state_result: EncodedCallResult | None = None
        errors: list[str] = []

        for result in call_results:
            function = result.call.extra_data.get("function")
            if function == "totalSupply":
                if result.success:
                    raw_total_supply = convert_int256_bytes_to_int(result.result)
                    total_supply = self.vault.share_token.convert_to_decimals(raw_total_supply)
                else:
                    errors.append("wstGBP totalSupply call failed")
            elif function == "navprice":
                if result.success:
                    raw_share_price = convert_int256_bytes_to_int(result.result)
                    share_price = Decimal(raw_share_price) / Decimal(10**18)
                    state_result = result
                else:
                    errors.append("wstGBP navprice call failed")
            elif function == "mintable":
                if result.success:
                    deposits_open = bool(convert_int256_bytes_to_int(result.result))
                else:
                    errors.append("wstGBP mintable call failed")
            elif function == "burnable":
                if result.success:
                    redemption_open = bool(convert_int256_bytes_to_int(result.result))
                else:
                    errors.append("wstGBP burnable call failed")

        total_assets = share_price * total_supply if share_price is not None and total_supply is not None else None

        if self.reader_state is not None and state_result is not None and total_assets is not None:
            self.reader_state.on_called(
                state_result,
                total_assets=total_assets,
                share_price=share_price,
            )

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
            deposits_open=deposits_open,
            redemption_open=redemption_open,
        )
