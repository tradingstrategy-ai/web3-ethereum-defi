"""Calculate ERC-4626 vault APY, or profitability.

- Read tutorial :ref:`historical-erc-4626-apy`
- Read tutorial :ref:`read-erc-4626-apy`
- See about `APY <https://tradingstrategy.ai/glossary/annual-percentage-yield-apy>`__.
"""

import dataclasses
import datetime
from decimal import Decimal
from typing import TypeAlias

from eth_defi.chain import get_block_time
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.timestamp import get_block_timestamp


# eth_typing version does not go through the type alias
BlockNumber: TypeAlias = int


@dataclasses.dataclass(frozen=True, slots=True)
class ERC4626Profitability:
    """Capture the data needed for the vault profitability calculation."""

    vault: ERC4626Vault
    start_block: BlockNumber
    end_block: BlockNumber
    share_prices: dict[BlockNumber, Decimal]
    timestamps: dict[BlockNumber, datetime.datetime]

    def calculate_profitability(
        self,
        annualise=True,
        year_in_seconds=365 * 24 * 3600,
    ) -> float:
        """Get profitability for the whole duration of the sampling period.

        This is the same as `APY <https://tradingstrategy.ai/glossary/annual-percentage-yield-apy>__`.
        But it's incorrect to use term APY was some vaults may incur losses as well.

        :param annualise:
            If True, calculate profit % if we can maintain this profitability for a year.

        :param year_in_seconds:
            Allow custom year durations.

        :return:
            Profitability as a percentage, either annualised or not.

            0.07 means 7% APY.
        """
        share_price_begin = self.share_prices.get(self.start_block)
        share_price_end = self.share_prices.get(self.end_block)

        assert share_price_begin is not None, "Share price at the beginning of the period is not available."
        assert share_price_begin is not None, "Share price at the end of the period is not available."

        assert share_price_begin > 0
        assert share_price_begin > 0

        profit = (share_price_end - share_price_begin) / share_price_begin

        if annualise:
            start_time = self.timestamps.get(self.start_block)
            end_time = self.timestamps.get(self.end_block)
            duration = end_time - start_time
            duration_seconds = duration.total_seconds()
            annualisation_factor = year_in_seconds / duration_seconds
            return float(profit) * annualisation_factor
        else:
            return float(profit)

    def get_time_range(self) -> tuple[datetime.datetime, datetime.datetime]:
        """Get the time range of the profitability data.

        :return:
            Tuple of start and end timestamps.
        """
        start_time = self.timestamps.get(self.start_block)
        end_time = self.timestamps.get(self.end_block)
        return start_time, end_time

    def get_block_range(self) -> tuple[BlockNumber, BlockNumber]:
        """Get the block range of the profitability data.

        :return:
            Tuple of start and end block numbers.
        """
        return self.start_block, self.end_block

    def get_share_price_range(self) -> tuple[Decimal, Decimal]:
        """Get the share price range of the profitability data.

        :return:
            Tuple of start and end share prices.
        """
        start_price = self.share_prices.get(self.start_block)
        end_price = self.share_prices.get(self.end_block)
        return start_price, end_price


def estimate_4626_profitability(
    vault: ERC4626Vault,
    start_block: int,
    end_block: int,
    sample_count=2,
) -> ERC4626Profitability:
    """Uses archive node and share price to calculate.

    Get N samples of share price data.

    - Uses archive node to read historiocal share price for profitability calculation.
    """

    assert isinstance(vault, ERC4626Vault), "vault must be an instance of ERC4626Vault"
    assert sample_count == 2, "For now, we only take start and end sample"
    assert end_block > start_block, f"End block must be greater than start block: {start_block} - {end_block}"

    web3 = vault.web3

    start_time = get_block_timestamp(web3, start_block)
    end_time = get_block_timestamp(web3, end_block)

    start_share_price = vault.fetch_share_price(block_identifier=start_block)
    end_share_price = vault.fetch_share_price(block_identifier=end_block)

    return ERC4626Profitability(
        vault=vault,
        start_block=start_block,
        end_block=end_block,
        share_prices={
            start_block: start_share_price,
            end_block: end_share_price,
        },
        timestamps={
            start_block: start_time,
            end_block: end_time,
        },
    )


def estimate_4626_recent_profitability(
    vault: ERC4626Vault,
    lookback_window: datetime.timedelta,
) -> ERC4626Profitability:
    """Get the real-time vault profitability.

    - Uses block time to estimate the number of blocks in the sample duration for the profitability calculation.

    See :py:func:`estimate_4626_profitability` for more details.

    :param vault:
        ERC-4626 vault to estimate profitability for.

    :param lookback_window:
        How far back we do we look for profitability calculation.

    :return:
        Profitability data instance.
    """

    web3 = vault.web3
    chain_id = web3.eth.chain_id
    block_time = get_block_time(chain_id)
    blocks_per_second = 1.0 / block_time

    end_block = web3.eth.block_number
    start_block = end_block - int(lookback_window.total_seconds() * blocks_per_second)

    return estimate_4626_profitability(
        vault=vault,
        start_block=start_block,
        end_block=end_block,
    )
