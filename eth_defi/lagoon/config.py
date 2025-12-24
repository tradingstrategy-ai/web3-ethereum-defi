"""Lagoon configurations across chains.

- How we need to handle Lagoon deployment on each chain
"""

from dataclasses import dataclass

from eth_typing import HexAddress

from eth_defi.token import USDC_NATIVE_TOKEN, USDT_NATIVE_TOKEN


@dataclass(slots=True, frozen=True)
class LagoonChainConfig:
    #: The default denomination token
    underlying: HexAddress
    #: Use BeaconProxyFactory to deploy
    factory_contract: bool
    #: Do we need to deploy the whole protocol or just the existing protocol
    from_the_scratch: bool


def get_lagoon_chain_config(chain_id: int) -> LagoonChainConfig:
    if chain_id == 56:
        # Binance uses USDT,
        # also it does not have official Lagoon factory as the writing of this.
        underlying = USDT_NATIVE_TOKEN[chain_id]
        from_the_scratch = True
        factory_contract = True
    elif chain_id == 421614:
        underlying = USDC_NATIVE_TOKEN[chain_id]
        factory_contract = True
        from_the_scratch = True
    else:
        underlying = USDC_NATIVE_TOKEN[chain_id]
        factory_contract = True
        from_the_scratch = False

    return LagoonChainConfig(
        underlying=underlying,
        factory_contract=factory_contract,
        from_the_scratch=from_the_scratch,
    )
