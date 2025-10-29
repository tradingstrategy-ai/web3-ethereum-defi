"""AUTO Finance Autopool vault support."""

import datetime
from dataclasses import dataclass
from functools import cached_property
import logging

from web3.contract import Contract
from eth_typing import BlockIdentifier, HexAddress

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault

from eth_defi.erc_7540.vault import ERC7540Vault

logger = logging.getLogger(__name__)


class AutoPoolVault(ERC4626Vault):
    """Autopool vault.

    Also known as *Tokemak*, *AUTO Finance*.

    - Fees are taken by minting more shares to the fee recipient, thus diluting all other shareholders.

    More information:

    - `Contract <https://arbiscan.io/address/0xf63b7f49b4f5dc5d0e7e583cfd79dc64e646320c#writeProxyContract>`__
    - `Github <https://github.com/Tokemak/v2-core-pub?tab=readme-ov-file>`__
    - `Implementationn <https://arbiscan.io/address/0x12db19359159e8ab0822506adf15d4d8dbff66c3#code>`__
    - `Fee logic <https://github.com/Tokemak/v2-core-pub/blob/de163d5a1edf99281d7d000783b4dc8ade03591e/src/vault/libs/AutopoolFees.sol#L138>`__
    - `Docs <https://docs.auto.finance/>`__
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="autopool/AutopoolETH.json",
        )

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        return datetime.timedelta(days=0)
