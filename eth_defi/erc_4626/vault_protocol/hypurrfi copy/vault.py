"""HypurrFi vault support."""

import datetime
import logging
import re

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


def is_hypurrfi_vault_name(name: str) -> bool:
    """Check if a vault name matches HypurrFi naming pattern.

    HypurrFi vault names:
    - Start with "hy" (case insensitive)
    - Contain a hyphen "-" (possibly with spaces around it)
    - End with a digit

    Examples:
    - "hyUSDC-0"
    - "hyWETH-1"
    - "hyUSDXL (Purr) - 2"
    - "hyUSDXL(PURR)-2" (symbol format)

    :param name:
        The vault token name to check.

    :return:
        True if the name matches the HypurrFi pattern.
    """
    if not name:
        return False

    # Pattern: starts with "hy", contains "-" (with optional spaces), ends with digit
    # Examples: "hyUSDC-0", "hyUSDXL (Purr) - 2", "hyUSDXL(PURR)-2"
    pattern = r"^hy.+\s*-\s*\d+$"
    return bool(re.match(pattern, name, re.IGNORECASE))


class HypurrFiVault(ERC4626Vault):
    """HypurrFi vault support.

    HypurrFi is a leveraged lending protocol on Hyperliquid's HyperEVM chain.
    The protocol uses FraxlendPair contracts for isolated lending markets.

    Key features:
    - Isolated lending markets with one asset and one collateral token per pair
    - Built on FraxlendPair contract architecture
    - Supports USDXL synthetic dollar for leveraged positions
    - TVL over $300M across pooled and isolated lending markets

    Links:
    - Homepage: https://www.hypurr.fi/
    - App: https://app.hypurr.fi/
    - Documentation: https://docs.hypurr.fi/
    - Twitter: https://x.com/HypurrFi
    - GitHub: https://github.com/hypurrfi
    - Security audit: https://x.com/PashovAuditGrp/status/1913215299320414234

    Example vault:
    - `hyUSDC-0 on HyperEVMScan <https://hyperevmscan.io/address/0x8001e1e7b05990d22dd8cdb9737f9fe6589827ce>`__
    """

    def has_custom_fees(self) -> bool:
        """HypurrFi uses FraxlendPair which has interest-based fees internalised."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Fees are internalised in the interest rate mechanism.

        FraxlendPair contracts use utilisation-based interest rates where
        fees are incorporated into the interest spread.
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fees are internalised in the interest rate mechanism.

        Performance fees are not explicitly charged; instead, the protocol
        earns from the interest rate spread between lenders and borrowers.
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """No lockup for lending positions.

        Users can withdraw at any time subject to liquidity availability.
        """
        return datetime.timedelta(days=0)

    def get_link(self, referral: str | None = None) -> str:
        """Get link to the HypurrFi app.

        HypurrFi uses the vault address in their app URL structure.
        """
        return f"https://app.hypurr.fi/lend/{self.vault_address}"
