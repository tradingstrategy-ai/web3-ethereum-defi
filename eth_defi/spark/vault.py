"""Spark protocol vault support.

Spark is a decentralised non-custodial liquidity protocol where users can
participate as suppliers or borrowers. It is built on top of the MakerDAO/Sky
infrastructure and allows users to earn yield on their stablecoins through
the Sky Savings Rate (SSR).

The sUSDC vault is an ERC-4626 compliant tokenised vault that allows users
to deposit USDC and earn the Sky Savings Rate. The vault handles USDC deposits
by converting USDC to USDS using the dss-lite-psm (Peg Stability Module) and
then depositing into sUSDS to earn yield.

Key features:

- No deposit/withdrawal fees at the smart contract level
- Yield accrues through the Sky Savings Rate (SSR)
- Instant deposits and withdrawals (subject to PSM liquidity)
- Fully backed by sUSDS (savings USDS)

- Homepage: https://spark.fi/
- Savings page: https://app.spark.fi/savings/mainnet/spusdc
- Documentation: https://docs.spark.fi/
- GitHub: https://github.com/sparkdotfi/spark-vaults
- Contract: https://etherscan.io/address/0xbc65ad17c5c0a2a4d159fa5a503f4992c7b545fe
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class SparkVault(ERC4626Vault):
    """Spark protocol vault support.

    Spark sUSDC vault allows users to deposit USDC and earn the Sky Savings Rate.
    The vault converts USDC to USDS via the Peg Stability Module (PSM) and deposits
    into sUSDS to earn yield.

    - Homepage: https://spark.fi/
    - Savings page: https://app.spark.fi/savings/mainnet/spusdc
    - Documentation: https://docs.spark.fi/
    - GitHub: https://github.com/sparkdotfi/spark-vaults
    - Contract: https://etherscan.io/address/0xbc65ad17c5c0a2a4d159fa5a503f4992c7b545fe
    """

    def has_custom_fees(self) -> bool:
        """Whether this vault has deposit/withdrawal fees.

        Spark sUSDC vault does not charge deposit/withdrawal fees at the smart
        contract level. However, the underlying PSM may have small fees (tin/tout).
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current management fee as a percent.

        Spark does not charge management fees. Yield comes directly from the
        Sky Savings Rate.

        :return:
            0.1 = 10%
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee as a percent.

        Spark does not charge performance fees on the sUSDC vault.

        :return:
            0.1 = 10%
        """
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period if any.

        Spark sUSDC vault has no lock-up period. Withdrawals are instant
        subject to PSM liquidity.
        """
        return datetime.timedelta(days=0)

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link.

        :param referral:
            Optional referral code (not used currently).

        :return:
            Link to the Spark savings page.
        """
        return "https://app.spark.fi/savings/mainnet/spusdc"
