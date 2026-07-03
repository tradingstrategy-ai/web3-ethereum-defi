"""Historical reader for ODA-FACT tokenised fund contracts."""

# Reader classes intentionally mirror :class:`VaultHistoricalReader` signatures.
# ruff: noqa: FBT001

import datetime
from collections.abc import Iterable
from typing import TYPE_CHECKING

from eth_defi.erc_4626.vault import VaultReaderState
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

if TYPE_CHECKING:
    from eth_defi.oda_fact.vault import OdaFactVault


class OdaFactVaultHistoricalReader(VaultHistoricalReader):
    """Read ODA-FACT historical supply and estimated share price.

    The on-chain part of ODA-FACT price history is ERC-20 ``totalSupply()``.
    Share price is currently an explicit adapter-level estimate because the
    known ODA-FACT token surface does not expose ERC-4626-style share conversion
    or NAV history.
    """

    def __init__(self, vault: "OdaFactVault", stateful: bool):
        """Create an ODA-FACT historical reader.

        :param vault:
            ODA-FACT vault adapter.

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
        """Construct ODA-FACT historical multicalls.

        :return:
            Multicall batch reading ERC-20 ``totalSupply()``.
        """

        yield EncodedCall.from_contract_call(
            self.vault.share_token.contract.functions.totalSupply(),
            extra_data={
                "function": "totalSupply",
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
        """Convert ODA-FACT multicall results to a vault price row.

        :param block_number:
            Historical block number.

        :param timestamp:
            Naive UTC block timestamp.

        :param call_results:
            Multicall results for calls from :py:meth:`construct_multicalls`.

        :return:
            :py:class:`VaultHistoricalRead` with ODA-FACT share price and supply.
        """

        total_supply = None
        errors: list[str] = []

        for result in call_results:
            function = result.call.extra_data.get("function")
            if function != "totalSupply":
                continue

            if result.success:
                raw_total_supply = convert_int256_bytes_to_int(result.result)
                total_supply = self.vault.share_token.convert_to_decimals(raw_total_supply)
            else:
                errors.append("ODA-FACT totalSupply call failed")

        share_price = self.vault.fetch_share_price(block_number)
        total_assets = share_price * total_supply if total_supply is not None else None

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
        )
