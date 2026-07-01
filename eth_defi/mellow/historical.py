"""Historical reader for Mellow Core Vaults."""

from __future__ import annotations

import datetime
from collections.abc import Iterable
from typing import TYPE_CHECKING

from eth_abi import decode

from eth_defi.erc_4626.vault import VaultReaderState
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.mellow.vault import convert_mellow_fee_d6_to_percent, convert_mellow_price_d18_to_share_price
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

if TYPE_CHECKING:
    from eth_defi.mellow.vault import MellowVault


class MellowVaultHistoricalReader(VaultHistoricalReader):
    """Read the currently supported Mellow historical state.

    The reader samples tokenised ``ShareManager.totalSupply()`` and the latest
    Mellow oracle report for the denomination asset. Mellow reports raw shares
    per raw asset as ``priceD18``; the reader converts it to the shared
    asset-per-share historical price convention and derives denomination-token
    TVL as ``share_price * total_supply``.
    """

    def __init__(self, vault: MellowVault, stateful: bool):  # noqa: FBT001
        """Create a Mellow historical reader.

        :param vault:
            Mellow vault adapter.

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
        """Construct Mellow historical multicalls.

        :return:
            Multicall batch reading ``ShareManager.totalSupply()``,
            ``Oracle.getReport(denomination_token)`` and FeeManager rates.
        """

        yield EncodedCall.from_contract_call(
            self.vault.share_manager_contract.functions.totalSupply(),
            extra_data={
                "function": "share_manager_total_supply",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )

        yield EncodedCall.from_contract_call(
            self.vault.fee_manager_contract.functions.performanceFeeD6(),
            extra_data={
                "function": "fee_manager_performance_fee_d6",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )

        yield EncodedCall.from_contract_call(
            self.vault.fee_manager_contract.functions.protocolFeeD6(),
            extra_data={
                "function": "fee_manager_protocol_fee_d6",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )

        denomination_token = self.vault.denomination_token
        if denomination_token is None:
            return

        yield EncodedCall.from_contract_call(
            self.vault.oracle_contract.functions.getReport(denomination_token.address),
            extra_data={
                "function": "oracle_get_report",
                "vault": self.vault.address,
                "asset_decimals": denomination_token.decimals,
                "share_token_decimals": self.vault.share_token.decimals,
            },
            first_block_number=self.first_block,
        )

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        """Convert Mellow multicall results to a vault price row.

        :param block_number:
            Historical block number.

        :param timestamp:
            Naive UTC block timestamp.

        :param call_results:
            Multicall results for calls from :py:meth:`construct_multicalls`.

        :return:
            :py:class:`VaultHistoricalRead` with Mellow share price and supply.
        """

        total_supply = None
        share_price = None
        performance_fee = None
        management_fee = None
        errors = []
        saw_oracle_report = False

        for result in call_results:
            function = result.call.extra_data.get("function")

            if function == "share_manager_total_supply":
                if result.success:
                    raw_total_supply = convert_int256_bytes_to_int(result.result)
                    total_supply = self.vault.share_token.convert_to_decimals(raw_total_supply)
                else:
                    errors.append("share_manager_total_supply call failed")
            elif function == "fee_manager_performance_fee_d6":
                if result.success:
                    performance_fee = convert_mellow_fee_d6_to_percent(convert_int256_bytes_to_int(result.result))
                else:
                    errors.append("fee_manager_performance_fee_d6 call failed")
            elif function == "fee_manager_protocol_fee_d6":
                if result.success:
                    # Mellow's protocol fee is an annual time-based share fee.
                    # The shared historical schema has only management and
                    # performance columns, so store it as management-like.
                    management_fee = convert_mellow_fee_d6_to_percent(convert_int256_bytes_to_int(result.result))
                else:
                    errors.append("fee_manager_protocol_fee_d6 call failed")
            elif function == "oracle_get_report":
                saw_oracle_report = True
                if result.success:
                    ((price_d18, _report_timestamp, is_suspicious),) = decode(
                        ["(uint224,uint32,bool)"],
                        result.result,
                    )
                    if is_suspicious:
                        errors.append("Mellow oracle report is suspicious")
                    else:
                        share_price = convert_mellow_price_d18_to_share_price(
                            price_d18=int(price_d18),
                            share_token_decimals=result.call.extra_data["share_token_decimals"],
                            asset_decimals=result.call.extra_data["asset_decimals"],
                        )
                        if share_price is None:
                            errors.append("Mellow oracle report priceD18 is zero")
                else:
                    errors.append("oracle_get_report call failed")

        if not saw_oracle_report:
            errors.append("Mellow denomination token is missing")

        total_assets = share_price * total_supply if share_price is not None and total_supply is not None else None

        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            performance_fee=performance_fee,
            management_fee=management_fee,
            errors=errors or None,
        )
