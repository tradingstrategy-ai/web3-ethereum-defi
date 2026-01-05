"""Yuzu Money protocol vault support.

Yuzu Money is a DeFi protocol that packages high-yield strategies into an
overcollateralised stablecoin (yzUSD). The protocol is deployed on the Plasma
chain and offers multiple products including yzUSD, syzUSD (staked yzUSD),
and yzPP (Yuzu Protection Pool).

Key features:

- yzUSD: Overcollateralised stablecoin targeting $1 USD, backed 1:1 by USDC
- syzUSD: Yield-bearing token received when staking yzUSD (ERC-4626 vault)
- yzPP: Junior tranche / insurance liquidity pool providing first-loss capital
- No performance fees - uses yield-smoothing mechanism instead
- Two-step redemption process with delay period

The yzPP vault (Yuzu Protection Pool) is the insurance liquidity pool that:

- Accrues yield from protocol strategies
- Bears first-loss risk on underlying strategies
- Uses time-delayed redemptions for risk management

Fee structure:

Yuzu Money does not charge traditional performance fees. Instead, they employ
a yield-smoothing mechanism where a consistent weekly yield target is distributed,
backed by a Reserve Fund that acts as a buffer. See:
https://yuzu-money.gitbook.io/yuzu-money/faq-1/performance-fee

Security:

- Audited by Pashov Audit Group (August 2025)
- Hypernative threat monitoring
- Nexus Mutual smart contract insurance
- 3/5 multisig with 48-hour timelock

- Homepage: https://yuzu.money/
- App: https://app.yuzu.money/
- Documentation: https://yuzu-money.gitbook.io/yuzu-money/
- Fee documentation: https://yuzu-money.gitbook.io/yuzu-money/faq-1/performance-fee
- Audit report: https://github.com/pashov/audits/blob/master/team/pdf/YuzuUSD-security-review_2025-08-28.pdf
- DefiLlama: https://defillama.com/protocol/yuzu-money
- Twitter: https://x.com/YuzuMoneyX
- Contract (yzPP): https://plasmascan.to/address/0xebfc8c2fe73c431ef2a371aea9132110aab50dca
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class YuzuMoneyVault(ERC4626Vault):
    """Yuzu Money protocol vault support.

    Yuzu Money yzPP (Protection Pool) vault is the junior tranche of the protocol
    that provides first-loss capital and earns higher yields in return.

    Key characteristics:

    - ERC-4626 compliant vault
    - Time-delayed redemptions (not instant)
    - No performance fees (yield-smoothing mechanism)
    - Bears first-loss risk for protocol strategies

    Fee structure:

    Yuzu Money does not charge traditional performance fees. Instead, they employ
    a yield-smoothing mechanism. See:
    https://yuzu-money.gitbook.io/yuzu-money/faq-1/performance-fee

    - Homepage: https://yuzu.money/
    - App: https://app.yuzu.money/
    - Documentation: https://yuzu-money.gitbook.io/yuzu-money/
    - Contract (yzPP): https://plasmascan.to/address/0xebfc8c2fe73c431ef2a371aea9132110aab50dca
    """

    def has_custom_fees(self) -> bool:
        """Whether this vault has deposit/withdrawal fees.

        Yuzu Money does not charge performance fees. They use a yield-smoothing
        mechanism instead. See:
        https://yuzu-money.gitbook.io/yuzu-money/faq-1/performance-fee
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current management fee as a percent.

        Yuzu Money does not charge management fees.

        :return:
            0.0 - no management fee
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee as a percent.

        Yuzu Money does not charge traditional performance fees. Instead, they
        employ a yield-smoothing mechanism where a consistent weekly yield target
        is distributed, backed by a Reserve Fund.

        See: https://yuzu-money.gitbook.io/yuzu-money/faq-1/performance-fee

        :return:
            0.0 - no performance fee
        """
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period if any.

        Yuzu Money yzPP vault uses time-delayed redemptions.
        Users must initiate a redeem order and wait for the delay period
        before finalising the redemption.

        The exact delay period is configurable by the protocol admin.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link.

        :param referral:
            Optional referral code (not used currently).

        :return:
            Link to the Yuzu Money app.
        """
        return "https://app.yuzu.money/"
