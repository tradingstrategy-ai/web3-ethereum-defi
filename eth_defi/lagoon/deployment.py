import logging
from dataclasses import dataclass
from typing import TypedDict

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.token import get_wrapped_native_token_address

logger = logging.getLogger(__name__)

DEFAULT_RATE_UPDATE_COOLDOWN = 86400

DEFAULT_MANAGEMENT_RATE = 200

DEFAULT_PERFORMANCE_RATE = 2000

@dataclass(slots=True)
class LagoonDeploymentParameters:
    underlying: HexAddress
    name: str
    symbol: str
    safe: str
    whitelistManager: str
    valuationManager: str
    admin: str
    feeReceiver: str
    feeRegistry: str

    #: Management fee in BPS
    managementRate: int = DEFAULT_MANAGEMENT_RATE  # Assuming these are integers, adjust type if different

    #: Performance fee in BPS
    performanceRate: int = DEFAULT_PERFORMANCE_RATE
    enableWhitelist: bool = False

    #: Max rate update frequency, seconds
    rateUpdateCooldown: int = DEFAULT_RATE_UPDATE_COOLDOWN

    #: If set None, then autoresolve
    wrappedNativeToken: HexAddress | None = None



def deploy_lagoon_with_guard(
    web3: Web3,
    deployer: HexAddress,
    owner: HexAddress,
    parameters: LagoonDeploymentParameters,
):
    """Deploy a new Lagoon vault with a guard.

    - Create a new Safe

    - Create a new Lagoon vault

    - Set guard policies

    - Set owership

    For Foundry recipe see https://github.com/hopperlabsxyz/lagoon-v0/blob/main/script/deploy_vault.s.sol
    """

    chain_id = web3.eth.chain_id

    logger.info(
        "Deploying Lagoon vault on chain %d, deployer is %s",
        chain_id,
        deployer,
    )

    # Autoresolve
    if parameters.wrappedNativeToken is None:
        parameters.wrappedNativeToken = get_wrapped_native_token_address(chain_id)


