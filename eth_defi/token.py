"""ERC-20 token information, deployment and manipulation.

Deploy ERC-20 tokens to be used within your test suite.

`Read also unit test suite for tokens to see how ERC-20 can be manipulated in pytest <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/tests/test_token.py>`_.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Union

from eth_tester.exceptions import TransactionFailed
from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import BadFunctionCallOutput

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import deploy_contract
from eth_defi.utils import sanitise_string

#: List of exceptions JSON-RPC provider can through when ERC-20 field look-up fails
#: TODO: Add exceptios from real HTTPS/WSS providers
#: `ValueError` is raised by Ganache
_call_missing_exceptions = (TransactionFailed, BadFunctionCallOutput, ValueError)


@dataclass
class TokenDetails:
    """A helper class to detail with token instructions.

    Any field can be None for non-wellformed tokens.
    """

    #: The underlying ERC-20 contract proxy class instance
    contract: Contract

    #: Token name e.g. `USD Circle`
    name: Optional[str] = None

    #: Token symbol e.g. `USDC`
    symbol: Optional[str] = None

    #: Token supply as raw units
    total_supply: Optional[int] = None

    #: Number of decimals
    decimals: Optional[int] = None

    def __repr__(self):
        return f"<{self.name} ({self.symbol}) at {self.contract.address}, {self.decimals} decimals>"

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

    :param web3: Web3 instance
    :param token_address: ERC-20 contract address:
    :param max_str_length: For input sanitisation
    :param raise_on_error: If set, raise `TokenDetailError` on any error instead of silently ignoring in and setting details to None.
    :return: Sanitised token info
    """

    erc_20 = get_deployed_contract(web3, "ERC20MockDecimals.json", token_address)

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

    return TokenDetails(erc_20, name, symbol, supply, decimals)
