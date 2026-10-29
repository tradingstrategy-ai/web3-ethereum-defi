"""EulerEarn redemption capacity rounding."""

from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager


class EulerEarnDepositManager(ERC4626DepositManager):
    """Preserve full exits when EulerEarn understates capacity by one raw share.

    The shared request builder requires a positive exact-call simulation before
    accepting the rounding discrepancy. Larger shortfalls remain unavailable.
    """

    def _is_redemption_capacity_rounding(self, raw_shares: int, maximum: int) -> bool:
        """Recognise only a one-raw-share underestimate with positive capacity."""
        return maximum > 0 and raw_shares - maximum == 1
