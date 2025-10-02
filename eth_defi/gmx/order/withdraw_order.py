"""
GMX Withdraw Order Implementation

Specialised class for removing liquidity from GMX markets.
Provides withdrawal transaction building and returning unsigned transactions.
"""

import logging
from dataclasses import dataclass

from eth_utils import to_checksum_address
from eth_typing import ChecksumAddress
from web3.types import TxParams

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_contract_addresses, get_exchange_router_contract, get_datastore_contract
from eth_defi.gmx.core.markets import Markets
from eth_defi.gmx.gas_utils import get_gas_limits
from eth_defi.gas import estimate_gas_fees
from eth_defi.compat import encode_abi_compat

ETH_ZERO_ADDRESS = "0x" + "0" * 40


@dataclass
class WithdrawResult:
    """Result of withdraw order creation containing unsigned transaction.

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


class WithdrawOrder:
    """GMX Withdraw Order class for removing liquidity from markets.

    Handles creation of withdrawal transactions for removing liquidity from GMX markets.
    Returns unsigned transactions for external signing.

    Example:
        TODO: Add example usage
    """

    def __init__(
        self,
        config: GMXConfig,
        market_key: ChecksumAddress,
        out_token: ChecksumAddress,
    ):
        """Initialise withdraw order with market and output token.

        :param config: GMX configuration
        :type config: GMXConfig
        :param market_key: Market contract address (hex)
        :type market_key: ChecksumAddress
        :param out_token: Token address to receive on withdrawal
        :type out_token: ChecksumAddress
        """
        self.config = config
        self.chain = config.get_chain()
        self.web3 = config.web3
        self.chain_id = config.web3.eth.chain_id
        self.contract_addresses = get_contract_addresses(self.chain)
        self._exchange_router_contract = get_exchange_router_contract(self.web3, self.chain)
        self.logger = logging.getLogger(self.__class__.__name__)

        self.market_key = to_checksum_address(market_key)
        self.out_token = to_checksum_address(out_token)

        # Initialize markets
        self.markets_instance = Markets(self.config)

        # Initialize gas limits
        self._initialize_gas_limits()

        self.logger.debug(f"Initialized withdraw order for market {self.market_key}")

    def _initialize_gas_limits(self):
        """Load gas limits from GMX datastore contract."""
        try:
            datastore = get_datastore_contract(self.web3, self.chain)
            self._gas_limits = get_gas_limits(datastore)
            self.logger.debug("Gas limits loaded from datastore contract")
        except Exception as e:
            self.logger.warning(f"Failed to load gas limits from datastore: {e}")
            self._gas_limits = {"withdraw": 2000000, "multicall_base": 200000}

    def create_withdraw_order(
        self,
        gm_amount: int,
        execution_buffer: float = 1.1,
        callback_gas_limit: int = 0,
        slippage_percent: float = 0.003,
    ) -> WithdrawResult:
        """Create a withdrawal order transaction.

        Creates an unsigned transaction for removing liquidity from a GMX market.
        The transaction needs to be signed and sent by the user.

        :param gm_amount: Amount of GM tokens to burn (in smallest unit, 18 decimals)
        :type gm_amount: int
        :param execution_buffer: Gas buffer multiplier for execution fee
        :type execution_buffer: float
        :param callback_gas_limit: Gas limit for callback execution
        :type callback_gas_limit: int
        :param slippage_percent: Slippage tolerance for minimum tokens received
        :type slippage_percent: float
        :return: WithdrawResult containing unsigned transaction and details
        :rtype: WithdrawResult
        :raises ValueError: If parameters are invalid or market doesn't exist
        """
        if gm_amount <= 0:
            raise ValueError("gm_amount must be greater than zero")

        # Get market info
        markets = self.markets_instance.get_available_markets()
        market_data = markets.get(self.market_key)
        if not market_data:
            raise ValueError(f"Market {self.market_key} not found")

        # Calculate execution fee
        gas_price = self.web3.eth.gas_price
        withdraw_gas = self._gas_limits.get("withdraw", 2000000)
        base_gas = self._gas_limits.get("multicall_base", 200000)
        total_gas = withdraw_gas + base_gas
        execution_fee = int(total_gas * gas_price * execution_buffer)

        # Estimate minimum tokens out (simplified - can be improved with reader contract)
        min_long_token_amount = 0
        min_short_token_amount = 0

        # Determine swap paths based on out_token
        long_token_swap_path, short_token_swap_path = self._determine_swap_paths(market_data)

        # Build withdrawal arguments
        arguments = self._build_withdraw_arguments(
            min_long_token_amount,
            min_short_token_amount,
            execution_fee,
            callback_gas_limit,
            long_token_swap_path,
            short_token_swap_path,
        )

        # Build multicall
        multicall_args, value_amount = self._build_multicall_args(execution_fee, arguments)

        # Build transaction
        transaction = self._build_transaction(multicall_args, value_amount, total_gas)

        self.logger.debug(f"Created withdraw order: gm_amount={gm_amount}, execution_fee={execution_fee}")

        return WithdrawResult(
            transaction=transaction,
            execution_fee=execution_fee,
            gas_limit=total_gas,
            min_long_token_amount=min_long_token_amount,
            min_short_token_amount=min_short_token_amount,
        )

    def _determine_swap_paths(self, market_data: dict) -> tuple[list, list]:
        """Determine swap paths for long and short tokens."""
        long_token_swap_path = []
        short_token_swap_path = []

        # If out_token doesn't match market's long/short tokens, would need swap path
        # For now, simplified - assumes out_token is one of the market tokens
        return long_token_swap_path, short_token_swap_path

    def _build_withdraw_arguments(
        self,
        min_long_token_amount: int,
        min_short_token_amount: int,
        execution_fee: int,
        callback_gas_limit: int,
        long_token_swap_path: list,
        short_token_swap_path: list,
    ) -> tuple:
        """Build withdrawal arguments tuple for contract call."""
        user_wallet_address = self.config.get_wallet_address()
        if not user_wallet_address:
            raise ValueError("User wallet address is required")

        user_checksum = to_checksum_address(user_wallet_address)
        eth_zero_address = ETH_ZERO_ADDRESS
        should_unwrap_native_token = True

        return (
            user_checksum,  # receiver
            eth_zero_address,  # callbackContract
            eth_zero_address,  # uiFeeReceiver
            self.market_key,  # market
            long_token_swap_path,  # longTokenSwapPath
            short_token_swap_path,  # shortTokenSwapPath
            min_long_token_amount,  # minLongTokenAmount
            min_short_token_amount,  # minShortTokenAmount
            should_unwrap_native_token,  # shouldUnwrapNativeToken
            execution_fee,  # executionFee
            callback_gas_limit,  # callbackGasLimit
        )

    def _build_multicall_args(
        self,
        execution_fee: int,
        arguments: tuple,
    ) -> tuple[list, int]:
        """Build multicall arguments for withdrawal transaction."""
        multicall_args = []

        # Send execution fee
        multicall_args.append(self._send_wnt(execution_fee))

        # Add create withdrawal call
        multicall_args.append(self._create_withdrawal(arguments))

        return multicall_args, execution_fee

    def _build_transaction(
        self,
        multicall_args: list,
        value_amount: int,
        gas_limit: int,
    ) -> TxParams:
        """Build the final unsigned transaction."""
        user_address = self.config.get_wallet_address()
        if not user_address:
            raise ValueError("User wallet address required")

        nonce = self.web3.eth.get_transaction_count(to_checksum_address(user_address))
        gas_fees = estimate_gas_fees(self.web3)

        transaction: TxParams = {
            "from": to_checksum_address(user_address),
            "to": self.contract_addresses.exchangerouter,
            "data": encode_abi_compat(self._exchange_router_contract, "multicall", [multicall_args]),
            "value": value_amount,
            "gas": gas_limit,
            "chainId": self.chain_id,
            "nonce": nonce,
        }

        if gas_fees.max_fee_per_gas is not None:
            transaction["maxFeePerGas"] = gas_fees.max_fee_per_gas
            transaction["maxPriorityFeePerGas"] = gas_fees.max_priority_fee_per_gas
        else:
            transaction["gasPrice"] = gas_fees.legacy_gas_price

        return transaction

    def _create_withdrawal(self, arguments: tuple) -> bytes:
        """Encode createWithdrawal function call."""
        hex_data = encode_abi_compat(
            self._exchange_router_contract,
            "createWithdrawal",
            [arguments],
        )
        if hex_data.startswith("0x"):
            hex_data = hex_data[2:]
        return bytes.fromhex(hex_data)

    def _send_wnt(self, amount: int) -> bytes:
        """Encode sendWnt function call."""
        hex_data = encode_abi_compat(
            self._exchange_router_contract,
            "sendWnt",
            [self.contract_addresses.withdrawalvault, amount],
        )
        if hex_data.startswith("0x"):
            hex_data = hex_data[2:]
        return bytes.fromhex(hex_data)
