"""Yo protocol vault support.

Yo is a DeFi protocol that offers an ERC-4626 vault with asynchronous
redemption mechanism. Users can deposit assets and request redemptions
which are fulfilled by operators, enabling cross-chain asset management.

The YoVault_V2 contract implements:

- Asynchronous redeem mechanism (requestRedeem/fulfillRedeem)
- Configurable deposit and withdrawal fees
- Oracle-based total assets calculation
- Price per share tracking with percentage change limits

Key features:

- Deposit/withdrawal fees configurable by operator
- Asynchronous redemption for cross-chain operations
- No lock-up period for deposits

- Homepage: https://www.yo.xyz/
- Documentation: https://docs.yo.xyz/
- GitHub: https://github.com/yoprotocol/core
- Contract: https://etherscan.io/address/0x0000000f2eb9f69274678c76222b35eec7588a65
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class YoVault(ERC4626Vault):
    """Yo protocol vault support.

    YoVault_V2 is an ERC-4626 vault with asynchronous redemption mechanism.
    The vault allows users to deposit assets and earn yield, with operators
    handling cross-chain asset management.

    Note: YoVault_V2 source was not available on Github, and the development
    seems not to be transparent.

    - Homepage: https://www.yo.xyz/
    - Documentation: https://docs.yo.xyz/
    - GitHub: https://github.com/yoprotocol/core
    - Contract: https://etherscan.io/address/0x0000000f2eb9f69274678c76222b35eec7588a65
    """

    def has_custom_fees(self) -> bool:
        """Whether this vault has deposit/withdrawal fees.

        Yo vault has configurable deposit and withdrawal fees.
        """
        return True

    def get_yo_vault_contract(self):
        """Get the YoVault contract with custom ABI."""
        return get_deployed_contract(
            self.web3,
            "yo/YoVault.json",
            self.vault_address,
        )

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current management fee as a percent.

        Yo vault does not have a separate management fee.
        Fees are charged on deposit and withdrawal.

        :return:
            0.1 = 10%
        """
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee as a percent.

        Yo vault does not have a performance fee.

        :return:
            0.1 = 10%
        """
        return None

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current deposit fee as a percent.

        Reads from the feeOnDeposit storage variable.
        The fee is stored as basis points with DENOMINATOR = 1e18.

        :return:
            0.1 = 10%
        """
        try:
            contract = self.get_yo_vault_contract()
            fee_raw = contract.functions.feeOnDeposit().call(block_identifier=block_identifier)
            # DENOMINATOR is 1e18, so fee is fee_raw / 1e18
            return fee_raw / 1e18
        except Exception as e:
            logger.warning("Failed to read deposit fee for Yo vault %s: %s", self.vault_address, e)
            return None

    def get_withdrawal_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current withdrawal fee as a percent.

        Reads from the feeOnWithdraw storage variable.
        The fee is stored as basis points with DENOMINATOR = 1e18.

        :return:
            0.1 = 10%
        """
        try:
            contract = self.get_yo_vault_contract()
            fee_raw = contract.functions.feeOnWithdraw().call(block_identifier=block_identifier)
            # DENOMINATOR is 1e18, so fee is fee_raw / 1e18
            return fee_raw / 1e18
        except Exception as e:
            logger.warning("Failed to read withdrawal fee for Yo vault %s: %s", self.vault_address, e)
            return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period if any.

        Yo vault uses asynchronous redemption mechanism,
        but there is no guaranteed lock-up period. Redemptions
        are fulfilled by operators.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link.

        :param referral:
            Optional referral code (not used currently).

        :return:
            Link to the Yo protocol homepage.
        """
        return "https://www.yo.xyz/"

    def can_check_deposit(self) -> bool:
        """Yo doesn't support address(0) checks for maxDeposit.

        The contract returns empty data for maxDeposit(address(0)).
        """
        return False
