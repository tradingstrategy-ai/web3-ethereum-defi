import dataclasses
from decimal import Decimal

from eth_typing import BlockNumber

from eth_defi.erc_4626.vault import ERC4626Vault


@dataclasses.dataclass(frozen=True, slots=True)
class ERC4626Profitability:
    vault: ERC4626Vault
    start_block: BlockNumber
    end_block: BlockNumber
    samples: dict[BlockNumber, Decimal] = {}

    def get_profitability(self) -> Decimal:
        """Get profitability for the whole duration of the sampling period."""

        share_price_begin = self.samples.get(self.start_block)
        share_price_end = self.samples.get(self.end_block)


def estimate_4626_profitability(
    vault: ERC4626Vault,
    start_block: int | None = None,
    end_block: int | None = None,

) -> ERC4626Profitability:
    """Uses archive node and share price to calculate.

    Get N samples of share price data.
    """