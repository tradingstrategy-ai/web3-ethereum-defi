"""Upshift vault support."""

import datetime
import logging

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class UpshiftVault(ERC4626Vault):
    """Upshift protocol vaults.

    Upshift democratises institutional-grade DeFi yield strategies through non-custodial vaults
    built on August infrastructure.

    Links:

    - `Homepage <https://www.upshift.finance/>`__
    - `Documentation <https://docs.upshift.finance/>`__
    - `Example vault on Etherscan <https://etherscan.io/address/0x69fc3f84fd837217377d9dae0212068ceb65818e>`__
    - `Implementation contract on Etherscan <https://etherscan.io/address/0x83AF2736AD2f59BA60F2da1493DE95730Bc0649d#code>`__
    - `Twitter <https://x.com/upshift_fi>`__

    Fee mechanism:

    Upshift vaults have multiple fee types that are configured per-vault and managed by the vault operator.
    The fee functions in the smart contract include:

    - ``withdrawalFee()``: Fee charged on standard withdrawals
    - ``instantRedemptionFee()``: Higher fee for immediate redemptions bypassing the claim queue
    - Management fees are charged periodically via ``chargeManagementFee()``

    See the `TokenizedAccount implementation <https://etherscan.io/address/0x83AF2736AD2f59BA60F2da1493DE95730Bc0649d#code>`__
    for the fee collection logic.
    """

    def has_custom_fees(self) -> bool:
        """Upshift has withdrawal and instant redemption fees."""
        return True

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Upshift vaults use a daily claim processing system.

        Withdrawals are processed through a request-claim system where users
        request redemption and then claim on designated days.
        """
        return datetime.timedelta(days=1)

    def get_link(self, referral: str | None = None) -> str:
        """Get link to the vault on Upshift app.

        URL format: https://app.upshift.finance/pools/{chain_id}/{checksummed_address}
        """
        chain_id = self.chain_id
        checksummed_address = Web3.to_checksum_address(self.vault_address)
        return f"https://app.upshift.finance/pools/{chain_id}/{checksummed_address}"
