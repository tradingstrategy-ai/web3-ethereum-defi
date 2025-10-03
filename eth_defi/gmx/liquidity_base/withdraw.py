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
    get_datastore_contract,
    get_reader_contract,
)
from eth_defi.gmx.constants import ETH_ZERO_ADDRESS
from eth_defi.gmx.core.markets import Markets
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.gas_utils import get_gas_limits
from eth_defi.gmx.utils import determine_swap_route, apply_factor
from eth_defi.gas import estimate_gas_fees
from eth_defi.compat import encode_abi_compat
from eth_defi.token import fetch_erc20_details


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

    Example:
        from eth_defi.gmx.liquidity_base.withdraw import Withdraw, WithdrawParams

        withdraw = Withdraw(config)
        params = WithdrawParams(
            market_key="0x...",
            gm_amount=1000000000000000000,  # 1 GM token
            out_token="0x...",
        )
        result = withdraw.create_withdrawal(params)
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
        self._reader_contract = get_reader_contract(self.web3, self.chain)
        self.logger = logging.getLogger(self.__class__.__name__)

        # Initialise markets
        self.markets = Markets(self.config)

        # Initialise oracle prices
        self.oracle_prices = OraclePrices(chain=self.chain)

        # Initialise gas limits
        self._initialize_gas_limits()

        self.logger.debug(f"Initialized {self.__class__.__name__} for {self.chain}")

    def _initialize_gas_limits(self):
        """Load gas limits from GMX datastore contract.

        Falls back to default constants if a datastore query fails.
        The gas limits are returned as integers (already called).
        """
        try:
            datastore = get_datastore_contract(self.web3, self.chain)
            # get_gas_limits returns a dict with integer values (already .call()'ed)
            self._gas_limits = get_gas_limits(datastore)
            self.logger.debug("Gas limits loaded from datastore contract")
        except Exception as e:
            self.logger.warning(f"Failed to load gas limits from datastore: {e}")
            # Fallback to default gas limits
            self._gas_limits = {
                "withdraw": 2000000,
                "multicall_base": 200000,
                "single_swap": 2000000,
                "estimated_fee_base_gas_limit": 500000,
                "estimated_fee_multiplier_factor": 1300000000000000000000000000000,  # 1.3 * 10^30 # just a fallback value scaled with GMX's decimals
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
        markets_data = self.markets.get_available_markets()
        market_data = markets_data.get(params.market_key)
        if not market_data:
            raise ValueError(f"Market {params.market_key} not found")

        # Get gas price
        gas_price = params.max_fee_per_gas if params.max_fee_per_gas else self.web3.eth.gas_price

        # Calculate execution fee using original formula
        execution_fee = self._calculate_execution_fee(gas_price)

        # Apply execution buffer
        execution_fee = int(execution_fee * params.execution_buffer)

        # Check GM token approval (raises error if insufficient)
        self._check_for_approval(params.market_key, params.gm_amount)

        # Determine swap paths using the helper function
        long_swap_path, short_swap_path = self._determine_swap_paths(
            params,
            market_data,
            markets_data,
        )

        # Estimate output amounts (minimum tokens expected)
        min_long_token_amount, min_short_token_amount = self._estimate_withdrawal(params, market_data, long_swap_path, short_swap_path)

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
        multicall_args, value_amount = self._build_multicall_args(
            params,
            arguments,
            execution_fee,
        )

        # Calculate total gas limit
        gas_limit = self._gas_limits.get("withdraw", 2000000)
        multicall_base = self._gas_limits.get("multicall_base", 200000)
        total_gas = gas_limit + multicall_base

        # Build transaction
        transaction = self._build_transaction(
            multicall_args,
            value_amount,
            total_gas,
            gas_price,
        )

        return WithdrawResult(
            transaction=transaction,
            execution_fee=execution_fee,
            gas_limit=total_gas,
            min_long_token_amount=min_long_token_amount,
            min_short_token_amount=min_short_token_amount,
        )

    def _calculate_execution_fee(self, gas_price: int) -> int:
        """Calculate execution fee using GMX's formula.

        Original formula:
        base_gas_limit = gas_limits["estimated_fee_base_gas_limit"].call()
        multiplier_factor = gas_limits["estimated_fee_multiplier_factor"].call()
        adjusted_gas_limit = base_gas_limit + apply_factor(estimated_gas_limit.call(), multiplier_factor)
        return adjusted_gas_limit * gas_price

        :param gas_price: Current gas price in wei
        :return: Execution fee in wei
        """
        base_gas_limit = self._gas_limits.get("estimated_fee_base_gas_limit", 500000)
        multiplier_factor = self._gas_limits.get(
            "estimated_fee_multiplier_factor",
            1300000000000000000000000000000,
        )
        estimated_gas_limit = self._gas_limits.get("withdraw", 2000000)

        # Apply the factor: value * factor / 10^30
        adjusted_gas_limit = base_gas_limit + apply_factor(estimated_gas_limit, multiplier_factor)

        return int(adjusted_gas_limit * gas_price)

    def _determine_swap_paths(self, params: WithdrawParams, market_data: dict, markets_data: dict) -> tuple[list, list]:
        """Determine swap paths for long and short tokens using the helper function.

        IMPORTANT: For withdrawals, the swap path is FROM out_token TO market token,
        which is the reverse of deposits.

        :param params: Withdrawal parameters
        :param market_data: Market information
        :param markets_data: All markets data
        :return: Tuple of (long_swap_path, short_swap_path)
        """
        long_swap_path = []
        short_swap_path = []

        market_long_token = market_data.get("long_token_address")
        market_short_token = market_data.get("short_token_address")

        # Build swap path for long token if needed
        # if market long token != out_token, route FROM out_token TO market long token
        if market_long_token.lower() != params.out_token.lower():
            try:
                long_swap_path, requires_multi_swap = determine_swap_route(
                    markets_data,
                    params.out_token,
                    market_long_token,
                    self.chain,
                )
                self.logger.debug(f"Long token swap path: {long_swap_path}, multi-swap: {requires_multi_swap}")
            except Exception as e:
                self.logger.debug(f"Could not determine long token swap route: {e}")
                long_swap_path = []

        # Build swap path for short token if needed
        # if market short token != out_token, route FROM out_token TO market short token
        if market_short_token.lower() != params.out_token.lower():
            try:
                short_swap_path, requires_multi_swap = determine_swap_route(
                    markets_data,
                    params.out_token,
                    market_short_token,
                    self.chain,
                )
                self.logger.debug(f"Short token swap path: {short_swap_path}, multi-swap: {requires_multi_swap}")
            except Exception as e:
                self.logger.debug(f"Could not determine short token swap route: {e}")
                short_swap_path = []

        return long_swap_path, short_swap_path

    def _estimate_withdrawal(
        self,
        params: WithdrawParams,
        market_data: dict,
        long_swap_path: list,
        short_swap_path: list,
    ) -> tuple[int, int]:
        """Estimate the amount of long and short tokens expected from withdrawal.

        :param params: Withdrawal parameters
        :param market_data: Market information
        :param long_swap_path: Swap path for long token
        :param short_swap_path: Swap path for short token
        :return: Tuple of (min_long_token_amount, min_short_token_amount) in wei
        """
        try:
            # Get oracle prices
            oracle_prices = self.oracle_prices.get_recent_prices()

            # Get token addresses
            index_token_address = market_data["index_token_address"]
            long_token_address = market_data["long_token_address"]
            short_token_address = market_data["short_token_address"]

            # Build market addresses tuple
            market_addresses = [
                params.market_key,
                index_token_address,
                long_token_address,
                short_token_address,
            ]

            # Build prices tuple (minPrice, maxPrice) for each token
            prices = (
                (
                    int(oracle_prices[index_token_address]["minPriceFull"]),
                    int(oracle_prices[index_token_address]["maxPriceFull"]),
                ),
                (
                    int(oracle_prices[long_token_address]["minPriceFull"]),
                    int(oracle_prices[long_token_address]["maxPriceFull"]),
                ),
                (
                    int(oracle_prices[short_token_address]["minPriceFull"]),
                    int(oracle_prices[short_token_address]["maxPriceFull"]),
                ),
            )

            # Call reader contract
            # getWithdrawalAmountOut returns (long_token_amount, short_token_amount)
            estimated_output = self._reader_contract.functions.getWithdrawalAmountOut(
                self.contract_addresses.datastore,
                market_addresses,
                prices,
                params.gm_amount,
                ETH_ZERO_ADDRESS,  # ui_fee_receiver
            ).call()

            min_long_token_amount = estimated_output[0]
            min_short_token_amount = estimated_output[1]

            self.logger.debug(
                f"Estimated withdrawal output: {min_long_token_amount} long tokens, {min_short_token_amount} short tokens",
            )

            return min_long_token_amount, min_short_token_amount

        except Exception as e:
            self.logger.warning(f"Failed to estimate withdrawal output: {e}. Using 0 as min output.")
            return 0, 0

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
        1. Send WNT (execution fee)
        2. Send GM tokens to withdrawal vault
        3. Create withdrawal order

        :param params: Withdrawal parameters
        :param arguments: Withdrawal arguments tuple
        :param execution_fee: Execution fee in wei
        :return: Tuple of (multicall_args, value_amount)
        """
        multicall_args = []
        value_amount = execution_fee

        # 1. Send execution fee (WNT)
        multicall_args.append(self._send_wnt(execution_fee))

        # 2. Send GM tokens to withdrawal vault
        multicall_args.append(self._send_gm_tokens(params.market_key, params.gm_amount))

        # 3. Create a withdrawal order
        multicall_args.append(self._create_withdrawal(arguments))

        return multicall_args, value_amount

    def _build_transaction(self, multicall_args: list, value_amount: int, gas_limit: int, gas_price: int) -> TxParams:
        """Build the final unsigned transaction.

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

        # Create an ERC20 contract instance for GM token
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
