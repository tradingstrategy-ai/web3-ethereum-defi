"""ERC-20 token deployment and manipulation.

Deploy ERC-20 tokens to be used within your test suite.

`Read also unit test suite for tokens to see how ERC-20 can be manipulated in pytest <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/tests/test_token.py>`_.
"""

import datetime
import json
import logging
import os
import warnings
from collections import OrderedDict, defaultdict
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from functools import cached_property
from pathlib import Path
from typing import Any, Iterable, Optional, TypeAlias, TypedDict, Union

import cachetools
from web3.contract.contract import ContractFunction, ContractFunctions

from eth_defi.compat import native_datetime_utc_now
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int, convert_solidity_bytes_to_string
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult, read_multicall_chunked
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.provider.named import get_provider_name
from eth_defi.sqlite_cache import PersistentKeyValueStore

with warnings.catch_warnings():
    # DeprecationWarning: pkg_resources is deprecated as an API. See https://setuptools.pypa.io/en/latest/pkg_resources.html
    warnings.simplefilter("ignore")
    try:
        from eth_tester.exceptions import TransactionFailed
    except ImportError:
        # New Web3.py versions got rid of this?
        # Mock here
        class TransactionFailed(Exception):
            pass


from eth_typing import HexAddress
from requests.exceptions import ReadTimeout
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import deploy_contract
from eth_defi.utils import sanitise_string

logger = logging.getLogger(__name__)

#: List of exceptions JSON-RPC provider can through when ERC-20 field look-up fails
#: TODO: Add exceptios from real HTTPS/WSS providers
#: `ValueError` is raised by Ganache
_call_missing_exceptions = (TransactionFailed, BadFunctionCallOutput, ValueError, ContractLogicError)

#: By default we cache 1024 token details using LRU in the process memory.
#:
DEFAULT_TOKEN_CACHE = cachetools.LRUCache(1024)

#: ERC-20 address, 0x prefixed string
TokenAddress: TypeAlias = str


#: Addresses of wrapped native token (WETH9) of different chains
WRAPPED_NATIVE_TOKEN: dict[int, HexAddress | str] = {
    # Mainnet
    1: "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    # Base
    8453: "0x4200000000000000000000000000000000000006",
    # WBNB
    56: "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
    # WETH: Arbitrum
    42161: "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
    # WAVAX
    43114: "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7",
    # WETH: Arbitrum Sepolia
    421614: "0x7b79995e5f793A07Bc00c21412e50Ecae098E7f9",
}

#: Addresses of USDC of different chains
USDC_NATIVE_TOKEN: dict[int, HexAddress | str] = {
    # Mainnet
    1: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    # Base
    8453: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    # Ava
    43114: "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
    # Arbitrum
    42161: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    # BNB
    # https://www.coingecko.com/en/coins/binance-bridged-usdc-bnb-smart-chain
    56: "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
    # Arbitrum Sepolia
    421614: "0x75faf114eafb1BDbe2F0316DF893fd58CE46AA4d",
}

#: Bridged USDC of different chains
BRIDGED_USDC_TOKEN: dict[int, HexAddress | str] = {
    # Arbitrum
    42161: "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8",
}


#: Used in fork testing
USDC_WHALE: dict[int, HexAddress | str] = {
    # Base
    #
    8453: "0x40EbC1Ac8d4Fedd2E144b75fe9C0420BE82750c6",
    # Arbitrum
    # Coinbase 10
    # https://arbiscan.io/token/0xaf88d065e77c8cc2239327c5edb3a432268e5831#balances
    42161: "0x3DD1D15b3c78d6aCFD75a254e857Cbe5b9fF0aF2",
}

#: Used in fork testing
USDT_WHALE: dict[int, HexAddress | str] = {
    # BNB Chain
    # https://bscscan.com/token/0x55d398326f99059ff775485246999027b3197955#balances
    56: Web3.to_checksum_address("0x128463A60784c4D3f46c23Af3f65Ed859Ba87974"),
    # Arbitrum
    # https://arbiscan.io/token/0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9#balances
    42161: "0x9E36CB86a159d479cEd94Fa05036f235Ac40E1d5",
}

#: Addresses USDT Tether of different chains
USDT_NATIVE_TOKEN: dict[int, HexAddress] = {
    # Mainnet
    1: "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    56: "0x55d398326f99059fF775485246999027B3197955",
    # Avalanche USDT.E
    43114: "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7",
    # Arbitrum
    42161: "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
}


#: Sky (MakerDAO) new tokens
SUSDS_NATIVE_TOKEN: dict[int, HexAddress] = {
    # Base
    8453: "0x5875eEE11Cf8398102FdAd704C9E96607675467a",
}

#: Berachain
#: 0xFCBD14DC51f0A4d49d5E53C2E0950e0bC26d0Dce
#: https://docs.berachain.com/learn/pol/tokens/honey
HONEY_NATIVE_TOKEN: dict[int, HexAddress] = {
    # Berachain
    80094: "0xFCBD14DC51f0A4d49d5E53C2E0950e0bC26d0Dce",
}


#: Token symbols that are stablecoin like.
#: Note that it is *not* safe to to check the token symbol to know if a token is a specific stablecoin,
#: but you always need to check the contract address.
#: Checking against this list only works
#: USDf and USDF
STABLECOIN_LIKE = set(
    [
        "ALUSD",
        "AUDT",
        "AUSD",
        "BAC",
        "BDO",
        "BEAN",
        "BOB",
        "BOLD",
        "BUSD",
        "BYUSD",
        "CADC",
        "CEUR",
        "CJPY",
        "CNHT",
        "CRVUSD",
        "CUSD",
        "DAI",
        "DJED",
        "DOLADUSD",
        "EOSDT",
        "EURA",
        "EURCV",
        "EUROC",
        "EUROe",
        "EURS",
        "EURT",
        "EURe",
        "EUSD",
        "FDUSD",
        "FEI",
        "FLEXUSD",
        "FUSD",
        "FXD",
        "FXUSD",
        "GBPT",
        "GHO",
        "GHST",
        "GUSD",
        "GYD",
        "GYEN",
        "HAI",
        "HUSD",
        "IRON",
        "JCHF",
        "JPYC",
        "KDAI",
        "LISUSD",
        "LUSD",
        "MIM",
        "MIMATIC",
        "MKUSD",
        "MUSD",
        "ONC",
        "OUSD",
        "PAR",
        "PAXG",
        "PYUSD",
        "RAI",
        "RLUSD",
        "RUSD",
        "SAI",
        "SDAI",
        "SEUR",
        "SFRAX",
        "SILK",
        "STUSD",
        "SUSD",
        "TCNH",
        "TOR",
        "TRYB",
        "TUSD",
        "USC",
        "USD+",
        "USD0",
        "USD1",
        "USD8",
        "USDA",
        "USDB",
        "USDC",
        "USDC.e",
        "USDCV",
        "USDD",
        "USDE",
        "USDF",
        "USDH",
        "USDHL",
        "USDM",
        "USDN",
        "USDO",
        "USDP",
        "USDR",
        "USDS",
        "USDT",
        "USDT.e",
        "USDT0",
        "USDV",
        "USDX",
        "USDXL",
        "USDai",
        "USDbC",
        "USDe",
        "USDf",
        "USDs",
        "USDt",
        "USD₮0",
        "USH",
        "USK",
        "USR",
        "UST",
        "USTC",
        "USX",
        "USXAU",
        "UTY",
        "UUSD",
        "VAI",
        "VEUR",
        "VST",
        "VUSD",
        "WXDAI",
        "XAUT",
        "XDAI",
        "XIDR",
        "XSGD",
        "XSTUSD",
        "XUSD",
        "YUSD",
        "ZCHF",
        "ZSD",
        "ZUSD",
        "avUSD",
        "bvUSD",
        "crvUSD",
        "dUSD",
        "deUSD",
        "frxUSD",
        "ftUSD",
        "gmUSD",
        "iUSD",
        "jEUR",
        "kUSD",
        "lvlUSD",
        "mUSD",
        "meUSDT",
        "msUSD",
        "plUSD",
        "reUSD",
        "sUSDC",
        "satUSD",
        "scUSD",
        "sosUSDT",
        "vbUSDC",
        "vbUSDT",
        "wM",
        "xUSD",
    ]
)


#: Stablecoins which can be used as collateral, but which also have built-in yield bearing function
#: with rebasing.
YIELD_BEARING_STABLES = {"sfrxUSD", "sUSDe", "sUSDai", "sBOLD"}

#: Stablecoins plus their interest wrapped counterparts on Compound and Aave.
#: Also contains other derivates.
WRAPPED_STABLECOIN_LIKE = {"cUSDC", "cUSDT", "sUSD", "aDAI", "cDAI", "tfUSDC", "alUSD", "agEUR", "gmdUSDC", "gDAI", "blUSD"}

#: All stablecoin likes - both interested bearing and non interest bearing.
ALL_STABLECOIN_LIKE = STABLECOIN_LIKE | WRAPPED_STABLECOIN_LIKE | YIELD_BEARING_STABLES


#: Some test accounts with funded USDC for Anvil mainnet forking
#:
#: TBD: In theory we can find ERC-20 balance slots and write value there with Anvil, but
#: it is difficult to do reliably.
LARGE_USDC_HOLDERS = {
    # Arbitrum
    42161: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    # Ave
    43114: "0x9f8c163cBA728e99993ABe7495F06c0A3c8Ac8b9",
    # Base
    # Bybits hot wallet
    # https://basescan.org/token/0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
    8453: "0xBaeD383EDE0e5d9d72430661f3285DAa77E9439F",
}


@dataclass(frozen=True, slots=True)
class DummyPickledContract:
    """Contract placeholder making contract references pickable"""

    address: str


@dataclass
class TokenDetails:
    """ERC-20 token Python presentation.

    - A helper class to work with ERC-20 tokens.

    - Read on-chain data, deal with token value decimal conversions.

    - Any field can be ``None`` for non-well-formed tokens.

    - Supports one-way pickling

    Example how to get USDC details on Polygon:

    .. code-block:: python

        usdc = fetch_erc20_details(web3, "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")  # USDC on Polygon
        formatted = f"Token {usdc.name} ({usdc.symbol}) at {usdc.address} on chain {usdc.chain_id}"
        assert formatted == "Token USD Coin (PoS) (USDC) at 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174 on chain 137"
    """

    #: The underlying ERC-20 contract proxy class instance
    contract: Contract

    #: Token name e.g. ``USD Circle``
    name: Optional[str] = None

    #: Token symbol e.g. ``USDC``
    symbol: Optional[str] = None

    #: Token supply as raw units
    total_supply: Optional[int] = None

    #: Number of decimals
    decimals: Optional[int] = None

    #: Extra metadata, e.g. related to caching this result
    extra_data: dict[str, Any] = field(default_factory=dict)

    def __eq__(self, other):
        """Token is the same if it's on the same chain and has the same contract address."""
        assert isinstance(other, TokenDetails)
        return (self.contract.address == other.contract.address) and (self.chain_id == other.chain_id)

    def __hash__(self):
        """Token hash."""
        return hash((self.chain_id, self.contract.address))

    def __repr__(self):
        return f"<{self.name} ({self.symbol}) at {self.contract.address}, {self.decimals} decimals, on chain {self.chain_id}>"

    def __getstate__(self):
        """Contract cannot be pickled."""
        state = self.__dict__.copy()
        state["contract"] = DummyPickledContract(address=self.contract.address)
        return state

    def __setstate__(self, state):
        """Contract cannot be pickled."""
        self.__dict__.update(state)

    @cached_property
    def chain_id(self) -> int:
        """The EVM chain id where this token lives."""
        return self.contract.w3.eth.chain_id

    @cached_property
    def address(self) -> HexAddress:
        """The address of this token.

        See also :py:meth:`address_lower`.
        """
        return self.contract.address

    @cached_property
    def address_lower(self) -> HexAddress:
        """The address of this token.

        Always lowercase.
        """
        return self.contract.address.lower()

    @property
    def functions(self) -> ContractFunctions:
        """Alias for underlying Web3 contract method"""
        return self.contract.functions

    def convert_to_decimals(self, raw_amount: int) -> Decimal:
        """Convert raw token units to decimals.

        Example:

        .. code-block:: python

            details = fetch_erc20_details(web3, token_address)
            # Convert 1 wei units to edcimals
            assert details.convert_to_decimals(1) == Decimal("0.0000000000000001")

        """
        assert type(raw_amount) == int, f"Got {type(raw_amount)}, expected int: {raw_amount}"
        return Decimal(raw_amount) / Decimal(10**self.decimals)

    def convert_to_raw(self, decimal_amount: Decimal) -> int:
        """Convert decimalised token amount to raw uint256.

        Example:

        .. code-block:: python

            details = fetch_erc20_details(web3, token_address)
            # Convert 1.0 USDC to raw unit with 6 decimals
            assert details.convert_to_raw(1) == 1_000_000

        """
        return int(decimal_amount * 10**self.decimals)

    def fetch_balance_of(self, address: HexAddress | str, block_identifier="latest") -> Decimal:
        """Get an address token balance.

        :param block_identifier:
            A specific block to query if doing archive node historical queries

        :return:
            Converted to decimal using :py:meth:`convert_to_decimal`
        """
        address = Web3.to_checksum_address(address)
        raw_amount = self.contract.functions.balanceOf(address).call(block_identifier=block_identifier)
        return self.convert_to_decimals(raw_amount)

    def transfer(
        self,
        to: HexAddress | str,
        amount: Decimal,
    ) -> ContractFunction:
        """Prepare a ERC20.transfer() transaction with human-readable amount.

        Example:

        .. code-block:: python

            another_new_depositor = web3.eth.accounts[6]
            tx_hash = base_usdc.transfer(another_new_depositor, Decimal(500)).transact({"from": usdc_holder, "gas": 100_000})
            assert_transaction_success_with_explanation(web3, tx_hash)

        :return:
            Bound contract function you need to turn to a tx
        """
        assert isinstance(amount, Decimal), f"Give amounts in decimal, got {type(amount)}"
        to = Web3.to_checksum_address(to)
        raw_amount = self.convert_to_raw(amount)
        return self.contract.functions.transfer(to, raw_amount)

    def approve(
        self,
        to: HexAddress | str,
        amount: Decimal,
    ) -> ContractFunction:
        """Prepare a ERC20.approve() transaction with human-readable amount.

        Example:

        .. code-block:: python

            usdc_amount = Decimal(9.00)
            tx_hash = usdc.approve(vault.address, usdc_amount).transact({"from": depositor})
            assert_transaction_success_with_explanation(web3, tx_hash)

        :return:
            Bound contract function you need to turn to a tx
        """
        assert isinstance(amount, Decimal), f"Give amounts in decimal, got {type(amount)}"
        to = Web3.to_checksum_address(to)
        raw_amount = self.convert_to_raw(amount)
        return self.contract.functions.approve(to, raw_amount)

    def fetch_raw_balance_of(self, address: HexAddress | str, block_identifier="latest") -> Decimal:
        """Get an address token balance.

        :param block_identifier:
            A specific block to query if doing archive node historical queries

        :return:
            Raw token amount.
        """
        address = Web3.to_checksum_address(address)
        raw_amount = self.contract.functions.balanceOf(address).call(block_identifier=block_identifier)
        return raw_amount

    @staticmethod
    def generate_cache_key(chain_id: int, address: str) -> str:
        """Generate a cache key for this token.

        - Cached by (chain, address) as a string

        - Validate the inputs before generating the key

        - Address is always lowercase

        :return:
            Human reaadable {chain_id}-{address}
        """
        assert type(chain_id) == int, f"Bad chain id: {chain_id}"
        assert type(address) == str
        assert address.startswith("0x"), f"Bad token address: {address}"
        return f"{chain_id}-{address.lower()}"

    def export(self) -> dict:
        """Create a serialisable entry of this class.

        Removes web3 connection and such unserialisable data.

        :return:
            Python dict of exported data.
        """
        clone = dict(**self.__dict__)
        clone["address"] = self.address
        clone["chain"] = self.chain_id
        del clone["contract"]
        return clone

    def is_stablecoin_like(self) -> bool:
        """Smell test for stablecoins.

        - Symbol check for common stablecoins
        - Not immune to scams
        - For the list see :py:func:`is_stablecoin_like`

        :return:
            True if we think this could be a stablecoin.
        """
        return is_stablecoin_like(self.symbol)


class TokenDetailError(Exception):
    """Cannot extract token details for an ERC-20 token for some reason."""


def create_token(
    web3: Web3,
    deployer: str,
    name: str,
    symbol: str,
    supply: int,
    decimals: int = 18,
) -> Contract:
    """Deploys a new ERC-20 token on local dev, testnet or mainnet.

    - Uses `ERC20Mock <https://github.com/sushiswap/sushiswap/blob/canary/contracts/mocks/ERC20Mock.sol>`_ contract for the deployment.

    - Waits until the transaction has completed

    Example:

    .. code-block::

        # Deploys an ERC-20 token where 100,000 tokens are allocated ato the deployer address
        token = create_token(web3, deployer, "Hentai books token", "HENTAI", 100_000 * 10**18)
        print(f"Deployed token contract address is {token.address}")
        print(f"Deployer account {deployer} has {token.functions.balanceOf(user_1).call() / 10**18} tokens")

    Find more examples in :ref:`tutorials` and unit testing source code.

    :param web3:
        Web3 instance

    :param deployer:
        Deployer account as 0x address.

        Make sure this account has enough ETH or native token to cover the gas cost.

    :param name: Token name

    :param symbol: Token symbol

    :param supply: Token starting supply as raw units.

        E.g. ``500 * 10**18`` to have 500 tokens minted to the deployer
        at the start.

    :param decimals: How many decimals ERC-20 token values have

    :return:
        Instance to a deployed Web3 contract.
    """
    return deploy_contract(web3, "ERC20MockDecimals.json", deployer, name, symbol, supply, decimals)


def get_erc20_contract(
    web3: Web3,
    address: HexAddress,
    contract_name="ERC20MockDecimals.json",
) -> Contract:
    """Wrap address as ERC-20 standard interface."""
    return get_deployed_contract(web3, contract_name, address)


def fetch_erc20_details(
    web3: Web3,
    token_address: Union[HexAddress, str],
    max_str_length: int = 256,
    raise_on_error=True,
    contract_name="ERC20MockDecimals.json",
    cache: dict | None = DEFAULT_TOKEN_CACHE,
    chain_id: int = None,
    cause_diagnostics_message: str | None = None,
) -> TokenDetails:
    """Read token details from on-chain data.

    Connect to Web3 node and do RPC calls to extract the token info.
    We apply some sanitazation for incoming data, like length checks and removal of null bytes.

    The function should not raise an exception as long as the underlying node connection does not fail.

    .. note ::

        Always give ``chain_id`` when possible. Otherwise the caching of data is inefficient.

    Example:

    .. code-block:: python

        details = fetch_erc20_details(web3, token_address)
        assert details.name == "Hentai books token"
        assert details.decimals == 6

    :param web3:
        Web3 instance

    :param token_address:
        ERC-20 contract address:

    :param max_str_length:
        For input sanitisation

    :param raise_on_error:
        If set, raise `TokenDetailError` on any error instead of silently ignoring in and setting details to None.

    :param contract_name:
        Contract ABI file to use.

        The default is ``ERC20MockDecimals.json``. For USDC use ``centre/FiatToken.json``.

    :param cache:
        Use this cache for cache token detail calls.

        The main purpose is to easily reduce JSON-RPC API call count.

        By default, we use LRU cache of 1024 entries.

        Set to ``None`` to disable the cache.

        Instance of :py:class:`cachetools.Cache'.
        See `cachetools documentation for details <https://cachetools.readthedocs.io/en/latest/#cachetools.LRUCache>`__.

    :param chain_id:
        Chain id hint for the cache.

        If not given do ``eth_chainId`` RPC call to figure out.

    :param cause_diagnostics_message:
        Log in Python logging subsystem why this fetch was done to debug RPC overuse.

    :return:
        Sanitised token info
    """

    if not chain_id:
        chain_id_given = False
        chain_id = web3.eth.chain_id
    else:
        chain_id_given = True

    erc_20 = get_erc20_contract(web3, token_address, contract_name)

    key = TokenDetails.generate_cache_key(chain_id, token_address)

    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return TokenDetails(
                erc_20,
                cached["name"],
                cached["symbol"],
                cached["supply"],
                cached["decimals"],
                extra_data={"cached": True},
            )

    logger.info(
        "Fetching uncached token, chain %s, address %s, chain id given: %s, reason: %s, token cache %s has %d entries",
        chain_id,
        token_address,
        chain_id_given,
        cause_diagnostics_message,
        cache.__class__.__name__,
        len(cache) if cache is not None else -1,
    )

    try:
        try:
            if chain_id == 42161 and token_address.lower() == "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8":
                # Legacy USDC on Arbitrum.
                # The contract still returns the old symbol, but Arbiscan and others show USDC.e,
                # and because this is widespread we do this hack override here.
                symbol = "USDC.e"
            else:
                raw_resp = erc_20.functions.symbol().call()
                symbol = sanitise_string(raw_resp[0:max_str_length])
        except BadFunctionCallOutput as e:
            # ABI mismatch
            # MakerDAO f*** yeah
            # *** web3.exceptions.BadFunctionCallOutput: Could not decode contract function call to symbol() with return data: b'MKR\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00', output_types: ['string']
            msg = str(e)

            start = msg.find("b'") + 2
            end = msg.find("\\x00", start)
            if end != -1:
                value = msg[start:end]
                symbol = value
            else:
                raise

    except ReadTimeout as e:
        # Handle this specially because Anvil is piece of hanging shit
        # and we need to manually clean up these all the time
        provider_name = get_provider_name(web3.provider)
        raise TokenDetailError(f"Token {token_address} timeout reading on chain {chain_id}: {e}, provider {provider_name}") from e
    except _call_missing_exceptions as e:
        if raise_on_error:
            raise TokenDetailError(f"Token {token_address} missing symbol on chain {chain_id}: {e}") from e
        symbol = None
    except OverflowError:
        # OverflowError: Python int too large to convert to C ssize_t
        # Que?
        # Sai Stablecoin uses bytes32 instead of string for name and symbol information
        # https://etherscan.io/address/0x89d24a6b4ccb1b6faa2625fe562bdd9a23260359#readContract
        symbol = None

    try:
        name = sanitise_string(erc_20.functions.name().call()[0:max_str_length])
    except BadFunctionCallOutput as e:
        # ABI mismatch
        # MakerDAO f*** yeah
        # *** web3.exceptions.BadFunctionCallOutput: Could not decode contract function call to symbol() with return data: b'MKR\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00', output_types: ['string']
        msg = str(e)

        start = msg.find("b'") + 2
        end = msg.find("\\x00", start)
        if end != -1:
            value = msg[start:end]
            name = value
        else:
            if raise_on_error:
                raise
            else:
                name = None
    except _call_missing_exceptions as e:
        if raise_on_error:
            raise TokenDetailError(f"Token {token_address} missing name: {e}") from e
        name = None
    except OverflowError:
        # OverflowError: Python int too large to convert to C ssize_t
        # Que?
        # Sai Stablecoin uses bytes32 instead of string for name and symbol information
        # https://etherscan.io/address/0x89d24a6b4ccb1b6faa2625fe562bdd9a23260359#readContract
        name = None

    try:
        decimals = erc_20.functions.decimals().call()
    except _call_missing_exceptions as e:
        if raise_on_error:
            raise TokenDetailError(f"Token {token_address} missing decimals") from e
        decimals = 0

    try:
        supply = erc_20.functions.totalSupply().call()
    except _call_missing_exceptions as e:
        if raise_on_error:
            raise TokenDetailError(f"Token {token_address} missing totalSupply") from e
        supply = None

    token_details = TokenDetails(erc_20, name, symbol, supply, decimals, extra_data={"cached": False})
    if cache is not None:
        cache[key] = {
            "name": name,
            "symbol": symbol,
            "supply": supply,
            "decimals": decimals,
        }
    return token_details


def reset_default_token_cache():
    """Purge the cached token data.

    See :py:data:`DEFAULT_TOKEN_CACHE`
    """
    global DEFAULT_TOKEN_CACHE
    # Cache has a horrible API
    DEFAULT_TOKEN_CACHE.__dict__["_LRUCache__order"] = OrderedDict()
    DEFAULT_TOKEN_CACHE.__dict__["_Cache__currsize"] = 0
    DEFAULT_TOKEN_CACHE.__dict__["_Cache__data"] = dict()


def get_wrapped_native_token_address(chain_id: int):
    address = WRAPPED_NATIVE_TOKEN.get(chain_id)
    assert address, f"Chain id {chain_id} not found"
    return address


def get_chain_stablecoins(chain_id: int) -> set[TokenDetails]:
    """Get all good known stablecoins on a chain.

    :raise AssertionError:
        Chain has zero stablecoins configured
    """

    assert type(chain_id) is int

    tokens = set()
    usdc = USDC_NATIVE_TOKEN.get(chain_id)
    if usdc is not None:
        tokens.add(usdc)

    usdt = USDT_NATIVE_TOKEN.get(chain_id)
    if usdt is not None:
        tokens.add(usdt)

    susds = SUSDS_NATIVE_TOKEN.get(chain_id)
    if susds is not None:
        tokens.add(susds)

    honey = HONEY_NATIVE_TOKEN.get(chain_id)
    if honey is not None:
        tokens.add(honey)

    assert len(tokens) > 0, f"Zero known good stablecoins configured for chain {chain_id}"
    return tokens


def get_chain_known_quote_tokens(chain_id: int) -> set[TokenDetails]:
    """Get all good quote tokens on  chain."""
    pass


def is_stablecoin_like(token_symbol: str | None, symbol_list=ALL_STABLECOIN_LIKE) -> bool:
    """Check if specific token symbol is likely a stablecoin.

    Useful for quickly filtering stable/stable pairs in the pools.
    However, you should never rely on this check alone.

    Note that new stablecoins might be introduced, so this check
    is never going to be future proof.

    Example:

    .. code-block:: python

        assert is_stablecoin_like("USDC") == True
        assert is_stablecoin_like("USDT") == True
        assert is_stablecoin_like("GHO") == True
        assert is_stablecoin_like("crvUSD") == True
        assert is_stablecoin_like("WBTC") == False

    :param token_symbol:
        Token symbol as it is written on the contract.
        May contain lower and uppercase latter.

    :param symbol_list:
        Which filtering list we use.
    """

    if token_symbol is None:
        return False

    assert isinstance(token_symbol, str), f"We got {token_symbol}"
    return token_symbol in symbol_list


def normalise_token_symbol(token_symbol: str | None) -> str | None:
    """Normalise token symbol for stablecoin detection.

    - Uppercase
    - Remove bridge suffixes
    - Fix USDT variations

    :param token_symbol:
        Token symbol as it is written on the contract.

    :return:
        Normalised token symbol
    """

    if token_symbol is None:
        return None

    assert isinstance(token_symbol, str), f"We got {token_symbol}"

    token_symbol = token_symbol.upper()

    if token_symbol.endswith(".E"):
        token_symbol = token_symbol.removesuffix(".E")

    if token_symbol in {"USDT0", "USD₮0"}:
        token_symbol = "USDT"

    return token_symbol


def get_weth_contract(web3: Web3, name: str = "1delta/IWETH9.json") -> Contract:
    """Get WETH9 contract for the chain

    - `See WETH9 contract <https://www.contractreader.io/contract/mainnet/0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2>`__
    - WETH9 is different contract with different functions on different chain

    :param web3:
        Web3 instance

    :param name:
        Alternative implementation.

    :return:
        WETH token details
    """
    chain_id = web3.eth.chain_id
    weth_address = get_wrapped_native_token_address(chain_id)
    return get_deployed_contract(
        web3,
        name,
        weth_address,
    )


class TokenCacheWarmupResult(TypedDict):
    tokens_read: int
    multicalls_done: int


class TokenDiskCache(PersistentKeyValueStore):
    """Token cache that stores tokens in disk.

    - Use with :py:func:`fetch_erc20_details`
    - For loading hundreds of tokens once
    - Shared across chains
    - Enable fast cache warmup with :py:meth:`load_token_details_with_multicall`
    - Persistent: Make sure subsequent batch jobs do not refetch token data over RPC as it is expensive
    - Store as a SQLite database

    Example:

    .. code-block:: python

        addresses = [
            "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # USDC
            "0x4200000000000000000000000000000000000006",  # WETH
            "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",  # DAI
            "0x554a1283cecca5a46bc31c2b82d6702785fc72d9",  # UNI
        ]

        cache = TokenDiskCache(tmp_path / "disk_cache.sqlite")
        web3factory = MultiProviderWeb3Factory(JSON_RPC_BASE)
        web3 = web3factory()

        #
        # Do single token lookups against cache
        #
        token = fetch_erc20_details(
            web3,
            token_address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            chain_id=web3.eth.chain_id,
            cache=cache,
        )
        assert token.extra_data["cached"] == False
        assert len(cache) == 1
        # After one look up, we should have it cached
        token = fetch_erc20_details(
            web3,
            token_address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            chain_id=web3.eth.chain_id,
            cache=cache,
        )
        assert token.extra_data["cached"] == True
        cache.purge()

        #
        # Warm up multiple on dry cache
        #
        result = cache.load_token_details_with_multicall(
            chain_id=web3.eth.chain_id,
            web3factory=web3factory,
            addresses=addresses,
            max_workers=max_workers,
            display_progress=False,
        )
        assert result["tokens_read"] == 4
        assert "8453-0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913".lower() in cache
        assert "8453-0x4200000000000000000000000000000000000006".lower() in cache

        cache_data = cache["8453-0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913".lower()]
        assert cache_data["name"] == "USD Coin"
        assert cache_data["symbol"] == "USDC"
        assert cache_data["decimals"] == 6
        assert cache_data["supply"] > 1_000_000

        token = fetch_erc20_details(
            web3,
            token_address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            chain_id=web3.eth.chain_id,
            cache=cache,
        )
        assert token.extra_data["cached"] == True
    """

    DEFAULT_TOKEN_DISK_CACHE_PATH = Path("~/.cache/eth-defi-tokens.sqlite")

    def __init__(
        self,
        filename=DEFAULT_TOKEN_DISK_CACHE_PATH,
        max_str_length: int = 256,
    ):
        assert isinstance(filename, Path), f"We got {filename}"
        filename = filename.expanduser()
        dirname = filename.parent
        os.makedirs(dirname, exist_ok=True)
        self.max_str_length = max_str_length
        super().__init__(filename)

    def __repr__(self):
        return f"<TokenDiskCache file={self.filename} entries={len(self)}>"

    def encode_value(self, value: dict) -> Any:
        value["saved_at"] = native_datetime_utc_now().isoformat()
        return json.dumps(value)

    def decode_value(self, value: str) -> Any:
        return json.loads(value)

    def encode_multicalls(self, address: HexAddress) -> EncodedCall:
        """Generate multicalls for each token address"""

        symbol_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="symbol()")[0:4],
            function="symbol",
            data=b"",
            extra_data=None,
        )
        yield symbol_call

        name_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="name()")[0:4],
            function="name",
            data=b"",
            extra_data=None,
        )
        yield name_call

        decimals_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="decimals()")[0:4],
            function="decimals",
            data=b"",
            extra_data=None,
        )
        yield decimals_call

        total_supply = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="totalSupply()")[0:4],
            function="totalSupply",
            data=b"",
            extra_data=None,
        )
        yield total_supply

    def generate_calls(self, chain_id: int, addresses: list[HexAddress]) -> Iterable[EncodedCall]:
        for address in addresses:
            cache_key = TokenDetails.generate_cache_key(chain_id, address)
            if cache_key not in self:
                yield from self.encode_multicalls(address)
            else:
                logger.debug("Was already cached: %s", address)

    def create_cache_entry(self, call_results: dict[str, EncodedCallResult]) -> dict:
        """Map multicall results to token details data for one address"""
        entry = {}

        symbol_result = call_results["symbol"]
        entry["address"] = symbol_result.call.address
        if symbol_result.success and len(symbol_result.result) > 0:
            entry["symbol"] = convert_solidity_bytes_to_string(symbol_result.result, self.max_str_length)
        else:
            entry["symbol"] = None

        name_result = call_results["name"]
        if name_result.success and len(name_result.result) > 0:
            entry["name"] = convert_solidity_bytes_to_string(name_result.result, self.max_str_length)
        else:
            entry["name"] = None

        decimals_result = call_results["decimals"]
        if decimals_result.success:
            entry["decimals"] = convert_int256_bytes_to_int(decimals_result.result)
        else:
            entry["decimals"] = 0

        total_supply_result = call_results["totalSupply"]
        if total_supply_result.success:
            entry["supply"] = convert_int256_bytes_to_int(total_supply_result.result)
        else:
            entry["supply"] = None

        # A poisoned token that blows up stuff and
        # makes JSON serialisation impossible

        def _cap(x, _max=2**256):
            if type(x) == int:
                return min(x, _max)
            return x

        entry["decimals"] = _cap(entry["decimals"], _max=99)
        entry["supply"] = _cap(entry["supply"])

        return entry

    def load_token_details_with_multicall(
        self,
        chain_id: int,
        web3factory: Web3Factory,
        addresses: list[HexAddress],
        display_progress: str | bool = False,
        max_workers=8,
        block_identifier="latest",
        checkpoint: int = 32,
    ) -> TokenCacheWarmupResult:
        """Warm up cache and load token details for multiple"""

        assert type(chain_id) == int, "chain_id must be an integer"
        assert type(addresses) == list, "addresses must be a list of HexAddress"

        if type(display_progress) == str:
            progress_bar_desc = display_progress
        else:
            progress_bar_desc = f"Loading token metadata for {len(addresses)} addresses using {max_workers} workers"

        logger.info(f"Loading token metadata for {len(addresses)} addresses using {max_workers} workers")

        encoded_calls = list(self.generate_calls(chain_id, addresses))
        multicalls_done = len(encoded_calls)

        # Temporary work buffer were we count that all calls to the address have been made,
        # because results are dropping in one by one
        results_per_address: dict[HexAddress, dict] = defaultdict(dict)

        for call_result in read_multicall_chunked(
            chain_id,
            web3factory,
            encoded_calls,
            block_identifier=block_identifier,
            progress_bar_desc=progress_bar_desc,
            max_workers=max_workers,
        ):
            results_per_address[call_result.call.address][call_result.call.func_name] = call_result

        tokens_read = 0

        for address, result_per_address in results_per_address.items():
            cache_entry = self.create_cache_entry(result_per_address)
            key = TokenDetails.generate_cache_key(chain_id, address)
            cache_entry["chain_id"] = chain_id
            try:
                self[key] = cache_entry
            except ValueError as e:
                raise ValueError(f"Could not cache token {address} on chain {chain_id}: {e}, data: {cache_entry}") from e
            tokens_read += 1

            if tokens_read % checkpoint == 0:
                self.commit()

        logger.info(
            "Read %d tokens for chain %d with %d multicalls ",
            tokens_read,
            chain_id,
            multicalls_done,
        )

        self.commit()

        return TokenCacheWarmupResult(
            tokens_read=tokens_read,
            multicalls_done=multicalls_done,
        )
