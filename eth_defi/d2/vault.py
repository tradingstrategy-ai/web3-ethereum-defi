"""D2 Finance vault support."""

import datetime
from dataclasses import dataclass
from functools import cached_property
import logging

from web3.contract import Contract
from eth_typing import BlockIdentifier

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.utils import from_unix_timestamp
from eth_defi.vault.base import VaultTechnicalRisk

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class Epoch:
    funding_start: datetime.datetime
    epoch_start: datetime.datetime
    epoch_end: datetime.datetime


class D2Vault(ERC4626Vault):
    """D2 Finance vaults.

    - Most vault logic is offchain, proprietary
    - VaultV1Whitelisted is a wrapper around Hyperliquid trading account
    - You need to hold a minimum amount of USDC (whitelistedAsset) to be able to deposit
    - The vault smart contract does not have visibility to the fees
    - Redemption must happen not during epoch
    - Fees are set and calculated offchain
    - The vaults have funding, trading and withdraw phases and you can only deposit/withdraw on the correct epoch
    - Lockups are up to 30-60 days or so
    - The vault owner can set epochs offhain, up to 10 years

    More information:

    - `Docs <https://gitbook.d2.finance/>`__
    - `HYPE++ strategy blog post <https://medium.com/@D2.Finance/hype-capitalizing-on-hyperliquids-launch-396f8665a2c0>`__

    Whitelist function logic:

    .. code-block:: solidity

            modifier onlyWhitelisted() {
                bool holder = false;
                if (whitelistAsset != address(0)) {
                    holder = IERC20(whitelistAsset).balanceOf(msg.sender) > whitelistBalance;
                }
                require(whitelisted[msg.sender] || holder, "!whitelisted");
                _;
            }

    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment.

        - Example impl https://arbiscan.io/address/0x350856A672e7bF7D7327c8a5e72Ac49833DBfB75#code
        """
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="d2/VaultV1Whitelisted.json",
        )

    def fetch_current_epoch_id(self) -> int:
        return self.vault_contract.functions.getCurrentEpoch().call()

    def fetch_current_epoch_info(self) -> Epoch:
        data = self.vault_contract.functions.getCurrentEpochInfo().call()
        return Epoch(
            funding_start=from_unix_timestamp(data[0]),
            epoch_start=from_unix_timestamp(data[1]),
            epoch_end=from_unix_timestamp(data[2]),
        )

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Non on-chain fee information available.

        - D2 share price is fees-inclusive per them: https://x.com/D2_Finance/status/1988624499588116979
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fees are internalized in the share price.

        - D2 share price is fees-inclusive per them: https://x.com/D2_Finance/status/1988624499588116979
        """
        return 0.20

    def get_estimated_lock_up(self) -> datetime.timedelta:
        epoch = self.fetch_current_epoch_info()
        return epoch.epoch_end - epoch.epoch_start
