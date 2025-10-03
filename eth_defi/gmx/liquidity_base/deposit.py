"""
GMX Deposit Base Class

Base class for adding liquidity to GMX markets.
Provides core deposit functionality that can be extended or used standalone.
"""

import logging
from typing import Optional
from dataclasses import dataclass

from eth_utils import to_checksum_address
from eth_typing import ChecksumAddress
from web3.types import TxParams

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import (
    get_contract_addresses,
    get_exchange_router_contract,
    NETWORK_TOKENS,
    get_datastore_contract,
)
from eth_defi.gmx.constants import ETH_ZERO_ADDRESS
from eth_defi.gmx.core.markets import Markets
from eth_defi.gmx.gas_utils import get_gas_limits
from eth_defi.gas import estimate_gas_fees
from eth_defi.compat import encode_abi_compat
from eth_defi.token import fetch_erc20_details


@dataclass
class DepositParams:
    """Parameters for deposit operations.

    :param market_key: Market address to deposit into
    :param initial_long_token: Long token address to deposit
    :param initial_short_token: Short token address to deposit
    :param long_token_amount: Amount of long tokens in wei
    :param short_token_amount: Amount of short tokens in wei
    :param execution_buffer: Multiplier for execution fee (default 1.3 = 30% buffer)
    :param max_fee_per_gas: Optional gas price override
    """

    market_key: ChecksumAddress
    initial_long_token: ChecksumAddress
    initial_short_token: ChecksumAddress
    long_token_amount: int
    short_token_amount: int
    execution_buffer: float = 1.3
    max_fee_per_gas: Optional[int] = None


@dataclass
class DepositResult:
    """Result of deposit operation.

    :param transaction: Unsigned transaction ready for signing
    :param execution_fee: Estimated execution fee in wei
    :param gas_limit: Gas limit for transaction
    :param min_market_tokens: Minimum market tokens expected
    """

    transaction: TxParams
    execution_fee: int
    gas_limit: int
    min_market_tokens: int


class Deposit:
    """GMX Deposit base class for adding liquidity to markets.

    Handles creation of deposit transactions for adding liquidity to GMX markets.
    Returns unsigned transactions for external signing.

    This is the base class that provides core deposit functionality.
    Use Deposit for a simpler interface.

    Example:
        TODO
    """

    def __init__(self, config: GMXConfig):
        """Initialize deposit with GMX configuration.

        :param config: GMX configuration instance
        :type config: GMXConfig
        """
        self.config = config
        self.chain = config.get_chain()
        self.web3 = config.web3
        self.chain_id = config.web3.eth.chain_id
        self.contract_addresses = get_contract_addresses(self.chain)
        self._exchange_router_contract = get_exchange_router_contract(self.web3, self.chain)
        self.logger = logging.getLogger(self.__class__.__name__)

        # Initialise markets
        self.markets = Markets(self.config)

        # Initialise gas limits
        self._initialize_gas_limits()

        self.logger.debug(f"Initialized {self.__class__.__name__} for {self.chain}")

    def _initialize_gas_limits(self):
        """Load gas limits from GMX datastore contract.

        Falls back to default constants if datastore query fails.
        """
        try:
            datastore = get_datastore_contract(self.web3, self.chain)
            self._gas_limits = get_gas_limits(datastore)
            self.logger.debug("Gas limits loaded from datastore contract")
        except Exception as e:
            self.logger.warning(f"Failed to load gas limits from datastore: {e}")
            # Fallback to default gas limits
            self._gas_limits = {
                "deposit": 2500000,
                "multicall_base": 200000,
                "single_swap": 2000000,
            }
            self.logger.debug("Using fallback gas limits from constants")

    def create_deposit(self, params: DepositParams) -> DepositResult:
        """Create a deposit transaction.

        :param params: Deposit parameters
        :type params: DepositParams
        :return: Deposit result with unsigned transaction
        :rtype: DepositResult
        :raises ValueError: If market not found or invalid parameters
        """
        # Validate market exists
        markets = self.markets.get_available_markets()
        market_data = markets.get(params.market_key)
        if not market_data:
            raise ValueError(f"Market {params.market_key} not found")

        # Get gas price
        gas_price = self.web3.eth.gas_price
        if params.max_fee_per_gas:
            gas_price = params.max_fee_per_gas

        # Calculate gas limits
        gas_limit = self._gas_limits.get("deposit", 2500000)
        multicall_base = self._gas_limits.get("multicall_base", 200000)
        total_gas = gas_limit + multicall_base

        # Calculate execution fee with buffer
        execution_fee = int(total_gas * gas_price * params.execution_buffer)

        # Check approvals
        self._check_for_approval(params.initial_long_token, params.long_token_amount)
        self._check_for_approval(params.initial_short_token, params.short_token_amount)

        # Determine swap paths
        long_swap_path, short_swap_path = self._determine_swap_paths(params, market_data)

        # Estimate output (minimum market tokens)
        min_market_tokens = 0  # TODO: Implement estimation

        # Build deposit arguments
        arguments = self._build_deposit_arguments(
            params,
            long_swap_path,
            short_swap_path,
            min_market_tokens,
            execution_fee,
        )

        # Build multicall
        multicall_args, value_amount = self._build_multicall_args(
            params,
            arguments,
            execution_fee,
        )

        # Build transaction
        transaction = self._build_transaction(
            multicall_args,
            value_amount,
            total_gas,
            gas_price,
        )

        return DepositResult(
            transaction=transaction,
            execution_fee=execution_fee,
            gas_limit=total_gas,
            min_market_tokens=min_market_tokens,
        )

    @staticmethod
    def _determine_swap_paths(params: DepositParams, market_data: dict) -> tuple[list, list]:
        """Determine swap paths for long and short tokens.

        :param params: Deposit parameters
        :param market_data: Market information
        :return: Tuple of (long_swap_path, short_swap_path)
        """
        # If tokens match market tokens, no swap needed
        long_swap_path = []
        short_swap_path = []

        market_long_token = market_data.get("long_token_address")
        market_short_token = market_data.get("short_token_address")

        # Build swap path for long token if needed
        if params.initial_long_token.lower() != market_long_token.lower():
            long_swap_path = [params.market_key]

        # Build swap path for short token if needed
        if params.initial_short_token.lower() != market_short_token.lower():
            short_swap_path = [params.market_key]

        return long_swap_path, short_swap_path

    def _build_deposit_arguments(
        self,
        params: DepositParams,
        long_swap_path: list,
        short_swap_path: list,
        min_market_tokens: int,
        execution_fee: int,
    ) -> tuple:
        """Build deposit arguments tuple for contract call.

        :param params: Deposit parameters
        :param long_swap_path: Swap path for long token
        :param short_swap_path: Swap path for short token
        :param min_market_tokens: Minimum expected market tokens
        :param execution_fee: Execution fee in wei
        :return: Tuple of deposit arguments
        """
        user_wallet_address = self.config.get_wallet_address()
        if not user_wallet_address:
            raise ValueError("User wallet address is required")

        user_checksum = to_checksum_address(user_wallet_address)
        market_checksum = to_checksum_address(params.market_key)
        long_token_checksum = to_checksum_address(params.initial_long_token)
        short_token_checksum = to_checksum_address(params.initial_short_token)

        eth_zero = ETH_ZERO_ADDRESS

        # Convert swap paths to checksum addresses
        long_swap_path_checksum = [to_checksum_address(addr) for addr in long_swap_path]
        short_swap_path_checksum = [to_checksum_address(addr) for addr in short_swap_path]

        callback_gas_limit = 0
        should_unwrap_native_token = True

        return (
            user_checksum,  # receiver
            eth_zero,  # callbackContract
            eth_zero,  # uiFeeReceiver
            market_checksum,  # market
            long_token_checksum,  # initialLongToken
            short_token_checksum,  # initialShortToken
            long_swap_path_checksum,  # longTokenSwapPath
            short_swap_path_checksum,  # shortTokenSwapPath
            min_market_tokens,  # minMarketTokens
            should_unwrap_native_token,  # shouldUnwrapNativeToken
            execution_fee,  # executionFee
            callback_gas_limit,  # callbackGasLimit
        )

    def _build_multicall_args(
        self,
        params: DepositParams,
        arguments: tuple,
        execution_fee: int,
    ) -> tuple[list, int]:
        """Build multicall arguments for deposit transaction.

        :param params: Deposit parameters
        :param arguments: Deposit arguments tuple
        :param execution_fee: Execution fee in wei
        :return: Tuple of (multicall_args, value_amount)
        """
        multicall_args = []
        value_amount = execution_fee

        # Get native token address
        tokens = NETWORK_TOKENS.get(self.chain.lower())
        if not tokens:
            raise ValueError(f"Unsupported chain: {self.chain}")

        native_token = tokens.get("WETH") if self.chain.lower() == "arbitrum" else tokens.get("WAVAX")

        # Send execution fee
        multicall_args.append(self._send_wnt(execution_fee))

        # Send long tokens if amount > 0
        if params.long_token_amount > 0:
            if params.initial_long_token.lower() == native_token.lower():
                # Native token - send as WNT and include in value
                value_amount += params.long_token_amount
                multicall_args.append(self._send_wnt(params.long_token_amount))
            else:
                # ERC20 token
                multicall_args.append(
                    self._send_tokens(
                        params.initial_long_token,
                        params.long_token_amount,
                    )
                )

        # Send short tokens if amount > 0
        if params.short_token_amount > 0:
            if params.initial_short_token.lower() == native_token.lower():
                # Native token - send as WNT and include in value
                value_amount += params.short_token_amount
                multicall_args.append(self._send_wnt(params.short_token_amount))
            else:
                # ERC20 token
                multicall_args.append(
                    self._send_tokens(
                        params.initial_short_token,
                        params.short_token_amount,
                    )
                )

        # Create deposit order
        multicall_args.append(self._create_deposit(arguments))

        return multicall_args, value_amount

    def _build_transaction(
        self,
        multicall_args: list,
        value_amount: int,
        gas_limit: int,
        gas_price: int,
    ) -> TxParams:
        """Build final unsigned transaction.

        :param multicall_args: List of encoded multicall arguments
        :param value_amount: Total value to send in wei
        :param gas_limit: Gas limit for transaction
        :param gas_price: Gas price in wei
        :return: Unsigned transaction dictionary
        """
        user_wallet_address = self.config.get_wallet_address()
        if not user_wallet_address:
            raise ValueError("User wallet address is required")

        # Get nonce and gas fees
        nonce = self.web3.eth.get_transaction_count(to_checksum_address(user_wallet_address))
        gas_fees = estimate_gas_fees(self.web3)

        transaction: TxParams = {
            "from": to_checksum_address(user_wallet_address),
            "to": self.contract_addresses.exchangerouter,
            "value": value_amount,
            "gas": gas_limit,
            "chainId": self.chain_id,
            "data": encode_abi_compat(self._exchange_router_contract, "multicall", [multicall_args]),
            "nonce": nonce,
        }

        # Add EIP-1559 or legacy gas pricing
        if gas_fees.max_fee_per_gas is not None:
            transaction["maxFeePerGas"] = gas_fees.max_fee_per_gas
            transaction["maxPriorityFeePerGas"] = gas_fees.max_priority_fee_per_gas
        else:
            transaction["gasPrice"] = gas_fees.legacy_gas_price

        return transaction

    def _create_deposit(self, arguments: tuple) -> bytes:
        """Encode createDeposit function call.

        :param arguments: Deposit arguments tuple
        :return: Encoded function call
        """
        hex_data = encode_abi_compat(self._exchange_router_contract, "createDeposit", [arguments])
        if hex_data.startswith("0x"):
            hex_data = hex_data[2:]
        return bytes.fromhex(hex_data)

    def _send_tokens(self, token_address: str, amount: int) -> bytes:
        """Encode sendTokens function call.

        :param token_address: Token contract address
        :param amount: Amount in wei
        :return: Encoded function call
        """
        deposit_vault = self.contract_addresses.depositvault
        hex_data = encode_abi_compat(
            self._exchange_router_contract,
            "sendTokens",
            [to_checksum_address(token_address), deposit_vault, amount],
        )
        if hex_data.startswith("0x"):
            hex_data = hex_data[2:]
        return bytes.fromhex(hex_data)

    def _send_wnt(self, amount: int) -> bytes:
        """Encode sendWnt function call.

        :param amount: Amount in wei
        :return: Encoded function call
        """
        deposit_vault = self.contract_addresses.depositvault
        hex_data = encode_abi_compat(self._exchange_router_contract, "sendWnt", [deposit_vault, amount])
        if hex_data.startswith("0x"):
            hex_data = hex_data[2:]
        return bytes.fromhex(hex_data)

    def _check_for_approval(self, token_address: str, amount: int) -> None:
        """Check if tokens are approved for spending.

        :param token_address: Token contract address
        :param amount: Amount that needs approval
        :raises ValueError: If insufficient allowance
        """
        if amount == 0:
            return

        # Get native token to skip approval check
        tokens = NETWORK_TOKENS.get(self.chain.lower())
        if not tokens:
            raise ValueError(f"Unsupported chain: {self.chain}")

        native_token = tokens.get("WETH") if self.chain.lower() == "arbitrum" else tokens.get("WAVAX")

        # Skip approval for native token (will be wrapped)
        if token_address.lower() == native_token.lower():
            return

        user_wallet_address = self.config.get_wallet_address()
        if not user_wallet_address:
            raise ValueError("User wallet address is required")

        # Fetch token details
        token_details = fetch_erc20_details(self.web3, token_address)

        # Create ERC20 contract instance
        from web3 import Web3

        erc20_abi = [
            {
                "constant": True,
                "inputs": [
                    {"name": "owner", "type": "address"},
                    {"name": "spender", "type": "address"},
                ],
                "name": "allowance",
                "outputs": [{"name": "", "type": "uint256"}],
                "type": "function",
            }
        ]

        token_contract = self.web3.eth.contract(address=to_checksum_address(token_address), abi=erc20_abi)

        # Check allowance
        spender = self.contract_addresses.exchangerouter
        allowance = token_contract.functions.allowance(
            to_checksum_address(user_wallet_address),
            spender,
        ).call()

        if allowance < amount:
            required = amount / (10**token_details.decimals)
            current = allowance / (10**token_details.decimals)
            raise ValueError(f"Insufficient token allowance for {token_details.symbol}. Required: {required:.4f}, Current allowance: {current:.4f}. Please approve tokens first.")
