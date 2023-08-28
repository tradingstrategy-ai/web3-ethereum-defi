"""Reserve, TVL and interest data reading of pools.

- Reads Aave reserves metrics data on-chain from a certain timestamp/block number

- Relies on a lot of undocumented Aave v3 source code to pull out the data

- Based on

  - https://github.com/aave/aave-utilities
  - https://github.com/aave/aave-utilities/tree/master/packages/contract-helpers/src/v3-UiPoolDataProvider-contract
  - https://github.com/aave/aave-v3-periphery/blob/master/contracts/misc/UiPoolDataProviderV3.sol
  - https://github.com/aave/aave-ui/blob/f34f1cfc4fa6c1128b31eaa70b37b5b2109d1dc5/src/libs/pool-data-provider/hooks/use-v2-protocol-data-with-rpc.tsx#L62
  - https://github.com/aave/aave-utilities/blob/664e92b5c7710e8060d4dcac5d6c0ebb48bb069f/packages/math-utils/src/formatters/user/index.ts#L95
  - https://github.com/aave/aave-utilities/blob/664e92b5c7710e8060d4dcac5d6c0ebb48bb069f/packages/math-utils/src/formatters/reserve/index.ts#L310

"""
from dataclasses import dataclass
from typing import Dict, List, Tuple, TypeAlias, TypedDict

from web3 import Web3
from web3._utils.abi import named_tree
from web3.contract import Contract

from eth_defi.aave_v3.deployer import AaveDeployer
from eth_defi.event_reader.conversion import convert_jsonrpc_value_to_int

#:
#: Aave contracts we need to know about to read reserves data.
#:
#: Chain id -> labelled address mapping from Aave documentation
#: https://docs.aave.com/developers/deployed-contracts/v3-mainnet
#:
#:
_addresses = {
    # Ethereum
    1: {
        "PoolAddressProvider": "0x2f39d218133AFaB8F2B819B1066c7E434Ad94E9e",
        "UiPoolDataProviderV3": "0x91c0eA31b49B69Ea18607702c5d9aC360bf3dE7d",
    },
    # Polygon
    137: {
        "PoolAddressProvider": "0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb",
        "UiPoolDataProviderV3": "0xC69728f11E9E6127733751c8410432913123acf1",
    },
    # Binance Smarrt Chain mainnet (not supported by AAVE v3)
    # 56: {
    #     "PoolAddressProvider": "",
    #     "UiPoolDataProviderV3": "",
    # },
    # Avalanche C-chain
    43114: {
        "PoolAddressProvider": "0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb",
        "UiPoolDataProviderV3": "0xF71DBe0FAEF1473ffC607d4c555dfF0aEaDb878d",
    },
    # Arbitrum One
    42161: {
        "PoolAddressProvider": "0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb",
        "UiPoolDataProviderV3": "0x145dE30c929a065582da84Cf96F88460dB9745A7",
    },
    # Ethereum Classic (not supported by AAVE v3)
    # 61: {
    #     "PoolAddressProvider": "",
    #     "UiPoolDataProviderV3": "",
    # },
    # Ganache test chain  (not supported by AAVE v3)
    # 1337: {
    #     "PoolAddressProvider": "",
    #     "UiPoolDataProviderV3": "",
    # },
}


class AaveContractsNotConfigured(Exception):
    """We lack hardcoded data of Aave contract addresses for a particular chain."""


@dataclass
class HelperContracts:
    """Contracts needed to resolve reserve info on Aave v3."""

    #: Which EVM chain
    chain_id: int

    #: See
    #: - https://github.com/aave/aave-v3-periphery/blob/master/contracts/misc/interfaces/IUiPoolDataProviderV3.sol
    #: - https://github.com/aave/aave-v3-periphery/blob/master/contracts/misc/UiPoolDataProviderV3.sol
    ui_pool_data_provider: Contract

    #: See https://github.com/aave/aave-v3-core/blob/27a6d5c83560694210849d4abf09a09dec8da388/contracts/interfaces/IPoolAddressesProvider.sol#L5
    pool_addresses_provider: Contract


#: Quick and dirty "any" Solidity value hack
StructVal: TypeAlias = str | bool | int


class AggregatedReserveData(TypedDict):
    """Rough mapping of AggreatedReserveData in Aave v3 Solidity source code.

    .. note ::

        This data is not useful until JavaScript based formatters from
        aave-utilities are applied. As writing of this, these formatters are only
        available as undocumented JavaScript code in this repository.
        `See the repository for more information <https://github.com/aave/aave-utilities>`__.

    """

    underlyingAsset: StructVal
    name: StructVal
    symbol: StructVal
    decimals: StructVal
    baseLTVasCollateral: StructVal
    reserveLiquidationThreshold: StructVal
    reserveLiquidationBonus: StructVal
    reserveFactor: StructVal
    usageAsCollateralEnabled: StructVal
    borrowingEnabled: StructVal
    stableBorrowRateEnabled: StructVal
    isActive: StructVal
    isFrozen: StructVal
    liquidityIndex: StructVal
    variableBorrowIndex: StructVal
    liquidityRate: StructVal
    variableBorrowRate: StructVal
    stableBorrowRate: StructVal
    lastUpdateTimestamp: StructVal
    aTokenAddress: StructVal
    stableDebtTokenAddress: StructVal
    variableDebtTokenAddress: StructVal
    interestRateStrategyAddress: StructVal
    availableLiquidity: StructVal
    totalPrincipalStableDebt: StructVal
    averageStableRate: StructVal
    stableDebtLastUpdateTimestamp: StructVal
    totalScaledVariableDebt: StructVal
    priceInMarketReferenceCurrency: StructVal
    priceOracle: StructVal
    variableRateSlope1: StructVal
    variableRateSlope2: StructVal
    stableRateSlope1: StructVal
    stableRateSlope2: StructVal
    baseStableBorrowRate: StructVal
    baseVariableBorrowRate: StructVal
    optimalUsageRatio: StructVal
    isPaused: StructVal
    isSiloedBorrowing: StructVal
    accruedToTreasury: StructVal
    unbacked: StructVal
    isolationModeTotalDebt: StructVal
    flashLoanEnabled: StructVal
    debtCeiling: StructVal
    debtCeilingDecimals: StructVal
    eModeCategoryId: StructVal
    borrowCap: StructVal
    supplyCap: StructVal
    eModeLtv: StructVal
    eModeLiquidationThreshold: StructVal
    eModeLiquidationBonus: StructVal
    eModePriceSource: StructVal
    eModeLabel: StructVal
    borrowableInIsolation: StructVal


class BaseCurrencyInfo(TypedDict):
    """Rough mapping of BaseCurrencyInfo in Aave v3 Solidity source code.

    Aave internally gets this data from ChainLink feed.
    """

    marketReferenceCurrencyUnit: StructVal
    marketReferenceCurrencyPriceInUsd: StructVal
    networkBaseTokenPriceInUsd: StructVal
    networkBaseTokenPriceDecimals: StructVal


class JSONSerialisableReserveData(TypedDict):
    """JSON friendly way to store Aave v3 protocol reserve status.

    All ints are converted to JavaScript to avoid BigInt issues.

    .. note ::

        This data is not useful until JavaScript based formatters from
        aave-utilities are applied. As writing of this, these formatters are only
        available as undocumented JavaScript code in this repository.
        `See the repository for more information <https://github.com/aave/aave-utilities>`__.

    """

    #: Which chain this was one
    chain_id: int

    #: When this fetch was performed
    block_number: int

    #: When this fetch was performed
    block_hash: str

    #: Unix timestamp when this fetch was performed
    timestamp: int

    #: ERC-20 address -> reserve info mapping.
    #:
    #: All addresses are lowercased 0x strings
    reserves: Dict[str, AggregatedReserveData]

    #: Chainlink currency conversion multipliers
    #: needed by aave-utilities to convert values to USD/useful/human-readable
    #:
    base_currency_info: BaseCurrencyInfo


def get_helper_contracts(web3: Web3) -> HelperContracts:
    """Get helper contracts need to read Aave reserve data.

    :raise AaveContractsNotConfigured:
        If we do not have labelled addresses for this chain
    """
    chain_id = web3.eth.chain_id

    if chain_id not in _addresses:
        raise AaveContractsNotConfigured(f"Chain {chain_id} does not have Aave v3 addresses configured")

    deployer = AaveDeployer()
    Contract = deployer.get_contract(web3, "UiPoolDataProviderV3.json")
    Contract.decode_tuples = False
    ui_pool_data_provider = Contract(Web3.to_checksum_address(_addresses[chain_id]["UiPoolDataProviderV3"]))

    Contract = deployer.get_contract(web3, "PoolAddressesProvider.json")
    Contract.decode_tuples = False
    pool_addresses_provider = Contract(Web3.to_checksum_address(_addresses[chain_id]["PoolAddressProvider"]))
    return HelperContracts(
        chain_id,
        ui_pool_data_provider,
        pool_addresses_provider,
    )


def fetch_reserves(
    contracts: HelperContracts,
    block_identifier=None,
) -> List[str]:
    """Enumerate available reserves.

    https://github.com/aave/aave-v3-core/blob/27a6d5c83560694210849d4abf09a09dec8da388/contracts/interfaces/IPool.sol#L603

    :return:
        Returns the list of the underlying assets of all the initialized reserves.

        List of ERC-20 addresses.
    """
    reserve_list = contracts.ui_pool_data_provider.functions.getReservesList(contracts.pool_addresses_provider.address).call(block_identifier=block_identifier)
    return reserve_list


def fetch_reserve_data(
    contracts: HelperContracts,
    block_identifier=None,
) -> Tuple[List[AggregatedReserveData], BaseCurrencyInfo]:
    """Fetch data for all reserves.

    :param contracts:
        Helper contracts needed to pull the data

    :return:
        List of data of all reserves, currency data from ChainLink used to convert this info for display

    """
    func = contracts.ui_pool_data_provider.functions.getReservesData(contracts.pool_addresses_provider.address)
    aggregated_reserve_data, base_currency_info = func.call(block_identifier=block_identifier)

    # Manually decode anonymous tuples to named struct fields
    outputs = func.abi["outputs"]
    AggregatedReserveData = outputs[0]["components"]
    BaseCurrencyInfo = outputs[1]["components"]

    aggregated_reserve_data_decoded = [named_tree(AggregatedReserveData, a) for a in aggregated_reserve_data]
    base_currency_info_decoded = named_tree(BaseCurrencyInfo, base_currency_info)
    return aggregated_reserve_data_decoded, base_currency_info_decoded


def fetch_aave_reserves_snapshot(web3: Web3, block_identifier=None) -> JSONSerialisableReserveData:
    """Get a snapshot of all data of Aave reserves at a certain point of time.

    See :py:class:`JSONSerialisableReserveData` for notes on how to transform the output
    to useful and/or human readable.

    Example:

    .. code-block:: python

        # Read Polygon Aave v3 reserves data at current block
        snapshot = fetch_aave_reserves_snapshot(web3)

    Example output:

    .. code-block:: text

        {'block_number': 46092890,
         'block_hash': '0x66b91e13e66978632d7687fa37d61994a092194dd83ab800c4b3fbbfbbc4b882',
         'timestamp': 1691574096,
         'reserves': {'0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063': {'underlyingAsset': '0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063',
           'name': '(PoS) Dai Stablecoin',
           'symbol': 'DAI',
           'decimals': '18',
           'baseLTVasCollateral': '7600',
           'reserveLiquidationThreshold': '8100',
           'reserveLiquidationBonus': '10500',
           'reserveFactor': '1000',
           'usageAsCollateralEnabled': True,
           'borrowingEnabled': True,
           'stableBorrowRateEnabled': True,
           'isActive': True,
           'isFrozen': False,
           'liquidityIndex': '1022026858597482843618393800',
           'variableBorrowIndex': '1039320957656647363864994430',
           'liquidityRate': '28850861922310792585422606',
           'variableBorrowRate': '39579583454495318816309720',
           'stableBorrowRate': '54947447931811914852038715',
           'lastUpdateTimestamp': '1691574072',
           'aTokenAddress': '0x82E64f49Ed5EC1bC6e43DAD4FC8Af9bb3A2312EE',
           'stableDebtTokenAddress': '0xd94112B5B62d53C9402e7A60289c6810dEF1dC9B',
           'variableDebtTokenAddress': '0x8619d80FB0141ba7F184CbF22fd724116D9f7ffC',
           'interestRateStrategyAddress': '0xA9F3C3caE095527061e6d270DBE163693e6fda9D',
           'availableLiquidity': '1889483036044495898670614',
           'totalPrincipalStableDebt': '411830124610128093102375',
           'averageStableRate': '55554322387136738659305167',
           'stableDebtLastUpdateTimestamp': '1691573968',
           'totalScaledVariableDebt': '6509001421349391268535081',
           'priceInMarketReferenceCurrency': '99970000',
           'priceOracle': '0x4746DeC9e833A82EC7C2C1356372CcF2cfcD2F3D',
           'variableRateSlope1': '40000000000000000000000000',
           'variableRateSlope2': '750000000000000000000000000',
           'stableRateSlope1': '5000000000000000000000000',
           'stableRateSlope2': '750000000000000000000000000',
           'baseStableBorrowRate': '50000000000000000000000000',
           'baseVariableBorrowRate': '0',
           'optimalUsageRatio': '800000000000000000000000000',
           'isPaused': False,
           'isSiloedBorrowing': False,
           'accruedToTreasury': '142442743829638527556',
           'unbacked': '0',
           'isolationModeTotalDebt': '0',
           'flashLoanEnabled': True,
           'debtCeiling': '0',
           'debtCeilingDecimals': '2',
           'eModeCategoryId': '1',
           'borrowCap': '30000000',
           'supplyCap': '45000000',
           'eModeLtv': '9300',
           'eModeLiquidationThreshold': '9500',
           'eModeLiquidationBonus': '10100',
           'eModePriceSource': '0x0000000000000000000000000000000000000000',
           'eModeLabel': 'Stablecoins',
           'borrowableInIsolation': True},
          '0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39': {'underlyingAsset': '0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39',
           'name': 'ChainLink Token',
           'symbol': 'LINK',
           'decimals': '18',


    :param web3:
        Web3 connection for some of the chain for which we have Aave v3 contract data available.

    :param block_identifier:
        Block when to take the snapshot.

        If not given, use the latest block.

    :return:
        JSON friendly dict where all ints are converted to string
    """

    helpers = get_helper_contracts(web3)

    if block_identifier is None:
        block_identifier = web3.eth.block_number

    block = web3.eth.get_block(block_identifier)

    aggregated_reserve_data, base_currency_info = fetch_reserve_data(helpers, block_identifier=block_identifier)

    reserve_map = {a["underlyingAsset"].lower(): _to_json_friendly(a) for a in aggregated_reserve_data}

    return JSONSerialisableReserveData(
        chain_id=helpers.chain_id,
        block_number=convert_jsonrpc_value_to_int(block["number"]),
        block_hash=block["hash"].hex(),
        timestamp=convert_jsonrpc_value_to_int(block["timestamp"]),
        reserves=reserve_map,
        base_currency_info=base_currency_info,
    )


def _to_json_friendly(d: dict) -> dict:
    """Deal with JavaScript lacking of good number types"""
    result = {}
    for k, v in d.items():
        if type(v) == int:
            v = str(v)
        result[k] = v
    return result
