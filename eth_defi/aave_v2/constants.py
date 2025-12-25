"""Aave v2 constants."""

from typing import NamedTuple

from eth_defi.aave_v3.constants import (  # noqa: passthrough imports, don't remove
    MAX_AMOUNT,
    AaveVersion,
)


class AaveV2Network(NamedTuple):
    # Network name
    name: str

    # Aave v2 lending pool address
    pool_address: str

    # Aave v2 lending pool configurator address
    pool_configurator_address: str

    # Block number when the pool was created
    pool_created_at_block: int


# https://docs.aave.com/developers/v/2.0/deployed-contracts/deployed-contracts
AAVE_V2_NETWORK_CHAINS: dict[int, str] = {
    1: "ethereum",
    137: "polygon",
    43114: "avalanche",
}

AAVE_V2_NETWORKS: dict[str, AaveV2Network] = {
    # Ethereum Mainnet
    "ethereum": AaveV2Network(
        name="Ethereum",
        pool_address="0x7d2768de32b0b80b7a3454c06bdac94a69ddc7a9",
        pool_configurator_address="0x311Bb771e4F8952E6Da169b425E7e92d6Ac45756",
        # https://etherscan.io/tx/0x7d77cc7523a491fa670bfefa0a386ab036b6511d6d9fa6c2cf5c07b349dc9d3a
        pool_created_at_block=11362579,
    ),
    # Polygon Mainnet
    "polygon": AaveV2Network(
        name="Polygon",
        pool_address="0x8dFf5E27EA6b7AC08EbFdf9eB090F32ee9a30fcf",
        pool_configurator_address="0x26db2b833021583566323e3b8985999981b9f1f3",
        # https://polygonscan.com/tx/0xb5a63fed49e97a58135b012fa14d83e680a0f3cd3aefeb551228d6e3640dbec9
        pool_created_at_block=12687245,
    ),
    # Avalanche C-Chain
    "avalanche": AaveV2Network(
        name="Avalanche",
        pool_address="0x4F01AeD16D97E3aB5ab2B501154DC9bb0F1A5A2C",
        pool_configurator_address="0x230B618aD4C475393A7239aE03630042281BD86e",
        # https://snowtrace.io/tx/0x5db8b8c3026d4a433ca67cbc120540ab6f8897b3aff37e78ba014ac505d167bc?chainId=43114
        pool_created_at_block=4607005,
    ),
}


def get_aave_v2_network_by_chain_id(chain_id: int) -> AaveV2Network:
    if chain_id not in AAVE_V2_NETWORK_CHAINS:
        raise ValueError(f"Unsupported chain id: {chain_id}")
    network_slug = AAVE_V2_NETWORK_CHAINS[chain_id]
    return AAVE_V2_NETWORKS[network_slug]
