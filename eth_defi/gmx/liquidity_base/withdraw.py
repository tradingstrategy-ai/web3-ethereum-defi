"""
GMX Withdraw Base Class

Base class for removing liquidity from GMX markets.
Provides core withdrawal functionality that can be extended or used standalone.
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
from eth_defi.gmx.core.markets import Markets
from eth_defi.gmx.gas_utils import get_gas_limits
from eth_defi.gas import estimate_gas_fees
from eth_defi.compat import encode_abi_compat
from eth_defi.token import fetch_erc20_details


ETH_ZERO_ADDRESS = "0x" + "0" * 40


@dataclass
class WithdrawParams:
    """Parameters for withdrawal operations.

    :param market_key: Market address to withdraw from
    :param gm_amount: Amount of GM tokens to burn (in wei)
    :param out_token: Desired output token address (long or short token)
    :param execution_buffer: Multiplier for execution fee (default 1.3 = 30% buffer)
    :param max_fee_per_gas: Optional gas price override
    """

    market_key: ChecksumAddress
    gm_amount: int
    out_token: ChecksumAddress
    execution_buffer: float = 1.3
    max_fee_per_gas: Optional[int] = None


@dataclass
class WithdrawResult:
    """Result of withdrawal operation.

    :param transaction: Unsigned transaction ready for signing
    :param execution_fee: Estimated execution fee in wei
    :param gas_limit: Gas limit for transaction
    :param min_long_token_amount: Minimum long tokens expected
    :param min_short_token_amount: Minimum short tokens expected
    """

    transaction: TxParams
    execution_fee: int
    gas_limit: int
    min_long_token_amount: int
    min_short_token_amount: int


class Withdraw:
    """GMX Withdraw base class for removing liquidity from markets.

    Handles creation of withdrawal transactions for removing liquidity from GMX markets.
    Returns unsigned transactions for external signing.

    This is the base class that provides core withdrawal functionality.
    Use WithdrawOrder for a simpler interface.

    Example:
        >>> from eth_defi.gmx.order.withdraw import Withdraw, WithdrawParams
        >>> from eth_defi.gmx.config import GMXConfig
        >>>
        >>> config = GMXConfig(...)
        >>> params = WithdrawParams(
        ...     market_key="0x...",
        ...     gm_amount=1000000000000000000,  # 1 GM token
        ...     out_token="0x...",  # Desired output token
        ... )
        >>> withdraw = Withdraw(config)
        >>> result = withdraw.create_withdrawal(params)
        >>> # Sign and broadcast result.transaction
    """

    def __init__(self, config: GMXConfig):
        """Initialize withdrawal with GMX configuration.

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

        # Initialize markets
        self.markets = Markets(self.config)

        # Initialize gas limits
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
                "withdraw": 2000000,
                "multicall_base": 200000,
                "single_swap": 2000000,
            }
            self.logger.debug("Using fallback gas limits from constants")

    def create_withdrawal(self, params: WithdrawParams) -> WithdrawResult:
        """Create a withdrawal transaction.

        :param params: Withdrawal parameters
        :type params: WithdrawParams
        :return: Withdrawal result with unsigned transaction
        :rtype: WithdrawResult
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
        gas_limit = self._gas_limits.get("withdraw", 2000000)
        multicall_base = self._gas_limits.get("multicall_base", 200000)
        total_gas = gas_limit + multicall_base

        # Calculate execution fee with buffer
        execution_fee = int(total_gas * gas_price * params.execution_buffer)

        # Check GM token approval
        self._check_for_approval(params.market_key, params.gm_amount)

        # Determine swap paths
        long_swap_path, short_swap_path = self._determine_swap_paths(params, market_data)

        # Estimate output amounts
        min_long_token_amount = 0  # TODO: Implement estimation
        min_short_token_amount = 0  # TODO: Implement estimation

        # Build withdrawal arguments
        arguments = self._build_withdraw_arguments(
            params,
            long_swap_path,
            short_swap_path,
            min_long_token_amount,
            min_short_token_amount,
            execution_fee,
        )

        # Build multicall
        multicall_args, value_amount = self._build_multicall_args(params, arguments, execution_fee)

        # Build transaction
        transaction = self._build_transaction(multicall_args, value_amount, total_gas, gas_price)

        return WithdrawResult(
            transaction=transaction,
            execution_fee=execution_fee,
            gas_limit=total_gas,
            min_long_token_amount=min_long_token_amount,
            min_short_token_amount=min_short_token_amount,
        )

    def _determine_swap_paths(self, params: WithdrawParams, market_data: dict) -> tuple[list, list]:
        """Determine swap paths for long and short tokens.

        :param params: Withdrawal parameters
        :param market_data: Market information
        :return: Tuple of (long_swap_path, short_swap_path)
        """
        # If out_token matches market tokens, no swap needed
        long_swap_path = []
        short_swap_path = []

        market_long_token = market_data.get("long_token_address")
        market_short_token = market_data.get("short_token_address")

        # Build swap path if out_token doesn't match market tokens
        if params.out_token.lower() != market_long_token.lower():
            long_swap_path = [params.market_key]

        if params.out_token.lower() != market_short_token.lower():
            short_swap_path = [params.market_key]

        return long_swap_path, short_swap_path

    def _build_withdraw_arguments(
        self,
        params: WithdrawParams,
        long_swap_path: list,
        short_swap_path: list,
        min_long_token_amount: int,
        min_short_token_amount: int,
        execution_fee: int,
    ) -> tuple:
        """Build withdrawal arguments tuple for contract call.

        :param params: Withdrawal parameters
        :param long_swap_path: Swap path for long token
        :param short_swap_path: Swap path for short token
        :param min_long_token_amount: Minimum expected long tokens
        :param min_short_token_amount: Minimum expected short tokens
        :param execution_fee: Execution fee in wei
        :return: Tuple of withdrawal arguments
        """
        user_wallet_address = self.config.get_wallet_address()
        if not user_wallet_address:
            raise ValueError("User wallet address is required")

        user_checksum = to_checksum_address(user_wallet_address)
        market_checksum = to_checksum_address(params.market_key)

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
            long_swap_path_checksum,  # longTokenSwapPath
            short_swap_path_checksum,  # shortTokenSwapPath
            min_long_token_amount,  # minLongTokenAmount
            min_short_token_amount,  # minShortTokenAmount
            should_unwrap_native_token,  # shouldUnwrapNativeToken
            execution_fee,  # executionFee
            callback_gas_limit,  # callbackGasLimit
        )

    def _build_multicall_args(self, params: WithdrawParams, arguments: tuple, execution_fee: int) -> tuple[list, int]:
        """Build multicall arguments for withdrawal transaction.

        :param params: Withdrawal parameters
        :param arguments: Withdrawal arguments tuple
        :param execution_fee: Execution fee in wei
        :return: Tuple of (multicall_args, value_amount)
        """
        multicall_args = []
        value_amount = execution_fee

        # Send execution fee
        multicall_args.append(self._send_wnt(execution_fee))

        # Send GM tokens to withdrawal vault
        multicall_args.append(self._send_gm_tokens(params.market_key, params.gm_amount))

        # Create withdrawal order
        multicall_args.append(self._create_withdrawal(arguments))

        return multicall_args, value_amount

    def _build_transaction(self, multicall_args: list, value_amount: int, gas_limit: int, gas_price: int) -> TxParams:
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

    def _create_withdrawal(self, arguments: tuple) -> bytes:
        """Encode createWithdrawal function call.

        :param arguments: Withdrawal arguments tuple
        :return: Encoded function call
        """
        hex_data = encode_abi_compat(self._exchange_router_contract, "createWithdrawal", [arguments])
        if hex_data.startswith("0x"):
            hex_data = hex_data[2:]
        return bytes.fromhex(hex_data)

    def _send_gm_tokens(self, market_address: str, amount: int) -> bytes:
        """Encode sendTokens function call for GM tokens.

        :param market_address: Market/GM token address
        :param amount: Amount in wei
        :return: Encoded function call
        """
        withdrawal_vault = self.contract_addresses.withdrawalvault
        hex_data = encode_abi_compat(
            self._exchange_router_contract,
            "sendTokens",
            [to_checksum_address(market_address), withdrawal_vault, amount],
        )
        if hex_data.startswith("0x"):
            hex_data = hex_data[2:]
        return bytes.fromhex(hex_data)

    def _send_wnt(self, amount: int) -> bytes:
        """Encode sendWnt function call.

        :param amount: Amount in wei
        :return: Encoded function call
        """
        withdrawal_vault = self.contract_addresses.withdrawalvault
        hex_data = encode_abi_compat(self._exchange_router_contract, "sendWnt", [withdrawal_vault, amount])
        if hex_data.startswith("0x"):
            hex_data = hex_data[2:]
        return bytes.fromhex(hex_data)

    def _check_for_approval(self, gm_token_address: str, amount: int) -> None:
        """Check if GM tokens are approved for spending.

        :param gm_token_address: GM token (market) address
        :param amount: Amount that needs approval
        :raises ValueError: If insufficient allowance
        """
        if amount == 0:
            return

        user_wallet_address = self.config.get_wallet_address()
        if not user_wallet_address:
            raise ValueError("User wallet address is required")

        # Create ERC20 contract instance for GM token
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

        token_contract = self.web3.eth.contract(address=to_checksum_address(gm_token_address), abi=erc20_abi)

        # Check allowance
        spender = self.contract_addresses.exchangerouter
        allowance = token_contract.functions.allowance(
            to_checksum_address(user_wallet_address),
            spender,
        ).call()

        if allowance < amount:
            # GM tokens have 18 decimals
            required = amount / (10**18)
            current = allowance / (10**18)
            raise ValueError(f"Insufficient GM token allowance. Required: {required:.4f}, Current allowance: {current:.4f}. Please approve GM tokens first.")
