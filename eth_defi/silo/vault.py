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


class SiloVault(ERC4626Vault):
    """Silo Finance.

    The Silo Protocol is a non-custodial lending primitive that creates programmable risk-isolated markets known as silos. Any user with a wallet can lend or borrow in a silo in a non-custodial manner. Silo markets use the peer-to-pool, overcollateralized model, where the value of a borrower's collateral always exceeds the value of their loan.

    Silo is the main component of the protocol. It implements lending logic, manages and isolates risk, acts as a vault for assets, and performs liquidations. Each Silo is composed of the unique asset for which it was created (ie. UNI) and bridge assets (ie. ETH and SiloDollar). There may be multiple bridge assets at any given time.

    TODO: Add fee data collection

    - `Has custom deposit/withdrawal functions <https://devdocs.silo.finance/smart-contracts-overview/core-protocol/silo#deposit>`__
    - `Github <https://github.com/silo-finance/silo-contracts-v2/blob/develop/silo-core/contracts/Silo.sol>`__
    - `Docs <https://devdocs.silo.finance/smart-contracts-overview/core-protocol/silo>`__
    """

    @cached_property
    def name(self) -> str:
        """Truncate protocol repeat in the name."""
        orig_name = super().name
        return orig_name.replace("Silo Finance ", "")

    def get_risk(self) -> VaultTechnicalRisk | None:
        return VaultTechnicalRisk.low

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="silo/Silo.json",
        )

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Fees are taken from AUM.

        https://docs.summer.fi/lazy-summer-protocol/governance/tip-streams
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """TODO: Currently unhandled"""
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta:
        """Buffered withdraws"""
        return datetime.timedelta(days=0)
