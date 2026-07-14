"""Historical reader for Midas tokenised products."""

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
    from eth_defi.midas.vault import MidasVault


class MidasVaultReaderState(VaultReaderState):
    """Persist adaptive Midas history scan state.

    Midas datafeeds publish share prices in USD and the historical reader
    already calculates TVL in USD.  Unlike ERC-4626 vaults, an mToken does not
    expose an ERC-20 denomination token, so the generic exchange-rate lookup
    cannot be used here.
    """

    @cached_property
    def exchange_rate(self) -> Decimal:
        """Return the USD exchange rate for Midas' USD-denominated NAV.

        :return:
            One, because Midas reader ``total_assets`` is already in USD.
        """

        return Decimal(1)


class MidasVaultHistoricalReader(VaultHistoricalReader):
    """Read historical supply and NAV/share for Midas products.

    Midas does not expose ERC-4626 ``convertToAssets()`` or ``totalAssets()``.
    The canonical on-chain share price is the Midas ``IDataFeed`` value exposed
    through ``getDataInBase18()``. Historical total assets are therefore
    calculated as ``totalSupply() * getDataInBase18()``.
    """

    def __init__(self, vault: "MidasVault", stateful: bool):
        """Create a Midas historical reader.

        :param vault:
            Midas vault adapter.
        :param stateful:
            Whether to attach adaptive reader state used by the shared
            historical multicaller.
        """

        super().__init__(vault)
        if stateful:
            self.reader_state = MidasVaultReaderState(vault)
        else:
            self.reader_state = None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Construct Midas historical multicalls.

        :return:
            Multicall batch reading ERC-20 ``totalSupply()`` and Midas
            ``getDataInBase18()`` NAV/share.
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
            self.vault.data_feed_contract.functions.getDataInBase18(),
            extra_data={
                "function": "getDataInBase18",
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
        """Convert Midas multicall results to a vault price row.

        :param block_number:
            Historical block number.
        :param timestamp:
            Naive UTC block timestamp.
        :param call_results:
            Multicall results for calls from :py:meth:`construct_multicalls`.
        :return:
            :py:class:`VaultHistoricalRead` with Midas NAV/share and supply.
        """

        total_supply: Decimal | None = None
        share_price: Decimal | None = None
        state_result: EncodedCallResult | None = None
        errors: list[str] = []

        for result in call_results:
            function = result.call.extra_data.get("function")

            if function == "totalSupply":
                if result.success:
                    raw_total_supply = convert_int256_bytes_to_int(result.result)
                    total_supply = self.vault.share_token.convert_to_decimals(raw_total_supply)
                else:
                    errors.append("Midas totalSupply call failed")

            elif function == "getDataInBase18":
                if result.success:
                    raw_share_price = convert_int256_bytes_to_int(result.result)
                    share_price = Decimal(raw_share_price) / Decimal(10**18)
                    state_result = result
                else:
                    errors.append("Midas getDataInBase18 call failed")

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
            deposits_open=False,
            redemption_open=False,
        )
