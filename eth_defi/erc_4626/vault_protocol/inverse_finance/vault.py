"""Inverse Finance sDOLA vault support.

Inverse Finance is a decentralised lending protocol built around the DOLA
stablecoin and the FiRM fixed-rate lending market. The sDOLA vault is an
ERC-4626 compliant savings vault where users stake DOLA and earn yield
derived from FiRM lending revenues.

Yield is generated through an automated xy=k auction mechanism: the vault
accumulates DBR (DOLA Borrowing Rights) rewards from the DolaSavings contract
and allows anyone to purchase the accrued DBR for DOLA via the ``buyDBR()``
function. Revenue from these purchases flows back into the vault, increasing
the DOLA reserve and therefore the share price.

Key features:

- No explicit management or performance fees
- Yield accrues through DBR auction mechanism (auto-compounding)
- Instant deposits and withdrawals
- DOLA staked is never rehypothecated or lent to third parties

- Homepage: https://www.inverse.finance/
- App: https://www.inverse.finance/firm
- Documentation: https://docs.inverse.finance/
- GitHub: https://github.com/InverseFinance/dola-savings
- Contract: https://etherscan.io/address/0xb45ad160634c528Cc3D2926d9807104FA3157305
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class InverseFinanceVault(ERC4626Vault):
    """Inverse Finance sDOLA vault support.

    sDOLA is a yield-bearing ERC-4626 vault where users deposit DOLA to earn
    yield from FiRM lending market revenues. Revenue is distributed through
    an xy=k DBR auction mechanism that auto-compounds DOLA back into the vault.

    - Homepage: https://www.inverse.finance/
    - App: https://www.inverse.finance/firm
    - Documentation: https://docs.inverse.finance/
    - GitHub: https://github.com/InverseFinance/dola-savings
    - Contract: https://etherscan.io/address/0xb45ad160634c528Cc3D2926d9807104FA3157305
    """

    def has_custom_fees(self) -> bool:
        """Whether this vault has deposit/withdrawal fees.

        sDOLA vault does not charge any explicit fees. Yield comes from
        the DBR auction mechanism.
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current management fee as a percent.

        Inverse Finance sDOLA does not charge management fees.

        :return:
            0.1 = 10%
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee as a percent.

        Inverse Finance sDOLA does not charge performance fees.

        :return:
            0.1 = 10%
        """
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period if any.

        sDOLA vault has no lock-up period. Withdrawals are instant.
        """
        return datetime.timedelta(days=0)

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link.

        :param referral:
            Optional referral code (not used currently).

        :return:
            Link to the Inverse Finance savings page.
        """
        return "https://www.inverse.finance/firm"
