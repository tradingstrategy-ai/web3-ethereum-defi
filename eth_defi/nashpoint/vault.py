"""NashPoint protocol support."""

from functools import cached_property
import logging

from web3.contract import Contract
from eth_typing import BlockIdentifier

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract

from eth_defi.erc_7540.vault import ERC7540Vault

logger = logging.getLogger(__name__)


class NashpointNodeVault(ERC7540Vault):
    """NashPoint vault.

    Also known as *Node* by NashPoint's internal terminology.

    - Fees are taken at the part of the rebalance, at the start of rebalancing after calculating NAV

    More information:

    - `Contract <https://arbiscan.io/address/0x6ca200319a0d4127a7a473d6891b86f34e312f42#code>`__
    - `Github <https://github.com/nashpoint/nashpoint-smart-contracts>`__
    - `Fee logic <https://github.com/nashpoint/nashpoint-smart-contracts/blob/06d948dd3ac126c389d8cf8f9fd53347a73b5059/src/Node.sol#L377>`__
    - `Docs <https://nashpoint.gitbook.io/nashpoint/user-documentation/node-contract-overview>`__
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="nashpoint/Node.json",
        )

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        return 0.0
