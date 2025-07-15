"""ERC-4626 vault type classification.

- Used in vault discovery to figure out what kind of vaults we have autodetected
- Use multicall based apporach to probe contracts
"""

import logging
from collections import defaultdict
from collections.abc import Iterable

import eth_abi
from attr import dataclass
from eth_typing import HexAddress
from web3 import Web3
from web3.types import BlockIdentifier

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult, read_multicall_chunked
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.vault.base import VaultBase, VaultSpec

logger = logging.getLogger(__name__)


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

    convert_to_shares_payload = eth_abi.encode(["uint256"], [share_probe_amount])
    zero_uint_payload = eth_abi.encode(["uint256"], [0])
    double_address = eth_abi.encode(["address", "address"], [ZERO_ADDRESS_STR, ZERO_ADDRESS_STR])

    # TODO: Might be bit slowish here, but we are not perf intensive
    for address in addresses:
        bad_probe_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="EVM IS BROKEN SHIT()")[0:4],
            function="EVM IS BROKEN SHIT",
            data=b"",
            extra_data=None,
        )

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

        # function isOperator(address controller, address operator) external returns (bool);
        erc_7540_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="isOperator(address,address)")[0:4],
            function="isOperator",
            data=double_address,
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

        # interface IERC7575 is IERC4626 {
        #     function share() external view returns (address);
        # }
        erc_7575_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="share()")[0:4],
            function="share",
            data=b"",
            extra_data=None,
        )

        # Kiln metavault
        # https://basescan.org/address/0x4b2A4368544E276780342750D6678dC30368EF35#readProxyContract
        # additionalRewardsStrategy
        # https://github.com/0xZunia/Kiln.MetaVault
        kiln_metavaut_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="additionalRewardsStrategy()")[0:4],
            function="additionalRewardsStrategy",
            data=b"",
            extra_data=None,
        )

        # MAX_MANAGEMENT_RATE
        # https://basescan.org/address/0x6a5ea384e394083149ce39db29d5787a658aa98a#readContract
        lagoon_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="MAX_MANAGEMENT_RATE()")[0:4],
            function="MAX_MANAGEMENT_RATE",
            data=b"",
            extra_data=None,
        )

        # GOV()
        # https://etherscan.io/address/0x4cE9c93513DfF543Bc392870d57dF8C04e89Ba0a#readContract
        yearn_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="GOV()")[0:4],
            function="GOV",
            data=b"",
            extra_data=None,
        )

        # Written in Vyper
        # isShutdown()
        # https://polygonscan.com/address/0xa013fbd4b711f9ded6fb09c1c0d358e2fbc2eaa0#readContract
        yearn_v3_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="get_default_queue()")[0:4],
            function="get_default_queue",
            data=b"",
            extra_data=None,
        )

        # https://basescan.org/address/0x84d7549557f0fb69efbd1229d8e2f350b483c09b#readContract
        superform_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="THIS_CHAIN_ID()")[0:4],
            function="THIS_CHAIN_ID",
            data=b"",
            extra_data=None,
        )

        # https://etherscan.io//address/0x862c57d48becB45583AEbA3f489696D22466Ca1b#readProxyContract
        superform_call_2 = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="METADEPOSIT_TYPEHASH()")[0:4],
            function="METADEPOSIT_TYPEHASH",
            data=b"",
            extra_data=None,
        )
        # profitMaxUnlockTime()
        # https://etherscan.io/address/0xa10c40f9e318b0ed67ecc3499d702d8db9437228#readProxyContract
        term_finance_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="repoTokenHoldings()")[0:4],
            function="repoTokenHoldings",
            data=b"",
            extra_data=None,
        )

        #
        # https://basescan.org/address/0x30a9a9654804f1e5b3291a86e83eded7cf281618#code
        euler_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="MODULE_VAULT()")[0:4],
            function="MODULE_VAULT",
            data=b"",
            extra_data=None,
        )

        yield bad_probe_call
        yield name_call
        yield share_price_call
        yield ipor_fee_call
        yield harvest_finance_call
        yield erc_7540_call
        yield baklava_space
        yield astrolab_call
        yield gains_network_call
        yield morpho_call
        yield erc_7575_call
        yield kiln_metavaut_call
        yield lagoon_call
        yield yearn_call
        yield yearn_v3_call
        yield superform_call
        yield superform_call_2
        yield term_finance_call
        yield euler_call


def identify_vault_features(
    calls: dict[str, EncodedCallResult],
    debug_text: str | None,
) -> set[ERC4626Feature]:
    """Based on multicall results, create the feature flags for the vault..

    :param calls:
        Call name -> result
    """

    features = set()

    # Example probe list
    #
    # EVM IS BROKEN SHIT False
    # name True
    # convertToShares True
    # getPerformanceFeeData False
    # vaultFractionToInvestDenominator False
    # isOperator False
    # outputToLp0Route False
    # agent False
    # depositCap False
    # MORPHO False
    # share False
    # additionalRewardsStrategy False
    # MAX_MANAGEMENT_RATE False
    # GOV False
    # isShutdown False
    # THIS_CHAIN_ID False
    # METADEPOSIT_TYPEHASH False
    # profitMaxUnlockTime True
    # MODULE_VAULT False

    # Should return uint256 share count. Broken proxies may return 0x or similar response.
    if not calls["convertToShares"].success and len(calls["convertToShares"].result) != 32:
        # Not ERC-4626 vault
        return {ERC4626Feature.broken}

    if calls["EVM IS BROKEN SHIT"].success:
        return {ERC4626Feature.broken}

    if calls["getPerformanceFeeData"].success and len(calls["getPerformanceFeeData"].result) == 64:
        # File 21 of 47 : PlasmaVaultStorageLib.sol
        #     /// @custom:storage-location erc7201:io.ipor.PlasmaVaultPerformanceFeeData
        #     struct PerformanceFeeData {
        #         address feeManager;
        #         uint16 feeInPercentage;
        #     }
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

    if calls["share"].success:
        features.add(ERC4626Feature.erc_7575_like)

    if calls["additionalRewardsStrategy"].success:
        features.add(ERC4626Feature.kiln_metavault_like)

    if calls["MAX_MANAGEMENT_RATE"].success:
        features.add(ERC4626Feature.lagoon_like)
        # All Lagoon should be ERC-7575
        assert ERC4626Feature.erc_7540_like in features, f"Lagoon vault did not pass ERC-7540 check: {debug_text}"

    if calls["GOV"].success:
        features.add(ERC4626Feature.yearn_compounder_like)

    if calls["get_default_queue"].success:
        features.add(ERC4626Feature.yearn_v3_like)

    if calls["THIS_CHAIN_ID"].success or calls["METADEPOSIT_TYPEHASH"].success:
        features.add(ERC4626Feature.superform_like)

    if calls["repoTokenHoldings"].success:
        features.add(ERC4626Feature.term_finance_like)

    if calls["MODULE_VAULT"].success:
        features.add(ERC4626Feature.euler_like)

    if len(features) > 4:
        # This contract somehow responses to all calls with success.
        # It is probably some sort of a broken proxy?
        # WARNING:eth_defi.erc_4626.scan:Could not read IPORVault 0xaa3868461c0d3B26F71ee177aF4242E3A3974DC2 ({<ERC4626Feature.gains_like: 'gains_like'>, <ERC4626Feature.astrolab_like: 'astrolab_like'>, <ERC4626Feature.morpho_like: 'morpho_like'>, <ERC4626Feature.erc_7540_like: 'erc_7540_like'>, <ERC4626Feature.kiln_metavault_like: 'kiln_metavault_like'>, <ERC4626Feature.erc_7575_like: 'erc_7575_like'>, <ERC4626Feature.harvest_finance: 'harvest_finance'>, <ERC4626Feature.lagoon_like: 'lagoon_like'>, <ERC4626Feature.ipor_like: 'ipor_like'>}): Node lacked state data when doing eth_call for block 0x1525af9
        return {ERC4626Feature.broken}

    # Panoptics do not expose any good calls we could get hold off.
    # For some minor protocols, we do not bother to read their contracts.
    name = calls["name"].result
    if name:
        name = name.decode("utf-8", errors="ignore")
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
        elif "Peapods" in name:
            features.add(ERC4626Feature.peapods_like)

    return features


def probe_vaults(
    chain_id: int,
    web3factory: Web3Factory,
    addresses: list[HexAddress],
    block_identifier: BlockIdentifier,
    max_workers=8,
    progress_bar_desc: str | None = None,
) -> Iterable[VaultFeatureProbe]:
    """Perform multicalls against each vault address to extract the features of the vault smart contract.

    :return:
        Iterator of what vault smart contract features we detected for each potential vault address
    """

    assert type(chain_id) == int

    probe_calls = list(create_probe_calls(addresses))

    # Temporary work buffer were we count that all calls to the address have been made,
    # because results are dropping in one by one
    results_per_address: dict[HexAddress, dict] = defaultdict(dict)

    for call_result in read_multicall_chunked(
        chain_id,
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
        features = identify_vault_features(address_call_results, debug_text=f"vault: {address}")
        yield VaultFeatureProbe(
            address=address,
            features=features,
        )


def detect_vault_features(
    web3: Web3,
    address: HexAddress | str,
    verbose=True,
) -> set[ERC4626Feature]:
    """Detect the ERC-4626 features of a vault smart contract.

    - Protocols: Harvest, Lagoon, etc.
    - Does support ERC-7540
    - Very slow, only use in scripts and tutorials.
    - Use to pass to :py:func:`create_vault_instance` to get a correct Python proxy class for the vault institated.

    Example:

    .. code-block:: python

        features = detect_vault_features(web3, spec.vault_address, verbose=False)
        logger.info("Detected vault features: %s", features)

        vault = create_vault_instance(
            web3,
            spec.vault_address,
            features=features,
        )

    :param verbose:
        Disable for command line scripts
    """
    address = Web3.to_checksum_address(address)
    logger.info("Detecting vault features for %s", address)
    probe_calls = list(create_probe_calls([address]))
    block_number = web3.eth.block_number

    results = {}
    for call in probe_calls:
        result = call.call_as_result(
            web3,
            block_identifier=block_number,
            ignore_error=True,
        )
        if verbose:
            logger.info("Result for %s: %s, error: %s", call.func_name, result.success, str(result.revert_exception))
        results[call.func_name] = result

    features = identify_vault_features(results, debug_text=f"vault: {address}")
    return features


def create_vault_instance(
    web3: Web3,
    address: HexAddress,
    features: set[ERC4626Feature] | None = None,
    token_cache: dict | None = None,
    auto_detect: bool = False,
) -> VaultBase | None:
    """Create a new vault instance class based on the detected features.

    - Get a protocol-specific Python instance that can e.g. read the fees of the vault (not standardised).

    See also
    - :py:func:`detect_vault_features` to determine features for a vault address

    :param features:
        Previously/manually extracted vault feature flags for the type.

        Give empty set for generic ERC-4626 vault class.

    :param auto_detect:
        Auto-detect the vault protocol.

        Very slow, do not use except in tutorials and scripts.
        Prefer to manually pass ``feature``.

    :return:
        None if the vault creation is not supported
    """

    if not features:
        # If no features are given, we assume it is a generic ERC-4626 vault
        features = {}

    if auto_detect:
        assert not features, "Do not pass features when auto-detecting vault type"

    spec = VaultSpec(web3.eth.chain_id, address.lower())

    if ERC4626Feature.broken in features:
        return None
    elif ERC4626Feature.ipor_like in features:
        # IPOR instance
        from eth_defi.ipor.vault import IPORVault

        return IPORVault(web3, spec, token_cache=token_cache)
    elif ERC4626Feature.lagoon_like in features:
        # Lagoon instance
        from eth_defi.lagoon.vault import LagoonVault

        return LagoonVault(web3, spec, token_cache=token_cache)
    elif ERC4626Feature.morpho_like in features:
        # Lagoon instance
        from eth_defi.morpho.vault import MorphoVault

        return MorphoVault(web3, spec, token_cache=token_cache)
    else:
        # Generic ERC-4626 without fee data
        from eth_defi.erc_4626.vault import ERC4626Vault

        return ERC4626Vault(web3, spec, token_cache=token_cache)
