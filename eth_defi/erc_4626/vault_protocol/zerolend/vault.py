"""ZeroLend protocol vault support."""

import logging

from eth_defi.erc_4626.vault_protocol.royco.vault import RoycoVault

logger = logging.getLogger(__name__)


class ZeroLendVault(RoycoVault):
    """ZeroLend protocol vault support.

    ZeroLend is a multi-chain DeFi lending protocol built on Layer 2 solutions,
    based on Aave V3. It specialises in Liquid Restaking Tokens (LRTs) lending,
    Real World Assets (RWAs) lending, and account abstraction.

    ZeroLend vaults use Royco Protocol's WrappedVault infrastructure for
    incentivised vault wrappers with integrated rewards systems.

    - Homepage: https://zerolend.xyz/
    - Application: https://app.zerolend.xyz/
    - Documentation: https://docs.zerolend.xyz/
    - Github: https://github.com/zerolend
    - Twitter: https://x.com/zerolendxyz
    - DefiLlama: https://defillama.com/protocol/zerolend

    Example vault (ZeroLend RWA USDC wrapped by Royco):
    - https://etherscan.io/address/0x887d57a509070a0843c6418eb5cffc090dcbbe95

    Audits:
    - Mundus Security
    - PeckShield
    - Halborn
    - Zokyo Security
    - Immunefi Bug Bounty

    See: https://docs.zerolend.xyz/security/audits
    """

    def get_link(self, referral: str | None = None) -> str:
        """Link to ZeroLend homepage."""
        return "https://zerolend.xyz/"
