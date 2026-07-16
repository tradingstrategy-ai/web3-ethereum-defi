"""Historical reader for Securitize DSToken fund instruments."""

import datetime
from collections.abc import Iterable
from typing import TYPE_CHECKING

from eth_defi.erc_4626.vault import VaultReaderState
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

if TYPE_CHECKING:
    from eth_defi.securitize.vault import SecuritizeVault


class SecuritizeVaultHistoricalReader(VaultHistoricalReader):
    """Read DSToken supply and adapter-provided NAV/share.

    A DSToken exposes ERC-20 supply but no common on-chain NAV interface. The
    associated adapter therefore supplies the share price for known products.
    """

    def __init__(self, vault: "SecuritizeVault", stateful: bool):  # noqa: FBT001
        """Create a historical reader.

        :param vault:
            Securitize DSToken vault adapter.
        :param stateful:
            Whether to attach shared adaptive reader state.
        """

        super().__init__(vault)
        self.reader_state = VaultReaderState(vault) if stateful else None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Construct the ERC-20 total-supply multicall.

        :return:
            The historical total-supply call.
        """

        yield EncodedCall.from_contract_call(
            self.vault.share_token.contract.functions.totalSupply(),
            extra_data={"function": "totalSupply", "vault": self.vault.address},
            first_block_number=self.first_block,
        )

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        """Convert multicall results to a historical vault row.

        :param block_number:
            Historical block number.
        :param timestamp:
            Naive UTC block timestamp.
        :param call_results:
            Total-supply multicall result.
        :return:
            Historical DSToken price and supply record.
        """

        total_supply = None
        errors: list[str] = []
        for result in call_results:
            if result.call.extra_data.get("function") != "totalSupply":
                continue
            if result.success:
                total_supply = self.vault.share_token.convert_to_decimals(convert_int256_bytes_to_int(result.result))
            else:
                errors.append("Securitize DSToken totalSupply call failed")

        share_price = self.vault.fetch_share_price(block_number)
        total_assets = share_price * total_supply if total_supply is not None else None
        fee_data = self.vault.get_fee_data()
        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            performance_fee=fee_data.performance,
            management_fee=fee_data.management,
            errors=errors or None,
        )
