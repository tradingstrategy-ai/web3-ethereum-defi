from enum import IntEnum


class FeeTier(IntEnum):
    """Different fee tiers for uniswap v3. Expressed as raw_fee value found on smart contracts."""

    #: 0.01% fee tier
    fee_1bps = 100

    #: 0.05% fee tier
    fee_5bps = 500

    #: 0.30% fee tier
    fee_30bps = 3000

    #: 1% fee tier
    fee_100bps = 10000

    def convert_to_multiplier(self) -> float:
        """Returns float value of fee tier
        e.g. if fee tier is fee_1bps -> returns 0.0001"""
        return float(self.value) / 1_000_000
