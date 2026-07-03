"""USDAi vault support."""

import datetime
import logging
from functools import cached_property

from eth_typing import BlockIdentifier
from web3.contract import Contract
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_7540.vault import ERC7540Vault

logger = logging.getLogger(__name__)

USDAI_REDEMPTION_WINDOW = datetime.timedelta(days=30)


class StakedUSDaiVault(ERC7540Vault):
    """Staked USDai vault.

    Also known as *USD.ai*, *Metastreet Foundation*, *Permian Labs*.


    - ERC-7540 redemption logic
    - Has an admin fee which is internalised in the share price (asset amount)
    - `About admin fees <https://github.com/metastreet-labs/metastreet-usdai-contracts/blob/b5198351b495ee8fa7615a09b5867093753f88e7/src/positionManagers/BasePositionManager.sol#L86>`__

    More information:

    - `Github <https://github.com/metastreet-labs/metastreet-usdai-contracts/blob/main/src/StakedUSDai.sol>`__
    - `Implementationn <https://arbiscan.io/address/0xc0540184de0e42eab2b0a4fc35f4817041001e85#code>`__
    - `RWA dashboard <https://forum.arbitrum.foundation/t/usd-ai-step-application/28791>`__
    - `Redemption implementation <https://github.com/metastreet-labs/metastreet-usdai-contracts/blob/b5198351b495ee8fa7615a09b5867093753f88e7/src/RedemptionLogic.sol#L236>`__
    - `Fee calculations <https://github.com/metastreet-labs/metastreet-usdai-contracts/blob/b5198351b495ee8fa7615a09b5867093753f88e7/src/positionManagers/BasePositionManager.sol#L86>`__
    - `Arbitrum DAO governance post <https://forum.arbitrum.foundation/t/usd-ai-step-application/28791>`__
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="usdai/StakedUSDai.json",
        )

    def has_custom_fees(self) -> bool:  # noqa: PLR6301
        """Deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:  # noqa: PLR6301, ARG002
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:  # noqa: PLR6301, ARG002
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:  # noqa: PLR6301
        """Estimate the USDai redemption waiting time.

        sUSDai moved from a direct ``timelock()`` accessor to queue-based
        redemptions in the 2026 implementation upgrade. Redemption requests
        are assigned to the next 30-day redemption window.

        :return:
            The protocol redemption window.
        """
        return USDAI_REDEMPTION_WINDOW

    def fetch_redemption_next_open(self) -> datetime.datetime | None:
        """Fetch the next USDai redemption processing timestamp.

        The current sUSDai implementation exposes ``redemptionTimestamp()``
        instead of the removed ``timelock()`` accessor.

        :return:
            Naive UTC datetime when the next redemption window opens, or
            ``None`` if the contract cannot be queried.
        """
        try:
            timestamp = self.vault_contract.functions.redemptionTimestamp().call()
        except (ValueError, BadFunctionCallOutput, ContractLogicError):
            return None
        if timestamp == 0:
            return None
        return datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc).replace(tzinfo=None)
