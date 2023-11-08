"""1delta deployments."""

from dataclasses import dataclass

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.aave_v3.deployment import AaveV3Deployment
from eth_defi.abi import get_deployed_contract


@dataclass(frozen=True)
class OneDeltaDeployment:
    """Describe 1delta deployment.

    This contains all smart contracts needed to interact with 1delta procotol.

    See :py:func:`fetch_deployment`.
    """

    #: The Web3 instance for which all the contracts here are bound
    web3: Web3

    #: FlashAggregator contract proxy
    flash_aggregator: Contract

    # DeltaBrokerProxy contract proxy
    broker_proxy: Contract


def fetch_deployment(
    web3: Web3,
    flash_aggregator_address: HexAddress | str,
    broker_proxy_address: HexAddress | str,
) -> OneDeltaDeployment:
    """Construct 1delta deployment based on on-chain data.

    - We need associated Aave instance to be able to construct transactions
      to open and close positions

    Polygon forked mainnet example:

    .. code-block:: python

        @pytest.fixture
        def aave_v3_deployment(web3):
            return fetch_aave_deployment(
                web3,
                pool_address="0x794a61358D6845594F94dc1DB02A252b5b4814aD",
                data_provider_address="0x69FA688f1Dc47d4B5d8029D5a35FB7a548310654",
                oracle_address="0xb023e699F5a33916Ea823A16485e259257cA8Bd1",
            )


        @pytest.fixture
        def one_delta_deployment(web3, aave_v3_deployment) -> OneDeltaDeployment:
            return fetch_1delta_deployment(
                web3,
                aave_v3_deployment,
                # flash_aggregator_address="0x168B4C2Cc2df4635D521Aa1F8961DD7218f0f427",
                flash_aggregator_address="0x74E95F3Ec71372756a01eB9317864e3fdde1AC53",
                broker_proxy_address="0x74E95F3Ec71372756a01eB9317864e3fdde1AC53",
            )

    :return:
        Data class representing 1delta deployment
    """
    flash_aggregator = get_deployed_contract(
        web3,
        "1delta/FlashAggregator.json",
        flash_aggregator_address,
    )
    broker_proxy = get_deployed_contract(
        web3,
        "1delta/DeltaBrokerProxy.json",
        broker_proxy_address,
    )

    return OneDeltaDeployment(
        web3=web3,
        flash_aggregator=flash_aggregator,
        broker_proxy=broker_proxy,
    )
