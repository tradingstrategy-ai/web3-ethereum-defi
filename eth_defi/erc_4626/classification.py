"""ERC-4626 vault type classification.

- Used in vault discovery to figure out what kind of vaults we have autodetected
- Use multicall based apporach to probe contracts
"""
from collections.abc import Iterable

import eth_abi
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.event_reader.multicall_batcher import EncodedCall


def create_probe_calls(addresses: Iterable[HexAddress]) -> Iterable[EncodedCall]:
    """Create calls that call each vault address using multicall"""

    convert_to_shares_payload = eth_abi.encode("['uint256'], [12345]")

    for address in addresses:

        share_price_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak("convertToShares(uint256)")[0:4],
            function="convertToShares",
            data=convert_to_shares_payload,
        )

        ipor_fee_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="getPerformanceFeeData()")[0:4],
            function="getPerformanceFeeData",
            data=b"",
        )

        yield [
            share_price_call,
            ipor_fee_call,
        ]


def probe_vaults(
    web3: Web3,
    list[HexAddress],
) -> dict[HexAddress, set[ERC4626Feature]]:
    """Perform multicalls against each vault addres to extract the features of the vault smart contract"""
    pass