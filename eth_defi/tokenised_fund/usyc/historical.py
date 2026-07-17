"""Historical reader for Circle USYC."""

# Reader classes intentionally mirror :class:`VaultHistoricalReader` signatures.
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
    from eth_defi.tokenised_fund.usyc.vault import USYCVault


class USYCHistoricalReader(VaultHistoricalReader):
    """Read USYC ERC-20 supply and official historical NAV/share.

    USYC is not ERC-4626. Its Chainlink-compatible oracle publishes its NAV
    per token after business-day reconciliation, so scans derive TVL as token
    supply multiplied by the oracle answer.
    """

    def __init__(self, vault: "USYCVault", stateful: bool):
        """Create a USYC historical reader.

        :param vault: USYC vault adapter.
        :param stateful: Whether to attach shared adaptive reader state.
        """
        super().__init__(vault)
        self.reader_state = VaultReaderState(vault) if stateful else None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Construct token-supply and oracle calls.

        :return: Multicalls for ``totalSupply`` and ``latestRoundData``.
        """
        yield EncodedCall.from_contract_call(
            self.vault.share_token.contract.functions.totalSupply(),
            extra_data={"function": "totalSupply", "vault": self.vault.address},
            first_block_number=self.first_block,
        )
        yield EncodedCall.from_contract_call(
            self.vault.price_oracle_contract.functions.latestRoundData(),
            extra_data={"function": "latestRoundData", "vault": self.vault.address},
            first_block_number=self.vault.oracle_first_seen_at_block,
        )

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        """Convert supply and oracle observations to one historical row.

        :param block_number: Historical block number.
        :param timestamp: Naive UTC block timestamp.
        :param call_results: Multicall results at ``block_number``.
        :return: USYC supply, NAV/share and TVL record.
        """
        total_supply: Decimal | None = None
        share_price: Decimal | None = None
        price_result: EncodedCallResult | None = None
        errors: list[str] = []
        for result in call_results:
            function = result.call.extra_data["function"]
            if not result.success:
                errors.append(f"USYC {function} call failed")
                continue
            if function == "totalSupply":
                total_supply = self.vault.share_token.convert_to_decimals(convert_int256_bytes_to_int(result.result))
            elif function == "latestRoundData":
                _round_id, answer, _started_at, updated_at, _answered_in_round = decode(["uint80", "int256", "uint256", "uint256", "uint80"], bytes(result.result))
                if answer > 0 and updated_at > 0:
                    share_price = self.vault.convert_raw_share_price(answer)
                    price_result = result
                else:
                    errors.append("USYC oracle returned an invalid price observation")

        total_assets = share_price * total_supply if share_price is not None and total_supply is not None else None
        if self.reader_state is not None and price_result is not None and total_assets is not None:
            self.reader_state.on_called(price_result, total_assets=total_assets, share_price=share_price)
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
