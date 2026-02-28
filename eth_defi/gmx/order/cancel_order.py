"""GMX order cancellation module.

Builds unsigned transactions to cancel pending limit orders (stop loss, take profit,
limit increase) by calling ``ExchangeRouter.cancelOrder(orderKey)`` wrapped in a
multicall. Cancel transactions require no ETH value, unlike order creation.

Example::

    from eth_defi.gmx.order.cancel_order import CancelOrder
    from eth_defi.gmx.order.pending_orders import fetch_pending_orders

    cancel = CancelOrder(config)

    # Fetch pending orders and cancel all stop losses
    stop_losses = [o for o in fetch_pending_orders(web3, "arbitrum", wallet_address) if o.is_stop_loss]

    if stop_losses:
        result = cancel.cancel_orders([o.order_key for o in stop_losses])
        tx = result.transaction.copy()
        del tx["nonce"]
        signed = wallet.sign_transaction_with_new_nonce(tx)
        tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
        web3.eth.wait_for_transaction_receipt(tx_hash)
"""

import logging
from dataclasses import dataclass

from eth_utils import to_checksum_address
from web3.types import TxParams

from eth_defi.compat import encode_abi_compat
from eth_defi.gas import estimate_gas_fees
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.constants import CANCEL_ORDER_GAS_LIMIT, DEFAULT_EXECUTION_BUFFER
from eth_defi.gmx.contracts import ContractAddresses, get_contract_addresses, get_exchange_router_contract

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CancelOrderResult:
    """Result of building a single order cancellation transaction.

    The :attr:`transaction` is an unsigned :class:`~web3.types.TxParams`
    dict that must be signed before submission.
    """

    #: Unsigned transaction ready for signing and submission
    transaction: TxParams

    #: The 32-byte order key that will be cancelled
    order_key: bytes

    #: Gas limit applied to the transaction
    gas_limit: int


@dataclass(slots=True)
class BatchCancelOrderResult:
    """Result of building a batch order cancellation transaction.

    Multiple ``cancelOrder`` calls are batched into a single ``multicall``
    transaction for gas efficiency.

    The :attr:`transaction` is an unsigned :class:`~web3.types.TxParams`
    dict that must be signed before submission.
    """

    #: Unsigned transaction ready for signing and submission
    transaction: TxParams

    #: The 32-byte order keys that will be cancelled
    order_keys: list[bytes]

    #: Gas limit applied to the transaction
    gas_limit: int


class CancelOrder:
    """Builds unsigned transactions to cancel pending GMX limit orders.

    Does **not** extend :class:`~eth_defi.gmx.order.base_order.BaseOrder` because
    cancellation does not require the heavy initialisation that order creation
    needs (oracle prices, market data, gas limits from DataStore). Only the
    ExchangeRouter contract and wallet address are required.

    Usage::

        cancel = CancelOrder(config)

        # Cancel a single order
        result = cancel.cancel_order(order_key)

        # Cancel multiple orders in one transaction
        result = cancel.cancel_orders([key1, key2, key3])

        # Sign and submit either result type:
        tx = result.transaction.copy()
        del tx["nonce"]
        signed = wallet.sign_transaction_with_new_nonce(tx)
        tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    """

    def __init__(self, config: GMXConfig) -> None:
        """Initialise the cancel order builder.

        :param config:
            GMX configuration with Web3 connection and wallet address.
        """
        self.config = config
        self.web3 = config.web3
        self.chain: str = config.get_chain()
        self.contract_addresses: ContractAddresses = get_contract_addresses(self.chain)
        self._exchange_router_contract = get_exchange_router_contract(self.web3, self.chain)
        self.chain_id: int = self.web3.eth.chain_id

    def cancel_order(self, order_key: bytes, execution_buffer: float = DEFAULT_EXECUTION_BUFFER) -> CancelOrderResult:
        """Build an unsigned transaction to cancel a single pending order.

        Encodes ``ExchangeRouter.cancelOrder(orderKey)`` and wraps it in a
        ``multicall`` call. No ETH value is sent.

        :param order_key:
            The 32-byte key identifying the order to cancel. Obtain this from
            :attr:`~eth_defi.gmx.order.pending_orders.PendingOrder.order_key`
            or from a previous order creation receipt.
        :param execution_buffer:
            Multiplier applied to ``web3.eth.gas_price`` to set the cancel
            transaction's ``maxFeePerGas``. Uses the same basis as open/close/sltp
            keeper fee calculations so gas costs are comparable.
            Defaults to :data:`~eth_defi.gmx.execution_buffer.DEFAULT_EXECUTION_BUFFER`.
        :return:
            :class:`CancelOrderResult` with the unsigned transaction.
        :raises ValueError:
            If no wallet address is configured in GMX config.
        """
        logger.info("Building cancel transaction for order %s", order_key.hex())

        gas_limit = CANCEL_ORDER_GAS_LIMIT
        transaction = self._build_cancel_transaction(
            multicall_args=[self._encode_cancel_order(order_key)],
            gas_limit=gas_limit,
            execution_buffer=execution_buffer,
        )

        logger.info(
            "Cancel transaction built: order_key=%s gas_limit=%d",
            order_key.hex(),
            gas_limit,
        )

        return CancelOrderResult(
            transaction=transaction,
            order_key=order_key,
            gas_limit=gas_limit,
        )

    def cancel_orders(
        self,
        order_keys: list[bytes],
        execution_buffer: float = DEFAULT_EXECUTION_BUFFER,
    ) -> BatchCancelOrderResult:
        """Build an unsigned transaction to cancel multiple pending orders at once.

        Batches all ``cancelOrder`` calls into a single ``multicall`` transaction
        for gas efficiency. Gas limit scales linearly with the number of orders.

        :param order_keys:
            List of 32-byte keys identifying the orders to cancel.
        :param execution_buffer:
            Multiplier applied to ``web3.eth.gas_price`` to set ``maxFeePerGas``.
            Defaults to :data:`~eth_defi.gmx.execution_buffer.DEFAULT_EXECUTION_BUFFER`.
        :return:
            :class:`BatchCancelOrderResult` with the unsigned multicall transaction.
        :raises ValueError:
            If ``order_keys`` is empty or no wallet address is configured.
        """
        if not order_keys:
            raise ValueError("order_keys must not be empty")

        logger.info(
            "Building batch cancel for %d order(s)",
            len(order_keys),
        )

        gas_limit = CANCEL_ORDER_GAS_LIMIT * len(order_keys)
        transaction = self._build_cancel_transaction(
            multicall_args=[self._encode_cancel_order(key) for key in order_keys],
            gas_limit=gas_limit,
            execution_buffer=execution_buffer,
        )

        logger.info(
            "Batch cancel transaction built: %d order(s) gas_limit=%d",
            len(order_keys),
            gas_limit,
        )

        return BatchCancelOrderResult(
            transaction=transaction,
            order_keys=order_keys,
            gas_limit=gas_limit,
        )

    def _encode_cancel_order(self, order_key: bytes) -> bytes:
        """Encode a single ``cancelOrder(bytes32)`` call for multicall.

        Uses the same ABI encoding pattern as
        :class:`~eth_defi.gmx.order.base_order.BaseOrder`.

        :param order_key:
            The 32-byte order key to encode.
        :return:
            ABI-encoded function call as raw bytes.
        """
        hex_data = encode_abi_compat(
            self._exchange_router_contract,
            "cancelOrder",
            [order_key],
        )
        if hex_data.startswith("0x"):
            hex_data = hex_data[2:]
        return bytes.fromhex(hex_data)

    def _build_cancel_transaction(
        self,
        multicall_args: list[bytes],
        gas_limit: int,
        execution_buffer: float = DEFAULT_EXECUTION_BUFFER,
    ) -> TxParams:
        """Build the final unsigned cancel transaction.

        Follows the same pattern as
        :meth:`~eth_defi.gmx.order.base_order.BaseOrder._build_transaction`
        but with ``value=0`` since cancellations do not send ETH.

        Gas pricing uses ``web3.eth.gas_price * execution_buffer`` — the same
        basis as open/close/sltp keeper fee calculations — instead of
        ``estimate_gas_fees()`` which includes ``maxPriorityFeePerGas`` from
        the Arbitrum RPC.  On Arbitrum the suggested priority fee can be ~1.5 gwei
        while the actual base fee is ~0.01 gwei, inflating cancel costs 100x.

        :param multicall_args:
            List of ABI-encoded ``cancelOrder`` calls to batch.
        :param gas_limit:
            Gas limit for the transaction.
        :param execution_buffer:
            Multiplier applied to ``web3.eth.gas_price`` to set ``maxFeePerGas``.
        :return:
            Unsigned :class:`~web3.types.TxParams` ready for signing.
        :raises ValueError:
            If no wallet address is configured in GMX config.
        """
        user_address = self.config.get_wallet_address()
        if not user_address:
            raise ValueError("User wallet address required for order cancellation")

        nonce = self.web3.eth.get_transaction_count(to_checksum_address(user_address))

        # Use web3.eth.gas_price (≈ base fee on Arbitrum) multiplied by
        # execution_buffer — identical to how BaseOrder calculates its keeper
        # fee basis.  This avoids the inflated maxPriorityFeePerGas returned
        # by eth_maxPriorityFeePerGas on Arbitrum (~1.5 gwei vs 0.01 gwei base).
        gas_price = self.web3.eth.gas_price
        buffered_gas_price = int(gas_price * execution_buffer)

        logger.info(
            "Cancel order gas pricing: gas_price=%.4f gwei, execution_buffer=%.1fx → maxFeePerGas=%.4f gwei",
            gas_price / 1e9,
            execution_buffer,
            buffered_gas_price / 1e9,
        )

        # Use estimate_gas_fees only to detect EIP-1559 support; the actual
        # fee values are overridden with the buffered gas_price.
        gas_fees = estimate_gas_fees(self.web3)

        transaction: TxParams = {
            "from": to_checksum_address(user_address),
            "to": self.contract_addresses.exchangerouter,
            "data": encode_abi_compat(
                self._exchange_router_contract,
                "multicall",
                [multicall_args],
            ),
            "value": 0,
            "gas": gas_limit,
            "chainId": self.chain_id,
            "nonce": nonce,
        }

        if gas_fees.max_fee_per_gas is not None:
            # EIP-1559 chain (Arbitrum, Avalanche): set maxFeePerGas from
            # gas_price * execution_buffer; zero priority tip (sequencer ignores it).
            transaction["maxFeePerGas"] = buffered_gas_price
            transaction["maxPriorityFeePerGas"] = 0
        else:
            transaction["gasPrice"] = buffered_gas_price

        return transaction
