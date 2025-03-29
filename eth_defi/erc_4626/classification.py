"""ERC-4626 vault type classification.

- Used in vault discovery to figure out what kind of vaults we have autodetected
- Use multicall based apporach to probe contracts
"""
from collections import defaultdict
from collections.abc import Iterable

import eth_abi
from attr import dataclass
from eth_typing import HexAddress
from web3 import Web3
from web3.types import BlockIdentifier

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.event_reader.multicall_batcher import EncodedCall, read_multicall_chunked, EncodedCallResult
from eth_defi.event_reader.web3factory import Web3Factory

#: How many multicall probes per address we make
#:
#: See :py:func:`create_probe_calls`
FEATURE_COUNT = 2


@dataclass(frozen=True, slots=True)
class VaultFeatureProbe:
    """Results of a multicall probing to a vault address."""
    address: HexAddress
    features: set[ERC4626Feature]


def create_probe_calls(
    addresses: Iterable[HexAddress],
    share_probe_amount=1_000_000,
) -> Iterable[EncodedCall]:
    """Create calls that call each vault address using multicall"""

    convert_to_shares_payload = eth_abi.encode(['uint256'], [share_probe_amount])

    # TODO: Might be bit slowish here, but we are not perf intensive
    for address in addresses:

        share_price_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="convertToShares(uint256)")[0:4],
            function="convertToShares",
            data=convert_to_shares_payload,
            extra_data=None,
        )

        # See ipor/vault.py
        ipor_fee_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="getPerformanceFeeData()")[0:4],
            function="getPerformanceFeeData",
            data=b"",
            extra_data=None,
        )

        yield share_price_call
        yield ipor_fee_call


def identify_vault_features(
    calls: dict[str, EncodedCallResult],
) -> set[ERC4626Feature]:
    """Based on multicall results, create the feature flags for the vault."""

    features = set()

    # Not ERC-4626 vaul
    if not calls["convertToShares"].success:
        return {ERC4626Feature.broken}

    if not calls["getPerformanceFeeData"].success:
        features.add(ERC4626Feature.ipor_like)

    return features


def probe_vaults(
    web3factory: Web3Factory,
    addresses: list[HexAddress],
    block_identifier: BlockIdentifier,
    max_workers=8,
    progress_bar_desc: str | None = None,
) -> Iterable[VaultFeatureProbe]:
    """Perform multicalls against each vault addres to extract the features of the vault smart contract.

    - USe multi
    """

    probe_calls = list(create_probe_calls(addresses))

    # Temporary work buffer were we count that all calls to the address have been made,
    # because results are dropping in one by one
    results_per_address: dict[HexAddress, dict] = defaultdict(dict)

    for call_result in read_multicall_chunked(
        web3factory,
        probe_calls,
        block_identifier=block_identifier,
        progress_bar_desc=progress_bar_desc,
        max_workers=max_workers,
    ):
        address = call_result.call.address
        address_calls = results_per_address[address]
        address_calls[call_result.call.func_name] = call_result
        if len(results_per_address[address]) >= FEATURE_COUNT:
            features = identify_vault_features(address_calls)
            yield VaultFeatureProbe(
                address=address,
                features=features,
            )
            del results_per_address[address]



