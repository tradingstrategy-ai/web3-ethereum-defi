"""Sentiment protocol vault support.

Sentiment is a decentralised onchain lending protocol that enables users to
programmatically lend and borrow digital assets on Ethereum and L2s.

The protocol uses a SuperPool architecture where deposits are aggregated
across multiple underlying pools with configurable allocation strategies.
"""

import datetime
import logging

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class SentimentVault(ERC4626Vault):
    """Sentiment SuperPool vault.

    Sentiment SuperPools are ERC-4626 vault aggregators that manage deposits
    across multiple underlying lending pools. They implement fee mechanisms
    and asset reallocation strategies.

    Key features:
    - ERC-4626 compliant vault aggregator
    - Fees are taken from interest earned
    - Deposits are allocated across multiple pools via deposit/withdraw queues
    - Supports allocator-based reallocation

    - Homepage: https://www.sentiment.xyz/
    - Documentation: https://docs.sentiment.xyz/
    - GitHub: https://github.com/sentimentxyz/protocol-v2
    - Audits: https://github.com/sentimentxyz/protocol-v2/tree/master/audits
    - Example vault: https://hyperevmscan.io/address/0xe45e7272da7208c7a137505dfb9491e330bf1a4e
    """

    def get_super_pool_contract(self):
        """Get the SuperPool contract instance."""
        return get_deployed_contract(
            self.web3,
            "sentiment/SuperPool.json",
            self.vault_address,
        )

    def has_custom_fees(self) -> bool:
        """Sentiment has a configurable fee on interest earned."""
        return True

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Sentiment does not have a separate management fee.

        All fees are taken from interest earned (performance-based).
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the fee taken from interest earned.

        The fee is stored as a value out of 1e18, so we divide by 1e18 to get
        the decimal representation.

        :return:
            Fee as a decimal (e.g., 0.1 for 10% fee), or None if fee reading fails.
        """
        try:
            contract = self.get_super_pool_contract()
            fee_raw = contract.functions.fee().call(block_identifier=block_identifier)
            # Fee is denominated out of 1e18, so divide to get decimal
            return fee_raw / 1e18
        except Exception as e:
            logger.warning("Could not read Sentiment SuperPool fee for %s: %s", self.vault_address, e)
            return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Sentiment SuperPools allow instant withdrawals when liquidity is available.

        Withdrawal may be limited if underlying pools have insufficient liquidity.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get a link to the vault on Sentiment app.

        Since Sentiment doesn't have individual vault pages with addresses,
        we link to the main lend page.
        """
        return "https://app.sentiment.xyz/"
