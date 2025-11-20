"""Summer finance vault support."""

import datetime

from functools import cached_property
import logging

from web3.contract import Contract

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.vault.base import VaultTechnicalRisk


logger = logging.getLogger(__name__)


class SummerVault(ERC4626Vault):
    """Summer Earn protocol.

    So-called "Lazy vault", also called "Fleets".

    - Ex Oasis.app, or OasisDEX

    - `About Lazy vaults <https://docs.summer.fi/lazy-summer-protocol/lazy-summer-protocol>`__
    - `Docs <https://docs.summer.fi/>`__
    - `Github repo <https://github.com/OasisDEX/summer-earn-protocol>`__
    - `Github contract source code <https://github.com/OasisDEX/summer-earn-protocol/blob/8a0eaa6e0ff420f4e574042855514590e0cc610e/packages/core-contracts/src/contracts/FleetCommander.sol>`__
    - `Share minting fee process <https://docs.summer.fi/lazy-summer-protocol/governance/tip-streams>`__
    """

    @cached_property
    def name(self) -> str:
        """Get vault name."""
        return f"Summer.fi {self.denomination_token.symbol}"

    def get_risk(self) -> VaultTechnicalRisk | None:
        return VaultTechnicalRisk.low

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="summer/FleetCommander.json",
        )

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Fees are taken from AUM.

        https://docs.summer.fi/lazy-summer-protocol/governance/tip-streams
        """
        return self.fetch_tip_rate(block_identifier)

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """No performance fee."""
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta:
        """Buffered withdraws?"""
        return datetime.timedelta(days=0)

    def fetch_tip_rate(self, block_identifier: BlockIdentifier) -> float:
        return self.vault_contract.functions.tipRate().call(block_identifier=block_identifier) / 10**20
