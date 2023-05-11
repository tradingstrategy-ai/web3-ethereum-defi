"""Aave v3 deployments."""
from dataclasses import dataclass
from typing import NamedTuple

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract


class AaveV3ReserveConfiguration(NamedTuple):
    # https://github.com/aave/aave-v3-core/blob/e0bfed13240adeb7f05cb6cbe5e7ce78657f0621/contracts/misc/AaveProtocolDataProvider.sol#L77

    decimals: int
    ltv: int
    liquidation_threshold: int
    liquidation_bonus: int
    reserve_factor: int
    usage_as_collateral_enabled: bool
    borrowing_enabled: bool
    stable_borrow_rate_enabled: bool
    is_active: bool
    is_frozen: bool


@dataclass(frozen=True)
class AaveV3Deployment:
    """Describe Aave v3 deployment."""

    #: The Web3 instance for which all the contracts here are bound
    web3: Web3

    #: Aave v3 pool contract proxy
    pool: Contract

    #: AaveProtocolDataProvider contract
    data_provider: Contract

    #: AaveOracle contract
    oracle: Contract

    def get_configuration_data(self, token_address: HexAddress):
        # https://github.com/aave/aave-v3-core/blob/e0bfed13240adeb7f05cb6cbe5e7ce78657f0621/contracts/misc/AaveProtocolDataProvider.sol#L77
        data = self.data_provider.functions.getReserveConfigurationData(token_address).call()
        return AaveV3ReserveConfiguration(*data)

    def get_price(self, token_address: HexAddress):
        # https://github.com/aave/aave-v3-core/blob/e0bfed13240adeb7f05cb6cbe5e7ce78657f0621/contracts/misc/AaveOracle.sol#L104
        return self.oracle.functions.getAssetPrice(token_address).call()
