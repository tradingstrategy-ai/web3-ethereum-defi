"""
GMX Deposit Order Implementation

Specialized class for adding liquidity to GMX markets.
Provides deposit transaction building and returning unsigned transactions.
"""

import logging
from typing import Optional
from dataclasses import dataclass

from eth_utils import to_checksum_address
from eth_typing import ChecksumAddress
from web3.types import TxParams

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_contract_addresses, get_exchange_router_contract, NETWORK_TOKENS, get_datastore_contract
from eth_defi.gmx.core.markets import Markets
from eth_defi.gmx.gas_utils import get_gas_limits
from eth_defi.gas import estimate_gas_fees
from eth_defi.compat import encode_abi_compat
from eth_defi.token import fetch_erc20_details


ETH_ZERO_ADDRESS = "0x" + "0" * 40


@dataclass
class DepositResult:
    """Result of deposit order creation containing unsigned transaction.

    :param transaction: Unsigned transaction ready for signing
    :param execution_fee: Estimated execution fee in wei
    :param gas_limit: Gas limit for transaction
    :param min_market_tokens: Minimum market tokens expected
    """

    transaction: TxParams
    execution_fee: int
    gas_limit: int
    min_market_tokens: int


class DepositOrder:
    """GMX Deposit Order class for adding liquidity to markets.

    Handles creation of deposit transactions for adding liquidity to GMX markets.
    Returns unsigned transactions for external signing.

    Example:
        TODO: Add example usage
    """

    def __init__(
        self,
        config: GMXConfig,
        market_key: ChecksumAddress,
        initial_long_token: Optional[ChecksumAddress] = None,
        initial_short_token: Optional[ChecksumAddress] = None,
    ):
        """Initialize deposit order with market and token configuration.

        :param config: GMX configuration
        :type config: GMXConfig
        :param market_key: Market contract address (hex)
        :type market_key: ChecksumAddress
        :param initial_long_token: Long token address to deposit (None = use market's long token)
        :type initial_long_token: Optional[ChecksumAddress]
        :param initial_short_token: Short token address to deposit (None = use market's short token)
        :type initial_short_token: Optional[ChecksumAddress]
        """
        self.config = config
        self.chain = config.get_chain()
        self.web3 = config.web3
        self.chain_id = config.web3.eth.chain_id
        self.contract_addresses = get_contract_addresses(self.chain)
        self._exchange_router_contract = get_exchange_router_contract(self.web3, self.chain)
        self.logger = logging.getLogger(self.__class__.__name__)

        self.market_key = to_checksum_address(market_key)
        self.initial_long_token = to_checksum_address(initial_long_token) if initial_long_token else None
        self.initial_short_token = to_checksum_address(initial_short_token) if initial_short_token else None

        # Initialize markets
        self.markets_instance = Markets(self.config)

        # Initialize gas limits
        self._initialize_gas_limits()

        self.logger.debug(f"Initialized deposit order for market {self.market_key}")

    def _initialize_gas_limits(self):
        """Load gas limits from GMX datastore contract."""
        try:
            datastore = get_datastore_contract(self.web3, self.chain)
            self._gas_limits = get_gas_limits(datastore)
            self.logger.debug("Gas limits loaded from datastore contract")
        except Exception as e:
            self.logger.warning(f"Failed to load gas limits from datastore: {e}")
            self._gas_limits = {"deposit": 2000000, "multicall_base": 200000}

    def create_deposit_order(
        self,
        long_token_amount: int,
        short_token_amount: int,
        execution_buffer: float = 1.1,
        callback_gas_limit: int = 0,
        slippage_percent: float = 0.003,
    ) -> DepositResult:
        """Create a deposit order transaction.

        Creates an unsigned transaction for adding liquidity to a GMX market.
        The transaction needs to be signed and sent by the user.

        :param long_token_amount: Amount of long token to deposit (in token's smallest unit)
        :type long_token_amount: int
        :param short_token_amount: Amount of short token to deposit (in token's smallest unit)
        :type short_token_amount: int
        :param execution_buffer: Gas buffer multiplier for execution fee
        :type execution_buffer: float
        :param callback_gas_limit: Gas limit for callback execution
        :type callback_gas_limit: int
        :param slippage_percent: Slippage tolerance for minimum tokens received
        :type slippage_percent: float
        :return: DepositResult containing unsigned transaction and details
        :rtype: DepositResult
        :raises ValueError: If parameters are invalid or market doesn't exist
        """
        if long_token_amount == 0 and short_token_amount == 0:
            raise ValueError("At least one of long_token_amount or short_token_amount must be non-zero")

        # Get market info
        markets = self.markets_instance.get_available_markets()
        market_data = markets.get(self.market_key)
        if not market_data:
            raise ValueError(f"Market {self.market_key} not found")

        # Set default token addresses if not provided
        if self.initial_long_token is None:
            self.initial_long_token = to_checksum_address(market_data["long_token_address"])
        if self.initial_short_token is None:
            self.initial_short_token = to_checksum_address(market_data["short_token_address"])

        # Calculate execution fee
        gas_price = self.web3.eth.gas_price
        deposit_gas = self._gas_limits.get("deposit", 2000000)
        base_gas = self._gas_limits.get("multicall_base", 200000)
        total_gas = deposit_gas + base_gas
        execution_fee = int(total_gas * gas_price * execution_buffer)

        # Estimate minimum market tokens (simple 1% slippage buffer)
        min_market_tokens = 0  # Can be estimated more accurately with reader contract

        # Check token approvals if not depositing
        self._check_for_approval(self.initial_long_token, long_token_amount)
        self._check_for_approval(self.initial_short_token, short_token_amount)

        # Build deposit arguments
        arguments = self._build_deposit_arguments(
            long_token_amount,
            short_token_amount,
            min_market_tokens,
            execution_fee,
            callback_gas_limit,
        )

        # Build multicall
        multicall_args, value_amount = self._build_multicall_args(
            long_token_amount,
            short_token_amount,
            execution_fee,
        )

        # Build transaction
        transaction = self._build_transaction(multicall_args, value_amount, total_gas)

        self.logger.debug(f"Created deposit order: long={long_token_amount}, short={short_token_amount}, execution_fee={execution_fee}")

        return DepositResult(
            transaction=transaction,
            execution_fee=execution_fee,
            gas_limit=total_gas,
            min_market_tokens=min_market_tokens,
        )

    def _build_deposit_arguments(
        self,
        long_token_amount: int,
        short_token_amount: int,
        min_market_tokens: int,
        execution_fee: int,
        callback_gas_limit: int,
    ) -> tuple:
        """Build deposit arguments tuple for contract call."""
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
            self.initial_long_token,  # initialLongToken
            self.initial_short_token,  # initialShortToken
            [],  # longTokenSwapPath (empty for now)
            [],  # shortTokenSwapPath (empty for now)
            min_market_tokens,  # minMarketTokens
            should_unwrap_native_token,  # shouldUnwrapNativeToken
            execution_fee,  # executionFee
            callback_gas_limit,  # callbackGasLimit
        )

    def _build_multicall_args(
        self,
        long_token_amount: int,
        short_token_amount: int,
        execution_fee: int,
    ) -> tuple[list, int]:
        """Build multicall arguments for deposit transaction."""
        chain_tokens = NETWORK_TOKENS.get(self.chain.lower())
        if not chain_tokens:
            raise ValueError(f"Unsupported chain: {self.chain}")

        if self.chain.lower() == "arbitrum":
            native_token_address = chain_tokens.get("WETH")
        elif self.chain.lower() == "avalanche":
            native_token_address = chain_tokens.get("WAVAX")
        else:
            raise ValueError(f"Unsupported chain: {self.chain}")

        multicall_args = []
        wnt_amount = 0

        # Handle long token deposit
        if long_token_amount > 0:
            if self.initial_long_token.lower() != native_token_address.lower():
                multicall_args.append(self._send_tokens(self.initial_long_token, long_token_amount))
            else:
                wnt_amount += long_token_amount

        # Handle short token deposit
        if short_token_amount > 0:
            if self.initial_short_token.lower() != native_token_address.lower():
                multicall_args.append(self._send_tokens(self.initial_short_token, short_token_amount))
            else:
                wnt_amount += short_token_amount

        # Send WNT (including any native token deposits + execution fee)
        total_wnt = wnt_amount + execution_fee
        multicall_args.append(self._send_wnt(total_wnt))

        # Add create deposit call
        arguments = self._build_deposit_arguments(
            long_token_amount,
            short_token_amount,
            0,  # min_market_tokens
            execution_fee,
            0,  # callback_gas_limit
        )
        multicall_args.append(self._create_deposit(arguments))

        return multicall_args, total_wnt

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

    def _create_deposit(self, arguments: tuple) -> bytes:
        """Encode createDeposit function call."""
        hex_data = encode_abi_compat(
            self._exchange_router_contract,
            "createDeposit",
            [arguments],
        )
        if hex_data.startswith("0x"):
            hex_data = hex_data[2:]
        return bytes.fromhex(hex_data)

    def _send_tokens(self, token_address: str, amount: int) -> bytes:
        """Encode sendTokens function call."""
        hex_data = encode_abi_compat(
            self._exchange_router_contract,
            "sendTokens",
            [token_address, self.contract_addresses.depositvault, amount],
        )
        if hex_data.startswith("0x"):
            hex_data = hex_data[2:]
        return bytes.fromhex(hex_data)

    def _send_wnt(self, amount: int) -> bytes:
        """Encode sendWnt function call."""
        hex_data = encode_abi_compat(
            self._exchange_router_contract,
            "sendWnt",
            [self.contract_addresses.depositvault, amount],
        )
        if hex_data.startswith("0x"):
            hex_data = hex_data[2:]
        return bytes.fromhex(hex_data)

    def _check_for_approval(self, token_address: str, amount: int) -> None:
        """Check if token has sufficient approval."""
        if amount == 0:
            return

        chain_tokens = NETWORK_TOKENS.get(self.chain.lower())
        if not chain_tokens:
            return

        if self.chain.lower() == "arbitrum":
            native_token_address = chain_tokens.get("WETH")
        elif self.chain.lower() == "avalanche":
            native_token_address = chain_tokens.get("WAVAX")
        else:
            return

        # Skip approval check for native tokens
        if token_address.lower() == native_token_address.lower():
            return

        user_address = self.config.get_wallet_address()
        token_details = fetch_erc20_details(self.web3, token_address)
        allowance = token_details.contract.functions.allowance(
            to_checksum_address(user_address),
            self.contract_addresses.syntheticsrouter,
        ).call()

        if allowance < amount:
            raise ValueError(
                f"Insufficient token approval for {token_details.symbol}. Required: {amount / (10**token_details.decimals):.4f}, Current allowance: {allowance / (10**token_details.decimals):.4f}. Please approve tokens first using: token.approve('{self.contract_addresses.syntheticsrouter}', amount)",
            )
