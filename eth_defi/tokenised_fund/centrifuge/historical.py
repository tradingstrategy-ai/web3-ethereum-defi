"""Historical reads for Centrifuge permissioned tranche tokens."""

# Reader classes intentionally mirror :class:`VaultHistoricalReader` signatures.
# ruff: noqa: ARG002, FBT001

import datetime
from collections.abc import Iterable
from decimal import Decimal
from typing import TYPE_CHECKING

from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

if TYPE_CHECKING:
    from eth_defi.tokenised_fund.centrifuge.vault import CentrifugeTrancheVault


class CentrifugeTrancheHistoricalReader(VaultHistoricalReader):
    """Read supply without inventing JTRSY NAV or TVL.

    The direct ``Tranche`` token exposes ERC-20 supply, but no NAV/share price.
    The linked Centrifuge pool vault owns valuation and dealing semantics. This
    reader records available supply and explicitly leaves price and TVL absent.
    """

    def __init__(self, vault: "CentrifugeTrancheVault", stateful: bool):
        """Create a read-only tranche-token history reader.

        :param vault:
            Direct Centrifuge Tranche token adapter.
        :param stateful:
            Ignored because no NAV call exists from which adaptive state could
            safely be updated.
        """

        super().__init__(vault)
        self.reader_state = None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Construct the ERC-20 supply read.

        :return:
            One ``totalSupply()`` multicall.
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
        """Convert token supply to an explicitly unpriced history row.

        :param block_number:
            Historical block number.
        :param timestamp:
            Naive UTC block timestamp.
        :param call_results:
            Results for :meth:`construct_multicalls`.
        :return:
            Supply-only historical record with unavailable price and TVL.
        """

        total_supply: Decimal | None = None
        errors = ["Centrifuge Tranche token has no on-chain NAV/share source; linked vault valuation is not configured"]
        for result in call_results:
            if result.call.extra_data.get("function") == "totalSupply" and result.success:
                total_supply = self.vault.share_token.convert_to_decimals(convert_int256_bytes_to_int(result.result))
            elif result.call.extra_data.get("function") == "totalSupply":
                errors.append("Centrifuge Tranche totalSupply call failed")

        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=None,
            total_assets=None,
            total_supply=total_supply,
            performance_fee=None,
            management_fee=None,
            errors=errors,
            deposits_open=False,
            redemption_open=False,
        )
