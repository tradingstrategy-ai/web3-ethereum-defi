"""Hyperlend Wrapped HLP vault support.

Wrapped HyperLiquidity Provider (WHLP) is a tokenised version of HyperLiquidity Provider (HLP).
By minting WHLP, users earn trading fees from Hyperliquid while retaining full liquidity and
DeFi composability on HyperEVM.

Key features:

- WHLP token appreciates over time as HLP yields accrue
- Users deposit USDT0 to mint WHLP
- 10% performance fee on yield
- No management fees or deposit/withdrawal fees
- Managed by Paxos Labs
- Integrated with HyperLend and Looping Collective

- Homepage: https://app.hyperlend.finance/hlp
- Documentation: https://docs.loopingcollective.org/products/wrapped-hlp
- Contract: https://hyperevmscan.io/address/0x06fd9d03b3d0f18e4919919b72d30c582f0a97e5
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class WrappedHLPVault(ERC4626Vault):
    """Hyperlend Wrapped HLP vault support.

    Wrapped HyperLiquidity Provider (WHLP) is a tokenised version of HyperLiquidity Provider (HLP).
    By minting WHLP, users earn trading fees from Hyperliquid while retaining full liquidity and
    DeFi composability on HyperEVM.

    The token appreciates over time as HLP yields accrue to it. Users deposit USDT0 to mint WHLP,
    which represents a claim on the underlying HLP vault's earnings.

    - Homepage: https://app.hyperlend.finance/hlp
    - Documentation: https://docs.loopingcollective.org/products/wrapped-hlp
    - Fee documentation: https://docs.loopingcollective.org/products/wrapped-hlp
    - Contract: https://hyperevmscan.io/address/0x06fd9d03b3d0f18e4919919b72d30c582f0a97e5
    """

    @property
    def name(self) -> str:
        """Override the vault name.

        The on-chain name is generic, so we provide a more descriptive name.
        """
        return "Wrapped HLP"

    def has_custom_fees(self) -> bool:
        """Whether this vault has deposit/withdrawal fees.

        WHLP does not charge deposit/withdrawal fees. The only fee is a 10%
        performance fee on yield.
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current management fee as a percent.

        WHLP does not charge management fees.

        :return:
            0.1 = 10%
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee as a percent.

        WHLP charges a 10% performance fee on yield.

        :return:
            0.1 = 10%
        """
        return 0.10

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period if any.

        WHLP has no lock-up period.
        """
        return datetime.timedelta(days=0)

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link.

        :param referral:
            Optional referral code (not used currently).

        :return:
            Link to the Hyperlend WHLP page.
        """
        return "https://app.hyperlend.finance/hlp"
