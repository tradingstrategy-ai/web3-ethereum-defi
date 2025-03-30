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
from eth_defi.vault.base import VaultBase, VaultSpec


@dataclass(frozen=True, slots=True)
class VaultFeatureProbe:
    """Results of a multicall probing to a vault address."""
    address: HexAddress
    features: set[ERC4626Feature]


def create_probe_calls(
    addresses: Iterable[HexAddress],
    share_probe_amount=1_000_000,
) -> Iterable[EncodedCall]:
    """Create calls that call each vault address using multicall.

    - Because ERC standards are such a shit show, and nobody is using good interface standard,
      we figure out the vault type by probing it with various calls
    """

    convert_to_shares_payload = eth_abi.encode(['uint256'], [share_probe_amount])
    zero_uint_payload = eth_abi.encode(['uint256'], [0])

    # TODO: Might be bit slowish here, but we are not perf intensive
    for address in addresses:

        name_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="name()")[0:4],
            function="name",
            data=b"",
            extra_data=None,
        )

        # Shouldl be present in all ERC-4626 vaults
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

        # https://github.com/harvest-finance/harvest/blob/14420a4444c6aaa7bf0d2303a5888feb812a0521/contracts/Vault.sol#L86C12-L86C26
        harvest_finance_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="vaultFractionToInvestDenominator()")[0:4],
            function="vaultFractionToInvestDenominator",
            data=b"",
            extra_data=None,
        )

        # See standard
        # https://eips.ethereum.org/EIPS/eip-7540#methods
        erc_7545_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="isOperator()")[0:4],
            function="isOperator",
            data=b"",
            extra_data=None,
        )

        # Baso Finance
        # https://defillama.com/protocol/baso-finance
        #
        #     // earned is an estimation, it won't be exact till the supply > rewardPerToken calculations have run
        #     function earned() public view returns (uint) {
        #         if(startTime <= 0 || lastClaimTime > block.timestamp){
        #             return 0;
        #         }
        #         return (block.timestamp - lastClaimTime)*emissionSpeed;
        #     }
        baso_finance_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="isOperator()")[0:4],
            function="isOperator",
            data=b"",
            extra_data=None,
        )

        # BRT2: vAMM
        # https://basescan.org/address/0x49AF8CAf88CFc8394FcF08Cf997f69Cee2105f2b#readProxyContract
        #
        baklava_space = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="outputToLp0Route(uint256)")[0:4],
            function="outputToLp0Route",
            data=zero_uint_payload,
            extra_data=None,
        )

        # https://basescan.org/address/0x2aeB4A62f40257bfC96D5be55519f70DB871c744#readContract
        astrolab_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="agent()")[0:4],
            function="agent",
            data=b"",
            extra_data=None,
        )

        # https://basescan.org/address/0x944766f715b51967E56aFdE5f0Aa76cEaCc9E7f9#readProxyContract
        # https://basescan.org/address/0x2ac590a4a78298093e5bc7742685446af96d56e7#code
        # https://github.com/GainsNetwork/gTrade-v6.1/tree/main
        gains_network_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="depositCap()")[0:4],
            function="depositCap",
            data=b"",
            extra_data=None,
        )

        # Morpho
        # Moonwell runs on Morpho
        # https://basescan.org/address/0x6b13c060F13Af1fdB319F52315BbbF3fb1D88844#readContract
        morpho_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="MORPHO()")[0:4],
            function="MORPHO",
            data=b"",
            extra_data=None,
        )

        # https://docs.fluid.instadapp.io/
        # https://basescan.org/address/0x1943FA26360f038230442525Cf1B9125b5DCB401#code

        yield name_call
        yield share_price_call
        yield ipor_fee_call
        yield harvest_finance_call
        yield erc_7545_call
        yield baso_finance_call
        yield baklava_space
        yield astrolab_call
        yield gains_network_call
        yield morpho_call


def identify_vault_features(
    calls: dict[str, EncodedCallResult],
) -> set[ERC4626Feature]:
    """Based on multicall results, create the feature flags for the vault."""

    features = set()

    if not calls["convertToShares"].success:
        # Not ERC-4626 vault
        features.add(ERC4626Feature.broken)

    if calls["getPerformanceFeeData"].success:
        features.add(ERC4626Feature.ipor_like)

    if calls["vaultFractionToInvestDenominator"].success:
        features.add(ERC4626Feature.harvest_finance)

    if calls["isOperator"].success:
        features.add(ERC4626Feature.erc_7540_like)

    if calls["agent"].success:
        features.add(ERC4626Feature.astrolab_like)

    if calls["depositCap"].success:
        features.add(ERC4626Feature.gains_like)

    if calls["MORPHO"].success:
        features.add(ERC4626Feature.morpho_like)

    # Panoptics do not expose any good calls we could get hold off.
    # For some minor protocols, we do not bother to read their contracts.
    name = calls["name"].result
    if name:
        try:
            name = name.decode("utf-8")
            if "POPT-V1" in name:
                features.add(ERC4626Feature.panoptic_like)
            elif "Return Finance" in name:
                features.add(ERC4626Feature.panoptic_like)
            elif "ArcadiaV2" in name:
                features.add(ERC4626Feature.arcadia_finance_like)
            elif "BRT2" in name:
                features.add(ERC4626Feature.baklava_space_like)
            elif name == "Satoshi":
                features.add(ERC4626Feature.satoshi_stablecoin)
            elif "Athena" in name:
                features.add(ERC4626Feature.athena_like)
            elif "RightsToken" in name:
                features.add(ERC4626Feature.reserve_like)
            elif "Fluid" in name:
                features.add(ERC4626Feature.fluid_like)

        except:
            pass

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

    for address, address_call_results in results_per_address.items():
        features = identify_vault_features(address_call_results)
        yield VaultFeatureProbe(
            address=address,
            features=features,
        )


def create_vault_instance(
    web3: Web3,
    address: HexAddress,
    features: set[ERC4626Feature],
) -> VaultBase | None:
    """Create a new vault instance class based on the features.

    :return:
        None if the vault creation is not supported
    """

    spec = VaultSpec(web3.eth.chain_id, address)

    if ERC4626Feature.broken in features:
        return None
    elif ERC4626Feature.ipor_like in features:
        # IPOR instance
        from eth_defi.ipor.vault import IPORVault
        return IPORVault(web3, spec)
    else:
        # Generic ERC-4626 without fee data
        from eth_defi.erc_4626.vault import ERC4626Vault
        return ERC4626Vault(web3, spec)
