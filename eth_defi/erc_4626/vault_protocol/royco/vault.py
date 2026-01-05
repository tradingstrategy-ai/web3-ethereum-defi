"""Royco Protocol WrappedVault support."""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class RoycoVault(ERC4626Vault):
    """Royco Protocol WrappedVault support.

    Royco is an Incentivised Action Market (IAM) Protocol that allows protocols
    to create incentivised ERC-4626 vault wrappers with integrated rewards systems.
    The WrappedVault contract wraps underlying vaults and adds reward distribution
    functionality, supporting multiple simultaneous reward programmes.

    - Homepage: https://royco.org/
    - Documentation: https://docs.royco.org/
    - Github: https://github.com/roycoprotocol/royco
    - Example vault: https://etherscan.io/address/0x887d57a509070a0843c6418eb5cffc090dcbbe95

    Contract addresses:
    - WrappedVaultFactory: 0x75e502644284edf34421f9c355d75db79e343bca
    - WrappedVault implementation: 0x3c44c20377e252567d283dc7746d1bea67eb3e66
    - VaultMarketHub: 0xa97eCc6Bfda40baf2fdd096dD33e88bd8e769280

    Audits:
    - Spearbit (October 2024)
    - Cantina Private Competition
    - Cantina Open Competition

    See: https://docs.royco.org/for-incentive-providers/audits
    """

    def has_custom_fees(self) -> bool:
        """Royco vaults wrap underlying vaults.

        Fees are handled by the underlying wrapped vault, not by the wrapper itself.
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fees are determined by the underlying wrapped vault."""
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fees are determined by the underlying wrapped vault."""
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Lock-up depends on the underlying vault."""
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Link to Royco homepage.

        Individual vault pages are not available on the Royco interface.
        """
        return "https://royco.org/"
