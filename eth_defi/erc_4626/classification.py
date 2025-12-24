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
from eth_defi.vault.risk import BROKEN_VAULT_CONTRACTS

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
        gains_tranche_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="depositCap()")[0:4],
            function="depositCap",
            data=b"",
            extra_data=None,
        )

        # gToken like vaults
        # https://github.com/0xOstium/smart-contracts-public/blob/da3b944623bef814285b7f418d43e6a95f4ad4b1/src/OstiumVault.sol#L243
        gains_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="maxDiscountP()")[0:4],
            function="maxDiscountP",
            data=b"",
            extra_data=None,
        )

        # OstiumVault detector on the top of Gains
        # https://github.com/0xOstium/smart-contracts-public/blob/da3b944623bef814285b7f418d43e6a95f4ad4b1/src/OstiumVault.sol
        registry_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="registry()")[0:4],
            function="registry",
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

        # TODO: No way separate from Goat Protocol, see test_superform
        # Superform
        # https://github.com/TrueFi-Protocol
        # https://app.superform.xyz/
        # https://arbiscan.io/address/0xa7781f1d982eb9000bc1733e29ff5ba2824cdbe5#code
        # superform_call_3 = EncodedCall.from_keccak_signature(
        #     address=address,
        #     signature=Web3.keccak(text="PROFIT_UNLOCK_TIME()")[0:4],
        #     function="PROFIT_UNLOCK_TIME",
        #     data=b"",
        #     extra_data=None,
        # )

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

        # https://arbiscan.io/address/0x5f851f67d24419982ecd7b7765defd64fbb50a97#readContract
        umami_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="aggregateVault()")[0:4],
            function="aggregateVault",
            data=b"",
            extra_data=None,
        )

        plutus_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="SAY_TRADER_ROLE()")[0:4],
            function="SAY_TRADER_ROLE",
            data=b"",
            extra_data=None,
        )

        # https://arbiscan.io/address/0x75288264fdfea8ce68e6d852696ab1ce2f3e5004#code
        d2_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="getCurrentEpochInfo()")[0:4],
            function="getCurrentEpochInfo",
            data=b"",
            extra_data=None,
        )

        # Untangled finance
        # https://app.untangled.finance/
        # https://arbiscan.io/address/0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9#code
        untangled_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="claimableKeeper()")[0:4],
            function="claimableKeeper",
            data=b"",
            extra_data=None,
        )

        # https://arbiscan.io/address/0xb739ae19620f7ecb4fb84727f205453aa5bc1ad2#code
        # Fluid conflicting https://etherscan.io/address/0x00c8a649c9837523ebb406ceb17a6378ab5c74cf#readContract
        trade_factory_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="tradeFactory()")[0:4],
            function="tradeFactory",
            data=b"",
            extra_data=None,
        )

        # Goat protocol
        # https://github.com/goatfi/contracts
        # https://arbiscan.io/address/0x8a1ef3066553275829d1c0f64ee8d5871d5ce9d3#readContract
        # https://github.com/goatfi/contracts/blob/main/src/infra/multistrategy/Multistrategy.sol
        goat_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="DEGRADATION_COEFFICIENT()")[0:4],
            function="DEGRADATION_COEFFICIENT",
            data=b"",
            extra_data=None,
        )

        # USDai
        # https://arbiscan.io/address/0xc0540184de0e42eab2b0a4fc35f4817041001e85#code
        usdai_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="bridgedSupply()")[0:4],
            function="bridgedSupply",
            data=b"",
            extra_data=None,
        )

        # Autopool
        # https://arbiscan.io/address/0xf63b7f49b4f5dc5d0e7e583cfd79dc64e646320c#readProxyContract
        # https://github.com/Tokemak/v2-core-pub?tab=readme-ov-file
        autopool_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="autoPoolStrategy()")[0:4],
            function="autoPoolStrategy",
            data=b"",
            extra_data=None,
        )

        # NashPoint
        # https://arbiscan.io/address/0x6ca200319a0d4127a7a473d6891b86f34e312f42#readContract
        nashpoint_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="validateComponentRatios()")[0:4],
            function="validateComponentRatios",
            data=b"",
            extra_data=None,
        )

        # LLAMMA
        # https://arbiscan.io/address/0xe296ee7f83d1d95b3f7827ff1d08fe1e4cf09d8d#code
        llamma_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="borrowed_token()")[0:4],
            function="borrowed_token",
            data=b"",
            extra_data=None,
        )

        # Summer Earn
        # https://arbiscan.io/address/0xe296ee7f83d1d95b3f7827ff1d08fe1e4cf09d8d#code
        summer_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="ADMIRALS_QUARTERS_ROLE()")[0:4],
            function="ADMIRALS_QUARTERS_ROLE",
            data=b"",
            extra_data=None,
        )

        # Silo Finance
        # https://arbiscan.io/address/0xacb7432a4bb15402ce2afe0a7c9d5b738604f6f9#readContract
        silo_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="utilizationData()")[0:4],
            function="utilizationData",
            data=b"",
            extra_data=None,
        )

        # TrueFi
        # https://github.com/TrueFi-Protocol
        # https://arbiscan.io/address/0x8626a4234721A605Fc84Bb49d55194869Ae95D98#readContract
        truefi_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="depositController()")[0:4],
            function="depositController",
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
        yield gains_call
        yield gains_tranche_call
        yield morpho_call
        yield erc_7575_call
        yield kiln_metavaut_call
        yield lagoon_call
        yield yearn_call
        yield yearn_v3_call
        yield superform_call
        yield superform_call_2
        # yield superform_call_3
        yield term_finance_call
        yield euler_call
        yield registry_call
        yield umami_call
        yield plutus_call
        yield d2_call
        yield untangled_call
        yield trade_factory_call
        yield goat_call
        yield usdai_call
        yield autopool_call
        yield nashpoint_call
        yield llamma_call
        yield summer_call
        yield silo_call
        yield truefi_call


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

    # If a call to an function which cannot exist succeeds, the contract is broken
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
        features.add(ERC4626Feature.gains_tranche_like)

    if calls["maxDiscountP"].success:
        if calls["registry"].success:
            features.add(ERC4626Feature.ostium_like)
        else:
            features.add(ERC4626Feature.gains_like)

    if calls["MORPHO"].success:
        features.add(ERC4626Feature.morpho_like)

    # Triggered by USDai
    # TODO: Any better ways to check this?
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

    # https://arbiscan.io/address/0x5f851f67d24419982ecd7b7765defd64fbb50a97#readContract
    if calls["aggregateVault"].success:
        features.add(ERC4626Feature.umami_like)

    # https://arbiscan.io/address/0x0f49730bc6ba3a3024d32131c1da7168d226e737#code
    if calls["SAY_TRADER_ROLE"].success:
        features.add(ERC4626Feature.plutus_like)

    if calls["getCurrentEpochInfo"].success:
        features.add(ERC4626Feature.d2_like)

    if calls["claimableKeeper"].success:
        features.add(ERC4626Feature.untangled_like)
        features.add(ERC4626Feature.erc_7540_like)

    if calls["tradeFactory"].success:
        features.add(ERC4626Feature.yearn_tokenised_strategy)

    if calls["DEGRADATION_COEFFICIENT"].success:
        features.add(ERC4626Feature.goat_like)

    if calls["bridgedSupply"].success:
        features.add(ERC4626Feature.usdai_like)
        features.add(ERC4626Feature.erc_7540_like)
        features.add(ERC4626Feature.erc_7575_like)

    if calls["autoPoolStrategy"].success:
        features.add(ERC4626Feature.autopool_like)

    if calls["validateComponentRatios"].success:
        features.add(ERC4626Feature.nashpoint_like)

    if calls["borrowed_token"].success:
        features.add(ERC4626Feature.llamma_like)

    if calls["ADMIRALS_QUARTERS_ROLE"].success:
        features.add(ERC4626Feature.summer_like)

    if calls["utilizationData"].success:
        features.add(ERC4626Feature.silo_like)

    if calls["depositController"].success:
        features.add(ERC4626Feature.truefi_like)

    # # TODO: No way separate from Goat Protocol, see test_superform
    # if calls["PROFIT_UNLOCK_TIME"].success:
    #    features.add(ERC4626Feature.superform_like)

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
        elif "Savings GYD" in name:
            features.add(ERC4626Feature.gyroscope)

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

    assert address.lower() not in BROKEN_VAULT_CONTRACTS, f"Vault {address} is known broken vault contract like, avoid"

    address = Web3.to_checksum_address(address)
    logger.info("Detecting vault features for %s", address)
    probe_calls = list(create_probe_calls([address]))
    block_number = web3.eth.block_number

    results = {}
    for call in probe_calls:
        result = call.call_as_result(
            web3,
            block_identifier=block_number,
            ignore_error=False,
        )
        if verbose:
            logger.info("Result for %s: %s, error: %s", call.func_name, result.success, str(result.revert_exception))
        results[call.func_name] = result

    features = identify_vault_features(results, debug_text=f"vault: {address}")
    return features


def create_vault_instance(
    web3: Web3,
    address: HexAddress | str,
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

        return IPORVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.lagoon_like in features:
        # Lagoon instance
        from eth_defi.lagoon.vault import LagoonVault

        return LagoonVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.morpho_like in features:
        # Lagoon instance
        from eth_defi.morpho.vault import MorphoVault

        return MorphoVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.euler_like in features:
        # Lagoon instance
        from eth_defi.euler.vault import EulerVault

        return EulerVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.gains_like in features:
        # Lagoon instance
        from eth_defi.gains.vault import GainsVault

        return GainsVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.ostium_like in features:
        # Lagoon instance
        from eth_defi.gains.vault import OstiumVault

        return OstiumVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.umami_like in features:
        from eth_defi.umami.vault import UmamiVault

        return UmamiVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.plutus_like in features:
        from eth_defi.plutus.vault import PlutusVault

        return PlutusVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.harvest_finance in features:
        from eth_defi.harvest.vault import HarvestVault

        return HarvestVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.d2_like in features:
        from eth_defi.d2.vault import D2Vault

        return D2Vault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.untangled_like in features:
        from eth_defi.untangle.vault import UntangleVault

        return UntangleVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.yearn_v3_like in features or ERC4626Feature.yearn_tokenised_strategy in features:
        # Both of these have fees internatilised
        from eth_defi.yearn.vault import YearnV3Vault

        return YearnV3Vault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.goat_like in features:
        # Both of these have fees internatilised
        from eth_defi.goat.vault import GoatVault

        return GoatVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.usdai_like in features:
        # Both of these have fees internatilised
        from eth_defi.usdai.vault import StakedUSDaiVault

        return StakedUSDaiVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.autopool_like in features:
        # Both of these have fees internatilised
        from eth_defi.autopool.vault import AutoPoolVault

        return AutoPoolVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.nashpoint_like in features:
        # Both of these have fees internatilised
        from eth_defi.nashpoint.vault import NashpointNodeVault

        return NashpointNodeVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.llamma_like in features:
        # Both of these have fees internatilised
        from eth_defi.llamma.vault import LLAMMAVault

        return LLAMMAVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.summer_like in features:
        # Both of these have fees internatilised
        from eth_defi.summer.vault import SummerVault

        return SummerVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.silo_like in features:
        # Both of these have fees internatilised
        from eth_defi.silo.vault import SiloVault

        return SiloVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.truefi_like in features:
        # Both of these have fees internatilised
        from eth_defi.truefi.vault import TrueFiVault

        return TrueFiVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.superform_like in features:
        # Both of these have fees internatilised
        from eth_defi.superform.vault import SuperformVault

        return SuperformVault(web3, spec, token_cache=token_cache, features=features)

    else:
        # Generic ERC-4626 without fee data
        from eth_defi.erc_4626.vault import ERC4626Vault

        return ERC4626Vault(web3, spec, token_cache=token_cache, features=features)


def create_vault_instance_autodetect(
    web3: Web3,
    vault_address: HexAddress | str,
    token_cache: dict | None = None,
) -> VaultBase:
    """Create any vault instance.

    - Probes smart contract call first to identify what kind of vault we are dealing with
    """
    features = detect_vault_features(web3, vault_address, verbose=False)
    vault = create_vault_instance(web3, vault_address, features=features, token_cache=token_cache)
    assert vault is not None, f"Could not create vault instance: {vault_address} with features {features}"
    return vault
