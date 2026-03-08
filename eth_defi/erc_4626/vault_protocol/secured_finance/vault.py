"""Secured Finance vault support."""

import logging

from eth_typing import BlockIdentifier, HexAddress

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


#: JPYC lender vault on Ethereum.
SECURED_FINANCE_JPYC_LENDER_VAULT_ADDRESS: HexAddress = "0x6f6046e59501e484152d46045ba5eecf1cab8935"


class SecuredFinanceVault(ERC4626Vault):
    """Secured Finance vault support.

    Secured Finance offers fixed-rate lending markets and lender vaults.

    - Homepage: https://secured.finance/
    - App: https://vaults.secured.finance/
    - Example vault: https://etherscan.io/address/0x6f6046e59501e484152d46045ba5eecf1cab8935#code
    """

    def has_custom_fees(self) -> bool:
        """Fee model is not yet mapped for this protocol."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Management fee is not yet mapped for this protocol."""
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Performance fee is not yet mapped for this protocol."""
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get link to the Secured Finance vault app."""
        return "https://vaults.secured.finance/"
