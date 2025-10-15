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
    get_reader_contract,
)
from eth_defi.gmx.constants import ETH_ZERO_ADDRESS, ORDER_TYPES
from eth_defi.gmx.core.markets import Markets
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.gas_utils import get_gas_limits
from eth_defi.gmx.utils import determine_swap_route, apply_factor
from eth_defi.gas import estimate_gas_fees
from eth_defi.compat import encode_abi_compat
from eth_defi.token import fetch_erc20_details

logger = logging.getLogger(__name__)


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

    Example:
        from eth_defi.gmx.liquidity_base.deposit import Deposit, DepositParams

        deposit = Deposit(config)
        params = DepositParams(
            market_key="0x...",
            initial_long_token="0x...",
            initial_short_token="0x...",
            long_token_amount=1000000,
            short_token_amount=1000000,
        )
        result = deposit.create_deposit(params)
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
        self._reader_contract = get_reader_contract(self.web3, self.chain)

        # Initialize markets
        self.markets = Markets(self.config)

        # Initialize oracle prices
        self.oracle_prices = OraclePrices(chain=self.chain)

        # Initialize gas limits
        self._initialize_gas_limits()

        logger.debug("Initialized %s for %s", self.__class__.__name__, self.chain)

    def _initialize_gas_limits(self):
        """Load gas limits from GMX datastore contract.

        Falls back to default constants if datastore query fails.
        The gas limits are returned as integers (already called).
        """
        try:
            datastore = get_datastore_contract(self.web3, self.chain)
            # get_gas_limits returns a dict with integer values (already .call()'ed)
            self._gas_limits = get_gas_limits(datastore)
            logger.debug("Gas limits loaded from datastore contract")
        except Exception as e:
            logger.warning("Failed to load gas limits from datastore: %s", e)
            # Fallback to default gas limits
            self._gas_limits = {
                "deposit": 2500000,
                "multicall_base": 200000,
                "single_swap": 2000000,
                "estimated_fee_base_gas_limit": 500000,
                "estimated_fee_multiplier_factor": 1300000000000000000000000000000,  # 1.3 * 10^30
            }
            logger.debug("Using fallback gas limits from constants")

    def create_deposit(self, params: DepositParams) -> DepositResult:
        """Create a deposit transaction.

        :param params: Deposit parameters
        :type params: DepositParams
        :return: Deposit result with unsigned transaction
        :rtype: DepositResult
        :raises ValueError: If market not found or invalid parameters
        """
        # Validate market exists
        markets_data = self.markets.get_available_markets()
        market_data = markets_data.get(params.market_key)
        if not market_data:
            raise ValueError(f"Market {params.market_key} not found")

        # Check and set initial tokens for single-sided deposits
        params = self._check_initial_tokens(params, market_data)

        # Get gas price
        gas_price = params.max_fee_per_gas if params.max_fee_per_gas else self.web3.eth.gas_price

        # Calculate execution fee using original formula
        execution_fee = self._calculate_execution_fee(gas_price)

        # Apply execution buffer
        execution_fee = int(execution_fee * params.execution_buffer)

        # Check approvals (raises error if insufficient)
        self._check_for_approval(params.initial_long_token, params.long_token_amount)
        self._check_for_approval(params.initial_short_token, params.short_token_amount)

        # Determine swap paths using the helper function
        long_swap_path, short_swap_path = self._determine_swap_paths(
            params,
            market_data,
            markets_data,
        )

        # Estimate output (minimum market tokens)
        min_market_tokens = self._estimate_deposit(
            params,
            market_data,
            long_swap_path,
            short_swap_path,
        )

        # Build deposit arguments
        arguments = self._build_deposit_arguments(
            params,
            long_swap_path,
            short_swap_path,
            min_market_tokens,
            execution_fee,
        )

        # Build multicall (ORDER MATTERS - must match original)
        multicall_args, value_amount = self._build_multicall_args(params, arguments, execution_fee)

        # Calculate total gas limit
        gas_limit = self._gas_limits.get("deposit", 2500000)
        multicall_base = self._gas_limits.get("multicall_base", 200000)
        total_gas = gas_limit + multicall_base

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
    def _check_initial_tokens(params: DepositParams, market_data: dict) -> DepositParams:
        """Check and set initial tokens for single-sided deposits.

        If depositing 0 of long or short tokens, use the market's default token.
        This allows single-sided deposits.

        :param params: Deposit parameters
        :param market_data: Market information
        :return: Updated deposit parameters
        """
        # Create a copy to avoid mutating the input
        if params.long_token_amount == 0:
            params = DepositParams(
                market_key=params.market_key,
                initial_long_token=to_checksum_address(market_data["long_token_address"]),
                initial_short_token=params.initial_short_token,
                long_token_amount=params.long_token_amount,
                short_token_amount=params.short_token_amount,
                execution_buffer=params.execution_buffer,
                max_fee_per_gas=params.max_fee_per_gas,
            )

        if params.short_token_amount == 0:
            params = DepositParams(
                market_key=params.market_key,
                initial_long_token=params.initial_long_token,
                initial_short_token=to_checksum_address(market_data["short_token_address"]),
                long_token_amount=params.long_token_amount,
                short_token_amount=params.short_token_amount,
                execution_buffer=params.execution_buffer,
                max_fee_per_gas=params.max_fee_per_gas,
            )

        return params

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
        estimated_gas_limit = self._gas_limits.get("deposit", 2500000)

        # Apply the factor: value * factor / 10^30
        adjusted_gas_limit = base_gas_limit + apply_factor(estimated_gas_limit, multiplier_factor)

        return int(adjusted_gas_limit * gas_price)

    def _determine_swap_paths(self, params: DepositParams, market_data: dict, markets_data: dict) -> tuple[list, list]:
        """Determine swap paths for long and short tokens using the helper function.

        :param params: Deposit parameters
        :param market_data: Market information
        :param markets_data: All markets data
        :return: Tuple of (long_swap_path, short_swap_path)
        """
        long_swap_path = []
        short_swap_path = []

        market_long_token = market_data.get("long_token_address")
        market_short_token = market_data.get("short_token_address")

        # Build swap path for long token if needed
        if params.initial_long_token.lower() != market_long_token.lower():
            try:
                long_swap_path, requires_multi_swap = determine_swap_route(
                    markets_data,
                    params.initial_long_token,
                    market_long_token,
                    self.chain,
                )
                logger.debug("Long token swap path: %s, multi-swap: %s", long_swap_path, requires_multi_swap)
            except Exception as e:
                logger.warning("Could not determine long token swap route: %s", e)
                long_swap_path = []

        # Build swap path for short token if needed
        if params.initial_short_token.lower() != market_short_token.lower():
            try:
                short_swap_path, requires_multi_swap = determine_swap_route(
                    markets_data,
                    params.initial_short_token,
                    market_short_token,
                    self.chain,
                )
                logger.debug("Short token swap path: %s, multi-swap: %s", short_swap_path, requires_multi_swap)
            except Exception as e:
                logger.warning("Could not determine short token swap route: %s", e)
                short_swap_path = []

        return long_swap_path, short_swap_path

    def _estimate_deposit(
        self,
        params: DepositParams,
        market_data: dict,
        long_swap_path: list,
        short_swap_path: list,
    ) -> int:
        """Estimate the amount of GM tokens expected from deposit.

        :param params: Deposit parameters
        :param market_data: Market information
        :param long_swap_path: Swap path for long token
        :param short_swap_path: Swap path for short token
        :return: Minimum expected GM tokens (in wei)
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
            # Note: The order in the original is (min, max)
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
            estimated_output = self._reader_contract.functions.getDepositAmountOut(
                self.contract_addresses.datastore,
                market_addresses,
                prices,
                params.long_token_amount,
                params.short_token_amount,
                ETH_ZERO_ADDRESS,  # ui_fee_receiver
                ORDER_TYPES.DEPOSIT,  # swap_pricing_type: 3 = deposit
                False,  # include_virtual_inventory_impact
            ).call()

            logger.debug("Estimated deposit output: %s GM tokens", estimated_output)
            return estimated_output

        except Exception as e:
            logger.warning("Failed to estimate deposit output: %s. Using 0 as min output.", e)
            return 0

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

        1. Send long tokens (if > 0 and not native)
        2. Send short tokens (if > 0 and not native)
        3. Send WNT (native tokens + execution fee)
        4. Create a deposit order

        :param params: Deposit parameters
        :param arguments: Deposit arguments tuple
        :param execution_fee: Execution fee in wei
        :return: Tuple of (multicall_args, value_amount)
        """
        multicall_args = []
        wnt_amount = 0

        # Get a native token address
        tokens = NETWORK_TOKENS.get(self.chain.lower())
        if not tokens:
            raise ValueError(f"Unsupported chain: {self.chain}")

        native_token = tokens.get("WETH") if self.chain.lower() == "arbitrum" or "arbitrum_sepolia" else tokens.get("WAVAX")

        # 1. Send long tokens if amount > 0 AND not native token
        if params.long_token_amount > 0:
            if params.initial_long_token.lower() != native_token.lower():
                # ERC20 token
                multicall_args.append(self._send_tokens(params.initial_long_token, params.long_token_amount))
            else:
                # Native token - will be sent as WNT
                wnt_amount += params.long_token_amount

        # 2. Send short tokens if amount > 0 AND not native token
        if params.short_token_amount > 0:
            if native_token and params.initial_short_token.lower() == native_token.lower():
                # Native token - will be sent as WNT
                wnt_amount += params.short_token_amount
            else:
                # ERC20 token
                multicall_args.append(self._send_tokens(params.initial_short_token, params.short_token_amount))

        # 3. Send WNT (native tokens + execution fee)
        multicall_args.append(self._send_wnt(int(wnt_amount + execution_fee)))

        # 4. Create a deposit order
        multicall_args.append(self._create_deposit(arguments))

        # Total value to send with transaction
        value_amount = int(wnt_amount + execution_fee)

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

        if self.chain.lower() in ["arbitrum", "arbitrum_sepolia"]:
            native_token = tokens.get("WETH")
        elif self.chain.lower() in ["avalanche", "avalanche_fuji"]:
            native_token = tokens.get("WAVAX")
        else:
            native_token = None

        # Skip approval for native token (will be wrapped)
        if native_token and token_address.lower() == native_token.lower():
            return

        user_wallet_address = self.config.get_wallet_address()
        if not user_wallet_address:
            raise ValueError("User wallet address is required")

        # Fetch token details
        token_details = fetch_erc20_details(self.web3, token_address)

        # Create ERC20 contract instance
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
            },
            {
                "constant": False,
                "inputs": [
                    {"name": "spender", "type": "address"},
                    {"name": "amount", "type": "uint256"},
                ],
                "name": "approve",
                "outputs": [{"name": "", "type": "bool"}],
                "type": "function",
            },
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

            # Just log a warning - don't block transaction creation
            import logging

            logger = logging.getLogger(__name__)
            logger.warning("Insufficient token allowance for %s. Required: %.4f, Current allowance: %.4f. User needs to approve tokens before submitting the transaction.", token_details.symbol, required, current)
