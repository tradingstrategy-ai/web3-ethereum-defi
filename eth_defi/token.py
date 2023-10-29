"""ERC-20 token information, deployment and manipulation.

Deploy ERC-20 tokens to be used within your test suite.

`Read also unit test suite for tokens to see how ERC-20 can be manipulated in pytest <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/tests/test_token.py>`_.
"""
from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal
from functools import cached_property
from typing import Optional, Union

import cachetools
from eth_tester.exceptions import TransactionFailed
from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import deploy_contract
from eth_defi.utils import sanitise_string

#: List of exceptions JSON-RPC provider can through when ERC-20 field look-up fails
#: TODO: Add exceptios from real HTTPS/WSS providers
#: `ValueError` is raised by Ganache
_call_missing_exceptions = (TransactionFailed, BadFunctionCallOutput, ValueError, ContractLogicError)


#: By default we cache 1024 token details using LRU.
#:
#:
DEFAULT_TOKEN_CACHE = cachetools.LRUCache(1024)


@dataclass
class TokenDetails:
    """ERC-20 token Python presentation.

    - A helper class to work with ERC-20 tokens.

    - Read on-chain data, deal with token value decimal conversions.

    - Any field can be ``None`` for non-well-formed tokens.

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

    def __eq__(self, other):
        """Token is the same if it's on the same chain and has the same contract address."""
        assert isinstance(other, TokenDetails)
        return (self.contract.address == other.contract.address) and (self.chain_id == other.chain_id)

    def __hash__(self):
        """Token hash."""
        return hash((self.chain_id, self.contract.address))

    def __repr__(self):
        return f"<{self.name} ({self.symbol}) at {self.contract.address}, {self.decimals} decimals, on chain {self.chain_id}>"

    @cached_property
    def chain_id(self) -> int:
        """The EVM chain id where this token lives."""
        return self.contract.w3.eth.chain_id

    @property
    def address(self) -> HexAddress:
        """The address of this token."""
        return self.contract.address

    def convert_to_decimals(self, raw_amount: int) -> Decimal:
        """Convert raw token units to decimals.

        Example:

        .. code-block:: python

            details = fetch_erc20_details(web3, token_address)
            # Convert 1 wei units to edcimals
            assert details.convert_to_decimals(1) == Decimal("0.0000000000000001")

        """
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
        raw_amount = self.contract.functions.balanceOf(address).call(block_identifier=block_identifier)
        return self.convert_to_decimals(raw_amount)

    @staticmethod
    def generate_cache_key(chain_id: int, address: str) -> int:
        """Generate a cache key for this token.

        - Cached by (chain, address) tuple

        - Validate the inputs before generating the key
        """
        assert type(chain_id) == int
        assert type(address) == str
        assert address.startswith("0x")
        return hash((chain_id, address.lower()))


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
    """Deploys a new test token.

    Uses `ERC20Mock <https://github.com/sushiswap/sushiswap/blob/canary/contracts/mocks/ERC20Mock.sol>`_ contract for the deployment.

    `See Web3.py documentation on Contract instances <https://web3py.readthedocs.io/en/stable/contracts.html#contract-deployment-example>`_.

    Example:

    .. code-block::

        # Deploys an ERC-20 token where 100,000 tokens are allocated ato the deployer address
        token = create_token(web3, deployer, "Hentai books token", "HENTAI", 100_000 * 10**18)
        print(f"Deployed token contract address is {token.address}")
        print(f"Deployer account {deployer} has {token.functions.balanceOf(user_1).call() / 10**18} tokens")

    TODO: Add support for tokens with non-18 decimals like USDC.

    :param web3: Web3 instance
    :param deployer: Deployer account as 0x address
    :param name: Token name
    :param symbol: Token symbol
    :param supply: Token supply as raw units
    :param decimals: How many decimals ERC-20 token values have
    :return: Instance to a deployed Web3 contract.
    """
    return deploy_contract(web3, "ERC20MockDecimals.json", deployer, name, symbol, supply, decimals)


def fetch_erc20_details(
    web3: Web3,
    token_address: Union[HexAddress, str],
    max_str_length: int = 256,
    raise_on_error=True,
    contract_name="ERC20MockDecimals.json",
    cache: cachetools.Cache | None = DEFAULT_TOKEN_CACHE,
    chain_id: int = None,
) -> TokenDetails:
    """Read token details from on-chain data.

    Connect to Web3 node and do RPC calls to extract the token info.
    We apply some sanitazation for incoming data, like length checks and removal of null bytes.

    The function should not raise an exception as long as the underlying node connection does not fail.

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

    :return:
        Sanitised token info
    """

    if not chain_id:
        chain_id = web3.eth.chain_id

    erc_20 = get_deployed_contract(web3, contract_name, token_address)

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
            )
    try:
        symbol = sanitise_string(erc_20.functions.symbol().call()[0:max_str_length])
    except _call_missing_exceptions as e:
        if raise_on_error:
            raise TokenDetailError(f"Token {token_address} missing symbol") from e
        symbol = None
    except OverflowError:
        # OverflowError: Python int too large to convert to C ssize_t
        # Que?
        # Sai Stablecoin uses bytes32 instead of string for name and symbol information
        # https://etherscan.io/address/0x89d24a6b4ccb1b6faa2625fe562bdd9a23260359#readContract
        symbol = None

    try:
        name = sanitise_string(erc_20.functions.name().call()[0:max_str_length])
    except _call_missing_exceptions as e:
        if raise_on_error:
            raise TokenDetailError(f"Token {token_address} missing name") from e
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

    token_details = TokenDetails(erc_20, name, symbol, supply, decimals)
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
