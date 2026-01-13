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

from eth_defi.abi import ZERO_ADDRESS_BYTES, ZERO_ADDRESS_STR
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult, read_multicall_chunked
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.vault.base import VaultBase, VaultSpec
from eth_defi.vault.risk import BROKEN_VAULT_CONTRACTS

logger = logging.getLogger(__name__)


#: Chain restrictions for protocols deployed on 3 or fewer chains.
#:
#: Maps probe function names to the set of chain IDs where that protocol is deployed.
#: Probes for these protocols will be skipped on chains not in their set.
#:
#: Data source: https://top-defi-vaults.tradingstrategy.ai/top_vaults_by_chain.json
#: Data checked: 2026-01-13
#:
CHAIN_RESTRICTED_PROBES: dict[str, set[int]] = {
    # Disabled protocols - not found in vault data, disable everywhere
    "outputToLp0Route": set(),  # Baklava Space - not found in data
    "agent": set(),  # Astrolab - not found in data
    # Single chain protocols
    "marketManager": {143},  # Curvance - Monad only
    "validateComponentRatios": {42161},  # NashPoint - Arbitrum only
    "SAY_TRADER_ROLE": {42161},  # Plutus - Arbitrum only
    "aggregateVault": {42161},  # Umami - Arbitrum only
    "bridgedSupply": {42161},  # USDai - Arbitrum only
    "routerRegistry": {8453},  # Singularity Finance - Base only
    "strategist": {5000},  # Brink - Mantle only
    "vaultManager": {5000},  # Brink - Mantle only
    "strategy": {143},  # Accountable - Monad only
    "queue": {143},  # Accountable - Monad only
    "registry": {42161},  # Ostium - Arbitrum only
    # Two chain protocols
    "claimableKeeper": {137, 42161},  # Untangle Finance - Polygon, Arbitrum
    # Three chain protocols
    "getPerformanceFeeData": {1, 8453, 42161},  # IPOR - Ethereum, Base, Arbitrum
    "borrowed_token": {1, 10, 42161},  # Llama Lend - Ethereum, Optimism, Arbitrum
    "previewRateAfterDeposit": {1, 42161, 80094},  # Royco - Ethereum, Arbitrum, Berachain
    "repoTokenHoldings": {1, 9745, 43114},  # Term Finance - Ethereum, Plasma, Avalanche
    "depositController": {1, 56, 42161},  # TrueFi - Ethereum, BSC, Arbitrum
    "poolId": {1, 8453, 42161},  # Centrifuge - Ethereum, Base, Arbitrum
    "wards": {1, 8453, 42161},  # Centrifuge - Ethereum, Base, Arbitrum
}


def _should_yield_probe(func_name: str, chain_id: int | None) -> bool:
    """Check if a probe call should be yielded based on chain restrictions.

    :param func_name:
        The function name of the probe call.

    :param chain_id:
        The chain ID to check against. If None, always returns True (no filtering).

    :return:
        True if the probe should be yielded, False if it should be skipped.
    """
    if chain_id is None:
        return True  # No filtering when chain_id not provided

    if func_name in CHAIN_RESTRICTED_PROBES:
        return chain_id in CHAIN_RESTRICTED_PROBES[func_name]

    return True  # No restriction, always yield


#: Sentinel result for probes that were filtered out (not executed).
#: Used when accessing probe results for probes that were skipped due to chain filtering.
_MISSING_PROBE_RESULT = None


class _ProbeResultsDict(dict):
    """Wrapper for probe results that returns a "missing" result for filtered probes.

    When probes are filtered by chain_id, some probe keys won't exist in the results.
    This wrapper returns a fake "not successful" result for missing keys, allowing
    identify_vault_features() to work without KeyError.
    """

    def __getitem__(self, key: str) -> EncodedCallResult:
        try:
            return super().__getitem__(key)
        except KeyError:
            # Return a fake result that indicates the probe was not executed
            # This happens when the probe was filtered out due to chain restrictions
            return _MissingProbeResult(key)


class _MissingProbeResult:
    """Fake EncodedCallResult for probes that were filtered out.

    Always returns success=False and empty result, so feature detection
    will correctly skip this protocol.
    """

    __slots__ = ("func_name",)

    def __init__(self, func_name: str):
        self.func_name = func_name

    @property
    def success(self) -> bool:
        return False

    @property
    def result(self) -> bytes:
        return b""


@dataclass(frozen=True, slots=True)
class VaultFeatureProbe:
    """Results of a multicall probing to a vault address."""

    address: HexAddress
    features: set[ERC4626Feature]


def create_probe_calls(
    addresses: Iterable[HexAddress],
    share_probe_amount=1_000_000,
    chain_id: int | None = None,
) -> Iterable[EncodedCall]:
    """Create calls that call each vault address using multicall.

    - Because ERC standards are such a shit show, and nobody is using good interface standard,
      we figure out the vault type by probing it with various calls
    - Skips protocol-specific probes for protocols only deployed on certain chains
      (see :py:data:`CHAIN_RESTRICTED_PROBES` for the list)

    :param addresses:
        Iterable of vault contract addresses to probe.

    :param share_probe_amount:
        Amount used for convertToShares() probe call.

    :param chain_id:
        If provided, filters out probe calls for protocols that are not deployed on this chain.
        This reduces unnecessary RPC calls when scanning chains where certain protocols don't exist.
        Protocols deployed on 3 or fewer chains have their probes skipped on other chains.
        If None, all probes are generated (no filtering).
    """

    assert chain_id is not None, "Changed: always pass chain_id"

    convert_to_shares_payload = eth_abi.encode(["uint256"], [share_probe_amount])
    zero_uint_payload = eth_abi.encode(["uint256"], [0])
    double_address = eth_abi.encode(["address", "address"], [ZERO_ADDRESS_STR, ZERO_ADDRESS_STR])
    zero_address_payload = eth_abi.encode(["address"], [ZERO_ADDRESS_STR])

    # TODO: Might be bit slowish here, but we are not perf intensive
    for address in addresses:
        # ====================
        # Core probes - always yield (required for basic ERC-4626 detection)
        # ====================

        # Probe for broken contracts - if this succeeds, the contract is broken
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="EVM IS BROKEN SHIT()")[0:4],
            function="EVM IS BROKEN SHIT",
            data=b"",
            extra_data=None,
        )

        # name() - should be present on all ERC-4626 vaults
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="name()")[0:4],
            function="name",
            data=b"",
            extra_data=None,
        )

        # convertToShares() - should be present in all ERC-4626 vaults
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="convertToShares(uint256)")[0:4],
            function="convertToShares",
            data=convert_to_shares_payload,
            extra_data=None,
        )

        # ====================
        # Protocol-specific probes - some filtered by chain_id
        # ====================

        # IPOR - Ethereum, Base, Arbitrum only
        # See ipor/vault.py
        if _should_yield_probe("getPerformanceFeeData", chain_id):
            yield EncodedCall.from_keccak_signature(
                address=address,
                signature=Web3.keccak(text="getPerformanceFeeData()")[0:4],
                function="getPerformanceFeeData",
                data=b"",
                extra_data=None,
            )

        # Harvest Finance
        # https://github.com/harvest-finance/harvest/blob/14420a4444c6aaa7bf0d2303a5888feb812a0521/contracts/Vault.sol#L86C12-L86C26
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="vaultFractionToInvestDenominator()")[0:4],
            function="vaultFractionToInvestDenominator",
            data=b"",
            extra_data=None,
        )

        # ERC-7540 async vault detection
        # function isOperator(address controller, address operator) external returns (bool);
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="isOperator(address,address)")[0:4],
            function="isOperator",
            data=double_address,
            extra_data=None,
        )

        # Baklava Space - disabled (not found in data)
        # BRT2: vAMM
        # https://basescan.org/address/0x49AF8CAf88CFc8394FcF08Cf997f69Cee2105f2b#readProxyContract
        if _should_yield_probe("outputToLp0Route", chain_id):
            yield EncodedCall.from_keccak_signature(
                address=address,
                signature=Web3.keccak(text="outputToLp0Route(uint256)")[0:4],
                function="outputToLp0Route",
                data=zero_uint_payload,
                extra_data=None,
            )

        # Astrolab - disabled (not found in data)
        # https://basescan.org/address/0x2aeB4A62f40257bfC96D5be55519f70DB871c744#readContract
        if _should_yield_probe("agent", chain_id):
            yield EncodedCall.from_keccak_signature(
                address=address,
                signature=Web3.keccak(text="agent()")[0:4],
                function="agent",
                data=b"",
                extra_data=None,
            )

        # Gains Network / gToken vaults
        # https://github.com/0xOstium/smart-contracts-public/blob/da3b944623bef814285b7f418d43e6a95f4ad4b1/src/OstiumVault.sol#L243
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="maxDiscountP()")[0:4],
            function="maxDiscountP",
            data=b"",
            extra_data=None,
        )

        # Gains Network tranches
        # https://basescan.org/address/0x944766f715b51967E56aFdE5f0Aa76cEaCc9E7f9#readProxyContract
        # https://github.com/GainsNetwork/gTrade-v6.1/tree/main
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="depositCap()")[0:4],
            function="depositCap",
            data=b"",
            extra_data=None,
        )

        # Morpho V1
        # Moonwell runs on Morpho
        # https://basescan.org/address/0x6b13c060F13Af1fdB319F52315BbbF3fb1D88844#readContract
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="MORPHO()")[0:4],
            function="MORPHO",
            data=b"",
            extra_data=None,
        )

        # Morpho V2 - newer adapter-based architecture
        # https://docs.morpho.org/learn/concepts/vault-v2/
        # https://arbiscan.io/address/0xbeefff13dd098de415e07f033dae65205b31a894
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="adaptersLength()")[0:4],
            function="adaptersLength",
            data=b"",
            extra_data=None,
        )

        # ERC-7575 detection
        # interface IERC7575 is IERC4626 { function share() external view returns (address); }
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="share()")[0:4],
            function="share",
            data=b"",
            extra_data=None,
        )

        # Kiln metavault
        # https://basescan.org/address/0x4b2A4368544E276780342750D6678dC30368EF35#readProxyContract
        # https://github.com/0xZunia/Kiln.MetaVault
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="additionalRewardsStrategy()")[0:4],
            function="additionalRewardsStrategy",
            data=b"",
            extra_data=None,
        )

        # Lagoon
        # https://basescan.org/address/0x6a5ea384e394083149ce39db29d5787a658aa98a#readContract
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="MAX_MANAGEMENT_RATE()")[0:4],
            function="MAX_MANAGEMENT_RATE",
            data=b"",
            extra_data=None,
        )

        # Yearn V2
        # https://etherscan.io/address/0x4cE9c93513DfF543Bc392870d57dF8C04e89Ba0a#readContract
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="GOV()")[0:4],
            function="GOV",
            data=b"",
            extra_data=None,
        )

        # Yearn V3 (Vyper)
        # https://polygonscan.com/address/0xa013fbd4b711f9ded6fb09c1c0d358e2fbc2eaa0#readContract
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="get_default_queue()")[0:4],
            function="get_default_queue",
            data=b"",
            extra_data=None,
        )

        # Superform
        # https://basescan.org/address/0x84d7549557f0fb69efbd1229d8e2f350b483c09b#readContract
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="THIS_CHAIN_ID()")[0:4],
            function="THIS_CHAIN_ID",
            data=b"",
            extra_data=None,
        )

        # Superform (alternative)
        # https://etherscan.io//address/0x862c57d48becB45583AEbA3f489696D22466Ca1b#readProxyContract
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="METADEPOSIT_TYPEHASH()")[0:4],
            function="METADEPOSIT_TYPEHASH",
            data=b"",
            extra_data=None,
        )

        # Term Finance - Ethereum, Plasma, Avalanche only
        # https://etherscan.io/address/0xa10c40f9e318b0ed67ecc3499d702d8db9437228#readProxyContract
        if _should_yield_probe("repoTokenHoldings", chain_id):
            yield EncodedCall.from_keccak_signature(
                address=address,
                signature=Web3.keccak(text="repoTokenHoldings()")[0:4],
                function="repoTokenHoldings",
                data=b"",
                extra_data=None,
            )

        # Euler V1
        # https://basescan.org/address/0x30a9a9654804f1e5b3291a86e83eded7cf281618#code
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="MODULE_VAULT()")[0:4],
            function="MODULE_VAULT",
            data=b"",
            extra_data=None,
        )

        # EulerEarn - Metamorpho-based metavault for Euler ecosystem
        # https://github.com/euler-xyz/euler-earn
        # https://snowtrace.io/address/0xE1A62FDcC6666847d5EA752634E45e134B2F824B
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="supplyQueueLength()")[0:4],
            function="supplyQueueLength",
            data=b"",
            extra_data=None,
        )

        # Ostium - Arbitrum only (registry() call)
        # https://github.com/0xOstium/smart-contracts-public/blob/da3b944623bef814285b7f418d43e6a95f4ad4b1/src/OstiumVault.sol
        if _should_yield_probe("registry", chain_id):
            yield EncodedCall.from_keccak_signature(
                address=address,
                signature=Web3.keccak(text="registry()")[0:4],
                function="registry",
                data=b"",
                extra_data=None,
            )

        # Umami - Arbitrum only
        # https://arbiscan.io/address/0x5f851f67d24419982ecd7b7765defd64fbb50a97#readContract
        if _should_yield_probe("aggregateVault", chain_id):
            yield EncodedCall.from_keccak_signature(
                address=address,
                signature=Web3.keccak(text="aggregateVault()")[0:4],
                function="aggregateVault",
                data=b"",
                extra_data=None,
            )

        # Plutus - Arbitrum only
        if _should_yield_probe("SAY_TRADER_ROLE", chain_id):
            yield EncodedCall.from_keccak_signature(
                address=address,
                signature=Web3.keccak(text="SAY_TRADER_ROLE()")[0:4],
                function="SAY_TRADER_ROLE",
                data=b"",
                extra_data=None,
            )

        # D2 Finance
        # https://arbiscan.io/address/0x75288264fdfea8ce68e6d852696ab1ce2f3e5004#code
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="getCurrentEpochInfo()")[0:4],
            function="getCurrentEpochInfo",
            data=b"",
            extra_data=None,
        )

        # Untangled Finance - Polygon, Arbitrum only
        # https://app.untangled.finance/
        # https://arbiscan.io/address/0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9#code
        if _should_yield_probe("claimableKeeper", chain_id):
            yield EncodedCall.from_keccak_signature(
                address=address,
                signature=Web3.keccak(text="claimableKeeper()")[0:4],
                function="claimableKeeper",
                data=b"",
                extra_data=None,
            )

        # Yearn TokenizedStrategy / Fluid conflicting
        # https://arbiscan.io/address/0xb739ae19620f7ecb4fb84727f205453aa5bc1ad2#code
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="tradeFactory()")[0:4],
            function="tradeFactory",
            data=b"",
            extra_data=None,
        )

        # Goat Protocol
        # https://github.com/goatfi/contracts
        # https://github.com/goatfi/contracts/blob/main/src/infra/multistrategy/Multistrategy.sol
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="DEGRADATION_COEFFICIENT()")[0:4],
            function="DEGRADATION_COEFFICIENT",
            data=b"",
            extra_data=None,
        )

        # USDai - Arbitrum only
        # https://arbiscan.io/address/0xc0540184de0e42eab2b0a4fc35f4817041001e85#code
        if _should_yield_probe("bridgedSupply", chain_id):
            yield EncodedCall.from_keccak_signature(
                address=address,
                signature=Web3.keccak(text="bridgedSupply()")[0:4],
                function="bridgedSupply",
                data=b"",
                extra_data=None,
            )

        # Autopool (Tokemak)
        # https://arbiscan.io/address/0xf63b7f49b4f5dc5d0e7e583cfd79dc64e646320c#readProxyContract
        # https://github.com/Tokemak/v2-core-pub
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="autoPoolStrategy()")[0:4],
            function="autoPoolStrategy",
            data=b"",
            extra_data=None,
        )

        # NashPoint - Arbitrum only
        # https://arbiscan.io/address/0x6ca200319a0d4127a7a473d6891b86f34e312f42#readContract
        if _should_yield_probe("validateComponentRatios", chain_id):
            yield EncodedCall.from_keccak_signature(
                address=address,
                signature=Web3.keccak(text="validateComponentRatios()")[0:4],
                function="validateComponentRatios",
                data=b"",
                extra_data=None,
            )

        # Llama Lend (LLAMMA) - Ethereum, Optimism, Arbitrum only
        # https://arbiscan.io/address/0xe296ee7f83d1d95b3f7827ff1d08fe1e4cf09d8d#code
        if _should_yield_probe("borrowed_token", chain_id):
            yield EncodedCall.from_keccak_signature(
                address=address,
                signature=Web3.keccak(text="borrowed_token()")[0:4],
                function="borrowed_token",
                data=b"",
                extra_data=None,
            )

        # Summer Earn
        # https://arbiscan.io/address/0xe296ee7f83d1d95b3f7827ff1d08fe1e4cf09d8d#code
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="ADMIRALS_QUARTERS_ROLE()")[0:4],
            function="ADMIRALS_QUARTERS_ROLE",
            data=b"",
            extra_data=None,
        )

        # Silo Finance
        # https://arbiscan.io/address/0xacb7432a4bb15402ce2afe0a7c9d5b738604f6f9#readContract
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="utilizationData()")[0:4],
            function="utilizationData",
            data=b"",
            extra_data=None,
        )

        # TrueFi - Ethereum, BSC, Arbitrum only
        # https://github.com/TrueFi-Protocol
        # https://arbiscan.io/address/0x8626a4234721A605Fc84Bb49d55194869Ae95D98#readContract
        if _should_yield_probe("depositController", chain_id):
            yield EncodedCall.from_keccak_signature(
                address=address,
                signature=Web3.keccak(text="depositController()")[0:4],
                function="depositController",
                data=b"",
                extra_data=None,
            )

        # Yearn Morpho Compounder strategy - uses auction() for reward liquidation
        # https://etherscan.io/address/0x6D2981FF9b8d7edbb7604de7A65BAC8694ac849F
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="auction()")[0:4],
            function="auction",
            data=b"",
            extra_data=None,
        )

        # Yearn TokenizedStrategy - vault() points to parent vault
        # https://etherscan.io/address/0x6D2981FF9b8d7edbb7604de7A65BAC8694ac849F
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="vault()")[0:4],
            function="vault",
            data=b"",
            extra_data=None,
        )

        # Teller Protocol - LenderCommitmentGroup_Pool_V2 long-tail lending pools
        # https://basescan.org/address/0x13cd7cf42ccbaca8cd97e7f09572b6ea0de1097b
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="TELLER_V2()")[0:4],
            function="TELLER_V2",
            data=b"",
            extra_data=None,
        )

        # Upshift - TokenizedAccount vaults built on August infrastructure
        # https://etherscan.io/address/0x69fc3f84fd837217377d9dae0212068ceb65818e
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="settlementAccount()")[0:4],
            function="settlementAccount",
            data=b"",
            extra_data=None,
        )

        # Centrifuge - Ethereum, Base, Arbitrum only
        # LiquidityPool vaults for RWA financing
        # https://etherscan.io/address/0xa702ac7953e6a66d2b10a478eb2f0e2b8c8fd23e
        # https://github.com/centrifuge/liquidity-pools
        if _should_yield_probe("poolId", chain_id):
            yield EncodedCall.from_keccak_signature(
                address=address,
                signature=Web3.keccak(text="poolId()")[0:4],
                function="poolId",
                data=b"",
                extra_data=None,
            )

        # Centrifuge wards call for additional verification
        if _should_yield_probe("wards", chain_id):
            yield EncodedCall.from_keccak_signature(
                address=address,
                signature=Web3.keccak(text="wards(address)")[0:4],
                function="wards",
                data=zero_address_payload,
                extra_data=None,
            )

        # Royco Protocol - Ethereum, Arbitrum, Berachain only
        # WrappedVault contracts with reward distribution
        # https://etherscan.io/address/0x887d57a509070a0843c6418eb5cffc090dcbbe95
        if _should_yield_probe("previewRateAfterDeposit", chain_id):
            yield EncodedCall.from_keccak_signature(
                address=address,
                signature=Web3.keccak(text="previewRateAfterDeposit(address,uint256)")[0:4],
                function="previewRateAfterDeposit",
                data=eth_abi.encode(["address", "uint256"], [ZERO_ADDRESS_STR, 0]),
                extra_data=None,
            )

        # Gearbox Protocol - PoolV3 lending pools that return "POOL" from contractType()
        # https://github.com/Gearbox-protocol/core-v3/blob/main/contracts/pool/PoolV3.sol
        yield EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="contractType()")[0:4],
            function="contractType",
            data=b"",
            extra_data=None,
        )

        # Curvance Protocol - Monad only
        # BorrowableCToken and other cToken vaults have marketManager()
        # https://github.com/curvance/curvance-contracts
        # https://monadscan.com/address/0xad4aa2a713fb86fbb6b60de2af9e32a11db6abf2
        if _should_yield_probe("marketManager", chain_id):
            yield EncodedCall.from_keccak_signature(
                address=address,
                signature=Web3.keccak(text="marketManager()")[0:4],
                function="marketManager",
                data=b"",
                extra_data=None,
            )

        # Singularity Finance DynaVaults - Base only
        # routerRegistry() returns the address of the router registry contract
        # https://basescan.org/address/0xdf71487381Ab5bD5a6B17eAa61FE2E6045A0e805
        if _should_yield_probe("routerRegistry", chain_id):
            yield EncodedCall.from_keccak_signature(
                address=address,
                signature=Web3.keccak(text="routerRegistry()")[0:4],
                function="routerRegistry",
                data=b"",
                extra_data=None,
            )

        # Brink vaults - Mantle only
        # Uses modified events (DepositFunds/WithdrawFunds) instead of standard ERC-4626
        # https://mantlescan.xyz/address/0xE12EED61E7cC36E4CF3304B8220b433f1fD6e254
        if _should_yield_probe("strategist", chain_id):
            yield EncodedCall.from_keccak_signature(
                address=address,
                signature=Web3.keccak(text="strategist()")[0:4],
                function="strategist",
                data=b"",
                extra_data=None,
            )

        if _should_yield_probe("vaultManager", chain_id):
            yield EncodedCall.from_keccak_signature(
                address=address,
                signature=Web3.keccak(text="vaultManager()")[0:4],
                function="vaultManager",
                data=b"",
                extra_data=None,
            )

        # Accountable Capital - Monad only
        # AccountableAsyncRedeemVault with ERC-7540 async redemption queue
        # https://monadscan.com/address/0x58ba69b289De313E66A13B7D1F822Fc98b970554
        if _should_yield_probe("strategy", chain_id):
            yield EncodedCall.from_keccak_signature(
                address=address,
                signature=Web3.keccak(text="strategy()")[0:4],
                function="strategy",
                data=b"",
                extra_data=None,
            )

        if _should_yield_probe("queue", chain_id):
            yield EncodedCall.from_keccak_signature(
                address=address,
                signature=Web3.keccak(text="queue()")[0:4],
                function="queue",
                data=b"",
                extra_data=None,
            )


def identify_vault_features(
    address: HexAddress,
    calls: dict[str, EncodedCallResult],
    debug_text: str | None,
) -> set[ERC4626Feature]:
    """Based on multicall results, create the feature flags for the vault..

    :param calls:
        Call name -> result
    """

    # Shortcut for single vault protocols
    hardcoded_features = HARDCODED_PROTOCOLS.get(address.lower())
    if hardcoded_features is not None:
        return hardcoded_features

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

    # Morpho V2 - uses adaptersLength() instead of MORPHO()
    # Must check that MORPHO() fails to distinguish from V1
    if calls["adaptersLength"].success and not calls["MORPHO"].success:
        features.add(ERC4626Feature.morpho_v2_like)

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

    # EulerEarn - Metamorpho-based metavault
    # Must check supplyQueueLength AND curator to distinguish from other protocols
    # https://snowtrace.io/address/0xE1A62FDcC6666847d5EA752634E45e134B2F824B
    if calls["supplyQueueLength"].success:
        features.add(ERC4626Feature.euler_earn_like)

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

    # Teller Protocol - LenderCommitmentGroup_Pool_V2
    # https://basescan.org/address/0x13cd7cf42ccbaca8cd97e7f09572b6ea0de1097b
    if calls["TELLER_V2"].success:
        features.add(ERC4626Feature.teller_like)

    # Upshift - TokenizedAccount vaults
    # https://etherscan.io/address/0x69fc3f84fd837217377d9dae0212068ceb65818e
    if calls["settlementAccount"].success:
        features.add(ERC4626Feature.upshift_like)

    # Centrifuge - LiquidityPool vaults for RWA financing
    # https://etherscan.io/address/0xa702ac7953e6a66d2b10a478eb2f0e2b8c8fd23e
    # Both poolId and trancheId must succeed for Centrifuge identification
    if calls["poolId"].success and calls["wards"].success:
        features.add(ERC4626Feature.centrifuge_like)
        features.add(ERC4626Feature.erc_7540_like)

    # Royco Protocol - WrappedVault with reward distribution
    # https://etherscan.io/address/0x887d57a509070a0843c6418eb5cffc090dcbbe95
    if calls["previewRateAfterDeposit"].success:
        features.add(ERC4626Feature.royco_like)

    # Gearbox Protocol - PoolV3 lending pools
    # contractType() returns "POOL" as bytes32
    # https://github.com/Gearbox-protocol/core-v3/blob/main/contracts/pool/PoolV3.sol
    if calls["contractType"].success:
        try:
            contract_type = calls["contractType"].result
            if contract_type:
                # Decode bytes32 to string, strip null bytes
                decoded = contract_type.rstrip(b"\x00").decode("utf-8", errors="ignore")
                if decoded == "POOL":
                    features.add(ERC4626Feature.gearbox_like)
        except Exception:
            pass

    # Curvance Protocol - BorrowableCToken and other cToken vaults
    # marketManager() returns the IMarketManager address
    # https://github.com/curvance/curvance-contracts
    if calls["marketManager"].success:
        features.add(ERC4626Feature.curvance_like)

    # Singularity Finance DynaVaults
    # routerRegistry() returns the address of the router registry contract
    # https://basescan.org/address/0xdf71487381Ab5bD5a6B17eAa61FE2E6045A0e805
    if calls["routerRegistry"].success:
        features.add(ERC4626Feature.singularity_like)

    # Brink vaults
    # Uses modified events (DepositFunds/WithdrawFunds) instead of standard ERC-4626.
    # Both strategist() and vaultManager() must succeed for Brink identification.
    # https://mantlescan.xyz/address/0xE12EED61E7cC36E4CF3304B8220b433f1fD6e254
    if calls["strategist"].success and calls["vaultManager"].success:
        features.add(ERC4626Feature.brink_like)

    # Accountable Capital
    # AccountableAsyncRedeemVault with ERC-7540 async redemption queue.
    # Both strategy() and queue() must succeed for Accountable identification.
    # https://monadscan.com/address/0x58ba69b289De313E66A13B7D1F822Fc98b970554
    if calls["strategy"].success and calls["queue"].success:
        features.add(ERC4626Feature.accountable_like)

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
        elif "Morpho" in name and calls["auction"].success and calls["vault"].success:
            # Yearn Morpho Compounder strategy (TokenizedStrategy with auction mechanism)
            features.add(ERC4626Feature.yearn_morpho_compounder_like)
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

    probe_calls = list(create_probe_calls(addresses, chain_id=chain_id))

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
        # Wrap with _ProbeResultsDict to handle missing probes from chain filtering
        wrapped_results = _ProbeResultsDict(address_call_results)
        features = identify_vault_features(address, wrapped_results, debug_text=f"vault: {address}")
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

    hardcoded_flags = HARDCODED_PROTOCOLS.get(address.lower())
    if hardcoded_flags:
        features = hardcoded_flags
        logger.debug("Using hardcoded vault features for %s: %s", address, features)
        return hardcoded_flags

    address = Web3.to_checksum_address(address)
    logger.info("Detecting vault features for %s", address)
    chain_id = web3.eth.chain_id
    probe_calls = list(create_probe_calls([address], chain_id=chain_id))
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

    # Wrap with _ProbeResultsDict to handle missing probes from chain filtering
    wrapped_results = _ProbeResultsDict(results)
    features = identify_vault_features(address, wrapped_results, debug_text=f"vault: {address}")
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
        from eth_defi.erc_4626.vault_protocol.ipor.vault import IPORVault

        return IPORVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.lagoon_like in features:
        # Lagoon instance
        from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault

        return LagoonVault(web3, spec, token_cache=token_cache, features=features)

    # TODO: Some module deadlock sheningans for Morpho
    elif ERC4626Feature.morpho_like in features:
        # Morpho V1 instance
        from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import MorphoV1Vault
        from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault

        return MorphoV1Vault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.morpho_v2_like in features:
        # Morpho V2 instance (adapter-based architecture)
        from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import MorphoV1Vault
        from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault

        return MorphoV2Vault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.euler_earn_like in features:
        # EulerEarn metavault instance
        from eth_defi.erc_4626.vault_protocol.euler.vault import EulerEarnVault

        return EulerEarnVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.euler_like in features:
        # Euler instance
        from eth_defi.erc_4626.vault_protocol.euler.vault import EulerVault

        return EulerVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.gains_like in features:
        # Gains instance
        from eth_defi.erc_4626.vault_protocol.gains.vault import GainsVault

        return GainsVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.ostium_like in features:
        # Ostium instance
        from eth_defi.erc_4626.vault_protocol.gains.vault import OstiumVault

        return OstiumVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.umami_like in features:
        from eth_defi.erc_4626.vault_protocol.umami.vault import UmamiVault

        return UmamiVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.plutus_like in features:
        from eth_defi.erc_4626.vault_protocol.plutus.vault import PlutusVault

        return PlutusVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.harvest_finance in features:
        from eth_defi.erc_4626.vault_protocol.harvest.vault import HarvestVault

        return HarvestVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.d2_like in features:
        from eth_defi.erc_4626.vault_protocol.d2.vault import D2Vault

        return D2Vault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.untangled_like in features:
        from eth_defi.erc_4626.vault_protocol.untangle.vault import UntangleVault

        return UntangleVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.cap_like in features:
        # Covered Agent Protocol (CAP) uses Yearn V3 infrastructure
        from eth_defi.erc_4626.vault_protocol.cap.vault import CAPVault

        return CAPVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.foxify_like in features:
        from eth_defi.erc_4626.vault_protocol.foxify.vault import FoxifyVault

        return FoxifyVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.liquidity_royalty_like in features:
        from eth_defi.erc_4626.vault_protocol.liquidity_royalty.vault import LiquidityRoyalyJuniorVault

        return LiquidityRoyalyJuniorVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.csigma_like in features:
        from eth_defi.erc_4626.vault_protocol.csigma.vault import CsigmaVault

        return CsigmaVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.spark_like in features:
        from eth_defi.erc_4626.vault_protocol.spark.vault import SparkVault

        return SparkVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.yearn_morpho_compounder_like in features:
        # Yearn V3 vault with Morpho Compounder strategy
        from eth_defi.erc_4626.vault_protocol.yearn.morpho_compounder import YearnMorphoCompounderStrategy

        return YearnMorphoCompounderStrategy(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.yearn_v3_like in features or ERC4626Feature.yearn_tokenised_strategy in features:
        # Both of these have fees internatilised
        from eth_defi.erc_4626.vault_protocol.yearn.vault import YearnV3Vault

        return YearnV3Vault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.goat_like in features:
        # Both of these have fees internatilised
        from eth_defi.erc_4626.vault_protocol.goat.vault import GoatVault

        return GoatVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.usdai_like in features:
        # Both of these have fees internatilised
        from eth_defi.erc_4626.vault_protocol.usdai.vault import StakedUSDaiVault

        return StakedUSDaiVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.autopool_like in features:
        # Both of these have fees internatilised
        from eth_defi.erc_4626.vault_protocol.autopool.vault import AutoPoolVault

        return AutoPoolVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.nashpoint_like in features:
        # Both of these have fees internatilised
        from eth_defi.erc_4626.vault_protocol.nashpoint.vault import NashpointNodeVault

        return NashpointNodeVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.llamma_like in features:
        # Llama Lend - fees are internalised
        from eth_defi.erc_4626.vault_protocol.llama_lend.vault import LlamaLendVault

        return LlamaLendVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.summer_like in features:
        # Both of these have fees internatilised
        from eth_defi.erc_4626.vault_protocol.summer.vault import SummerVault

        return SummerVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.silo_like in features:
        # Both of these have fees internatilised
        from eth_defi.erc_4626.vault_protocol.silo.vault import SiloVault

        return SiloVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.truefi_like in features:
        # Both of these have fees internatilised
        from eth_defi.erc_4626.vault_protocol.truefi.vault import TrueFiVault

        return TrueFiVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.superform_like in features:
        # Both of these have fees internatilised
        from eth_defi.erc_4626.vault_protocol.superform.vault import SuperformVault

        return SuperformVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.teller_like in features:
        from eth_defi.erc_4626.vault_protocol.teller.vault import TellerVault

        return TellerVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.deltr_like in features:
        from eth_defi.erc_4626.vault_protocol.deltr.vault import DeltrVault

        return DeltrVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.upshift_like in features:
        from eth_defi.erc_4626.vault_protocol.upshift.vault import UpshiftVault

        return UpshiftVault(web3, spec, token_cache=token_cache, features=features)
    elif ERC4626Feature.sky_like in features:
        from eth_defi.erc_4626.vault_protocol.sky.vault import SkyVault

        return SkyVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.maple_like in features:
        from eth_defi.erc_4626.vault_protocol.maple.vault import SyrupVault

        return SyrupVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.maple_aqru_like in features:
        from eth_defi.erc_4626.vault_protocol.maple.aqru_vault import AQRUPoolVault

        return AQRUPoolVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.centrifuge_like in features:
        from eth_defi.erc_4626.vault_protocol.centrifuge.vault import CentrifugeVault

        return CentrifugeVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.ethena_like in features:
        from eth_defi.erc_4626.vault_protocol.ethena.vault import EthenaVault

        return EthenaVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.usdd_like in features:
        from eth_defi.erc_4626.vault_protocol.usdd.vault import USSDVault

        return USSDVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.term_finance_like in features:
        from eth_defi.erc_4626.vault_protocol.term_finance.vault import TermFinanceVault

        return TermFinanceVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.zerolend_like in features:
        from eth_defi.erc_4626.vault_protocol.zerolend.vault import ZeroLendVault

        return ZeroLendVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.royco_like in features:
        from eth_defi.erc_4626.vault_protocol.royco.vault import RoycoVault

        return RoycoVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.eth_strategy_like in features:
        from eth_defi.erc_4626.vault_protocol.eth_strategy.vault import EthStrategyVault

        return EthStrategyVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.yuzu_money_like in features:
        from eth_defi.erc_4626.vault_protocol.yuzu_money.vault import YuzuMoneyVault

        return YuzuMoneyVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.altura_like in features:
        from eth_defi.erc_4626.vault_protocol.altura.vault import AlturaVault

        return AlturaVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.spectra_usdn_wrapper_like in features or ERC4626Feature.spectra_erc4626_wrapper_like in features:
        from eth_defi.erc_4626.vault_protocol.spectra.erc4626_wrapper_vault import SpectraERC4626WrapperVault

        return SpectraERC4626WrapperVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.gearbox_like in features:
        from eth_defi.erc_4626.vault_protocol.gearbox.vault import GearboxVault

        return GearboxVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.mainstreet_like in features:
        from eth_defi.erc_4626.vault_protocol.mainstreet.vault import MainstreetVault

        return MainstreetVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.yieldfi_like in features:
        from eth_defi.erc_4626.vault_protocol.yieldfi.vault import YieldFiVault

        return YieldFiVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.resolv_like in features:
        from eth_defi.erc_4626.vault_protocol.resolv.vault import ResolvVault

        return ResolvVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.curvance_like in features:
        from eth_defi.erc_4626.vault_protocol.curvance.vault import CurvanceVault

        return CurvanceVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.singularity_like in features:
        from eth_defi.erc_4626.vault_protocol.singularity.vault import SingularityVault

        return SingularityVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.brink_like in features:
        from eth_defi.erc_4626.vault_protocol.brink.vault import BrinkVault

        return BrinkVault(web3, spec, token_cache=token_cache, features=features)

    elif ERC4626Feature.accountable_like in features:
        from eth_defi.erc_4626.vault_protocol.accountable.vault import AccountableVault

        return AccountableVault(web3, spec, token_cache=token_cache, features=features)

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


#: Handle problematic protocols.
#:
#: Some protocols cannot be detected by their vault smart contract structure, because they are using copy-paste smart contracts.
#: For these, we need to do by vault contract address whitelisting here.
#:
HARDCODED_PROTOCOLS = {
    # CAP - Covered Agent Protocol
    "0x3ed6aa32c930253fc990de58ff882b9186cd0072": {ERC4626Feature.cap_like},
    # CAP - Covered Agent Protocol - AaveV3Lender USDC vault on Ethereum
    # https://etherscan.io/address/0x7d7f72d393f242da6e22d3b970491c06742984ff
    "0x7d7f72d393f242da6e22d3b970491c06742984ff": {ERC4626Feature.cap_like},
    # Foxify - Sonic chain
    "0x3ccff8c929b497c1ff96592b8ff592b45963e732": {ERC4626Feature.foxify_like},
    # Liquidity Royalty Tranching - Junior Vault on Berachain
    "0x3a0a97dca5e6cacc258490d5ece453412f8e1883": {ERC4626Feature.liquidity_royalty_like},
    # cSigma Finance - csUSD vault on Ethereum
    # https://etherscan.io/address/0xd5d097f278a735d0a3c609deee71234cac14b47e
    "0xd5d097f278a735d0a3c609deee71234cac14b47e": {ERC4626Feature.csigma_like},
    # cSigma Finance - CsigmaV2Pool on Ethereum
    # https://etherscan.io/address/0x438982ea288763370946625fd76c2508ee1fb229
    "0x438982ea288763370946625fd76c2508ee1fb229": {ERC4626Feature.csigma_like},
    # cSigma Finance - cSuperior Quality Private Credit vault on Ethereum
    # https://etherscan.io/address/0x50d59b785df23728d9948804f8ca3543237a1495
    "0x50d59b785df23728d9948804f8ca3543237a1495": {ERC4626Feature.csigma_like},
    # Spark - sUSDC vault on Ethereum
    # https://etherscan.io/address/0xbc65ad17c5c0a2a4d159fa5a503f4992c7b545fe
    "0xbc65ad17c5c0a2a4d159fa5a503f4992c7b545fe": {ERC4626Feature.spark_like},
    # Deltr - StakeddUSD vault on Ethereum
    # https://etherscan.io/address/0xa7a31e6a81300120b7c4488ec3126bc1ad11f320
    "0xa7a31e6a81300120b7c4488ec3126bc1ad11f320": {ERC4626Feature.deltr_like},
    # Sky (formerly MakerDAO) - stUSDS vault on Ethereum
    # https://etherscan.io/address/0x99cd4ec3f88a45940936f469e4bb72a2a701eeb9
    "0x99cd4ec3f88a45940936f469e4bb72a2a701eeb9": {ERC4626Feature.sky_like},
    # Sky (formerly MakerDAO) - sUSDS vault on Ethereum
    # https://etherscan.io/address/0xa3931d71877c0e7a3148cb7eb4463524fec27fbd
    "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd": {ERC4626Feature.sky_like},
    # Maple Finance - syrupUSDC vault on Ethereum
    # https://etherscan.io/address/0x80ac24aa929eaf5013f6436cda2a7ba190f5cc0b
    "0x80ac24aa929eaf5013f6436cda2a7ba190f5cc0b": {ERC4626Feature.maple_like},
    # Maple Finance - syrupUSDT vault on Ethereum
    # https://etherscan.io/address/0x356b8d89c1e1239cbbb9de4815c39a1474d5ba7d
    "0x356b8d89c1e1239cbbb9de4815c39a1474d5ba7d": {ERC4626Feature.maple_like},
    # Maple Finance - AQRU Pool (Real-World Receivables) on Ethereum
    # https://etherscan.io/address/0xe9d33286f0E37f517B1204aA6dA085564414996d
    "0xe9d33286f0e37f517b1204aa6da085564414996d": {ERC4626Feature.maple_aqru_like},
    # Ethena - sUSDe vault on Ethereum
    # https://etherscan.io/address/0x9d39a5de30e57443bff2a8307a4256c8797a3497
    "0x9d39a5de30e57443bff2a8307a4256c8797a3497": {ERC4626Feature.ethena_like},
    # Decentralized USD (USDD) - sUSDD vault on Ethereum
    # https://etherscan.io/address/0xC5d6A7B61d18AfA11435a889557b068BB9f29930
    "0xc5d6a7b61d18afa11435a889557b068bb9f29930": {ERC4626Feature.usdd_like},
    # Decentralized USD (USDD) - sUSDD vault on BNB Chain
    # https://bscscan.com/address/0x8bA9dA757d1D66c58b1ae7e2ED6c04087348A82d
    "0x8ba9da757d1d66c58b1ae7e2ed6c04087348a82d": {ERC4626Feature.usdd_like},
    # Yearn SparkCompounder - ysUSDS vault on Ethereum
    # https://etherscan.io/address/0xc9f01b5c6048b064e6d925d1c2d7206d4feef8a3
    "0xc9f01b5c6048b064e6d925d1c2d7206d4feef8a3": {ERC4626Feature.yearn_tokenised_strategy},
    # ZeroLend RWA USDC vault wrapped by Royco on Ethereum
    # https://etherscan.io/address/0x887d57a509070a0843c6418eb5cffc090dcbbe95
    "0x887d57a509070a0843c6418eb5cffc090dcbbe95": {ERC4626Feature.zerolend_like, ERC4626Feature.royco_like},
    # ETH Strategy - ESPN (EthStrategyPerpetualNote) vault on Ethereum
    # https://etherscan.io/address/0xb250c9e0f7be4cff13f94374c993ac445a1385fe
    "0xb250c9e0f7be4cff13f94374c993ac445a1385fe": {ERC4626Feature.eth_strategy_like},
    # Yuzu Money - yzPP (Yuzu Protection Pool) vault on Plasma
    # https://plasmascan.to/address/0xebfc8c2fe73c431ef2a371aea9132110aab50dca
    "0xebfc8c2fe73c431ef2a371aea9132110aab50dca": {ERC4626Feature.yuzu_money_like},
    # Altura - NavVault (AVLT) on HyperEVM
    # https://hyperevmscan.io/address/0xd0ee0cf300dfb598270cd7f4d0c6e0d8f6e13f29
    "0xd0ee0cf300dfb598270cd7f4d0c6e0d8f6e13f29": {ERC4626Feature.altura_like},
    # Spectra Finance - ERC4626 wrapper for WUSDN (SmarDex delta-neutral synthetic dollar)
    # https://etherscan.io/address/0x06a491e3efee37eb191d0434f54be6e42509f9d3
    "0x06a491e3efee37eb191d0434f54be6e42509f9d3": {ERC4626Feature.spectra_usdn_wrapper_like},
    # Spectra Finance - ERC4626 wrapper (sw-earn) on Monad
    # https://monadscan.com/address/0x28e60b466a075cecef930d29f7f1b0facf48f950
    "0x28e60b466a075cecef930d29f7f1b0facf48f950": {ERC4626Feature.spectra_erc4626_wrapper_like},
    # Mainstreet Finance - smsUSD (legacy) vault on Sonic
    # https://sonicscan.org/address/0xc7990369DA608C2F4903715E3bD22f2970536C29
    "0xc7990369da608c2f4903715e3bd22f2970536c29": {ERC4626Feature.mainstreet_like},
    # Mainstreet Finance - Staked msUSD vault on Ethereum
    # https://etherscan.io/address/0x890a5122aa1da30fec4286de7904ff808f0bd74a
    "0x890a5122aa1da30fec4286de7904ff808f0bd74a": {ERC4626Feature.mainstreet_like},
    # YieldFi - vyUSD vault on Ethereum
    # https://etherscan.io/address/0x2e3c5e514eef46727de1fe44618027a9b70d92fc
    "0x2e3c5e514eef46727de1fe44618027a9b70d92fc": {ERC4626Feature.yieldfi_like},
    # YieldFi - yUSD vault on Arbitrum
    # https://arbiscan.io/address/0x4772d2e014f9fc3a820c444e3313968e9a5c8121
    "0x4772d2e014f9fc3a820c444e3313968e9a5c8121": {ERC4626Feature.yieldfi_like},
    # Resolv - wstUSR (Wrapped stUSR) vault on Ethereum
    # ERC-4626 wrapper around rebasing staked USR token
    # https://etherscan.io/address/0x1202f5c7b4b9e47a1a484e8b270be34dbbc75055
    "0x1202f5c7b4b9e47a1a484e8b270be34dbbc75055": {ERC4626Feature.resolv_like},
}

for a in HARDCODED_PROTOCOLS.keys():
    assert a == a.lower(), f"Hardcoded protocol address not lowercased: {a}"
