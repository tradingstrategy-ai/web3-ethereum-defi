"""Mainstreet Finance protocol vault support.

Mainstreet Finance (developed by Mainstreet Labs) is a synthetic USD stablecoin
ecosystem built on multi-asset collateralisation. The protocol delivers
institutional-grade delta-neutral yield strategies through a dual-token system -
msUSD (the synthetic stablecoin) and smsUSD/Staked msUSD (the staked version
that earns yield from options arbitrage strategies).

Key features:

- 20% protocol fee on yields (10% to insurance fund, 10% to treasury)
- 80% of yields distributed to smsUSD holders
- Cooldown period of up to 90 days for withdrawals (configurable by governance)
- Yield comes from CME index box spreads and options arbitrage strategies

The smart contracts are developed by Mainstreet Labs.

- Homepage: https://mainstreet.finance/
- Documentation: https://mainstreet-finance.gitbook.io/mainstreet.finance
- GitHub: https://github.com/Mainstreet-Labs/mainstreet-core
- Twitter: https://x.com/Main_St_Finance
- Legacy smsUSD contract (Sonic): https://sonicscan.org/address/0xc7990369DA608C2F4903715E3bD22f2970536C29
- Staked msUSD contract (Ethereum): https://etherscan.io/address/0x890a5122aa1da30fec4286de7904ff808f0bd74a
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


#: Custom vault names for Mainstreet vaults by address (lowercased)
#:
#: Some vaults have generic on-chain names that we override for clarity.
MAINSTREET_VAULT_NAMES: dict[str, str] = {
    # Ethereum Staked msUSD vault
    "0x890a5122aa1da30fec4286de7904ff808f0bd74a": "Staked msUSD",
}


class MainstreetVault(ERC4626Vault):
    """Mainstreet Finance protocol vault support.

    Mainstreet smsUSD vault allows users to stake msUSD and earn yield from
    protocol options arbitrage strategies (CME index box spreads). The vault
    implements a flexible cooldown mechanism controlled by governance.

    The smart contracts are developed by Mainstreet Labs.

    - Homepage: https://mainstreet.finance/
    - Documentation: https://mainstreet-finance.gitbook.io/mainstreet.finance
    - GitHub: https://github.com/Mainstreet-Labs/mainstreet-core
    - Twitter: https://x.com/Main_St_Finance
    - Legacy smsUSD contract (Sonic): https://sonicscan.org/address/0xc7990369DA608C2F4903715E3bD22f2970536C29
    - Staked msUSD contract (Ethereum): https://etherscan.io/address/0x890a5122aa1da30fec4286de7904ff808f0bd74a
    """

    @property
    def name(self) -> str:
        """Return a human-readable name for this vault.

        Uses custom names for known vaults, falls back to on-chain token name.
        """
        custom_name = MAINSTREET_VAULT_NAMES.get(self.vault_address.lower())
        if custom_name:
            return custom_name
        return super().name

    def has_custom_fees(self) -> bool:
        """Whether this vault has deposit/withdrawal fees.

        Mainstreet smsUSD vault does not charge deposit/withdrawal fees at the
        smart contract level.
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current management fee as a percent.

        Mainstreet does not charge management fees. However, the protocol takes
        20% of yields (10% insurance fund + 10% treasury).

        :return:
            0.1 = 10%
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee as a percent.

        Mainstreet takes 20% of gross yields:
        - 10% to insurance fund
        - 10% to treasury

        :return:
            0.1 = 10%
        """
        return 0.20

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period if any.

        Mainstreet smsUSD vault has a governance-configurable cooldown period
        of up to 90 days (MAX_COOLDOWN_DURATION). Default is 7 days.
        When cooldown is enabled, users must initiate cooldownAssets/cooldownShares
        and wait before claiming via unstake(). When cooldown is disabled
        (duration = 0), withdrawals are instant.

        This returns the default cooldown for conservative estimation.
        The actual cooldown duration can be read from the contract.
        """
        return datetime.timedelta(days=7)

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link.

        :param referral:
            Optional referral code (not used currently).

        :return:
            Link to the Mainstreet Finance app.
        """
        return "https://mainstreet.finance/"
