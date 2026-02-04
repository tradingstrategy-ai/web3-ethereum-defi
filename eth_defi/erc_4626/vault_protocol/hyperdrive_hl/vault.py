"""Hyperdrive vault support on HyperEVM.

Hyperdrive is the premier stablecoin money market on Hyperliquid and the foundational
layer for making everything on HyperCore liquid. It is an all-in-one DeFi hub offering
spot lending markets, liquid staking of HYPE, and advanced yield strategies.

Key features:

- Supply stablecoins to earn lending APY
- Borrow stablecoins against other assets
- Liquid stake HYPE to earn staking rewards whilst using it as collateral
- Use HLP as collateral or leverage HLP to maximise HLP yield
- Automated yield strategies with portfolio-focused approach

Security:

- Audited by Enigma Dark, Bail Security, and Obsidian Audits
- Backed by Binance Labs, Hack VC, Arrington Capital, and Delphi Ventures
- Protocol suffered a $782,000 exploit in 2025 (router contract vulnerability)

- Homepage: https://hyperdrive.fi/
- App: https://app.hyperdrive.fi/earn
- Documentation: https://hyperdrive-2.gitbook.io/hyperdrive/
- Twitter: https://x.com/hyperdrivedefi
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class HyperdriveVault(ERC4626Vault):
    """Hyperdrive vault support on HyperEVM.

    Hyperdrive is Hyperliquid's yield hub and the premier stablecoin money market
    on Hyperliquid. It enables users to supply stablecoins to earn lending APY,
    borrow stablecoins against other assets, and access liquid staking and
    yield strategies.

    The protocol provides automated yield strategies through its "Earn" product,
    which aggregates deposits into various yield-generating activities on HyperEVM.

    Note: The smart contracts are not verified on block explorers.

    - Homepage: https://hyperdrive.fi/
    - App: https://app.hyperdrive.fi/earn
    - Documentation: https://hyperdrive-2.gitbook.io/hyperdrive/
    - Twitter: https://x.com/hyperdrivedefi
    """

    def has_custom_fees(self) -> bool:
        """Whether this vault has deposit/withdrawal fees.

        Fee structure is not publicly documented.
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current management fee as a percent.

        Fee structure is not publicly documented for Hyperdrive vaults.

        :return:
            None - fee unknown
        """
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee as a percent.

        Fee structure is not publicly documented for Hyperdrive vaults.

        :return:
            None - fee unknown
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period if any.

        Lock-up period is not documented.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link.

        :param referral:
            Optional referral code (not used currently).

        :return:
            Link to the Hyperdrive Earn page.
        """
        return "https://app.hyperdrive.fi/earn"
