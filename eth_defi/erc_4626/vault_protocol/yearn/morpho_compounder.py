"""Yearn Morpho Compounder strategy support.

This module provides support for Yearn strategies that invest in Morpho vaults.
Note that this represents an individual Yearn strategy, not an independent vault.
The strategy is a Yearn V3 vault that compounds rewards from underlying Morpho positions.
"""

import datetime
import logging
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3.contract import Contract

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault_protocol.yearn.vault import YearnV3Vault

logger = logging.getLogger(__name__)


class YearnMorphoCompounderStrategy(YearnV3Vault):
    """Yearn Morpho Compounder strategy.

    This represents a Yearn V3 vault that uses MorphoCompounder strategies to invest
    in Morpho vaults and compound rewards. This is an individual Yearn strategy,
    not an independent vault - it uses the Yearn V3 vault infrastructure to manage
    deposits and withdrawals whilst the underlying strategy invests in Morpho.

    The strategy:

    - Deposits assets into Morpho vaults
    - Compounds rewards by claiming them, swapping via Uniswap V3 or auction mechanisms
    - Reinvests proceeds back into the Morpho position

    More information:

    - `Example Yearn Morpho Compounder vault <https://etherscan.io/address/0x6D2981FF9b8d7edbb7604de7A65BAC8694ac849F>`__
    - `Yearn website <https://yearn.fi/v3/1/0x6D2981FF9b8d7edbb7604de7A65BAC8694ac849F>`__
    - `Morpho protocol <https://morpho.org/>`__
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment.

        Uses the standard Yearn V3 vault ABI.
        """
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="yearn/YearnV3Vault.json",
        )

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees.

        Yearn Morpho Compounder strategies do not charge deposit/withdrawal fees.
        Fees are internalised into the share price through strategy profit reporting.
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get the current management fee as a percent.

        Yearn strategies internalise fees into the share price.

        :return:
            0.0 as fees are built into share price
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee as a percent.

        Yearn strategies internalise fees into the share price.

        :return:
            0.0 as fees are built into share price
        """
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta:
        """Get estimated lock-up period.

        No lock-up period for Yearn Morpho Compounder strategies.
        """
        return datetime.timedelta(0)

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link.

        Links to the Yearn V3 vault page.
        """
        return f"https://yearn.fi/v3/{self.chain_id}/{self.vault_address}"
