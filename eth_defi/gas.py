"""Gas price strategies.

`Web3.py no longer support gas price strategies post London hard work <https://web3py.readthedocs.io/en/stable/gas_price.html>`_.
"""

import enum
from dataclasses import dataclass
from pprint import pformat
from typing import Optional

from web3 import Web3
from web3.gas_strategies.rpc import rpc_gas_price_strategy

#: Safety buffer multiplier applied to ``maxFeePerGas`` in EIP-1559 transactions.
#:
#: On L2 chains like Arbitrum, the base fee can fluctuate between the time
#: gas is estimated and the time the signed transaction reaches the sequencer.
#: Without a buffer, this race condition causes rejections with:
#:
#: ``max fee per gas less than block base fee``
#:
#: A 12% buffer absorbs typical L2 base fee volatility while keeping
#: overpayment minimal (unused gas fee is refunded by the protocol).
#:
#: For more information see:
#:
#: - `Arbitrum gas fees documentation <https://docs.arbitrum.io/how-arbitrum-works/gas-fees>`_
#: - `Arbitrum base fee discussion on GitHub <https://github.com/OffchainLabs/nitro/issues/1>`_
#: - `EIP-1559 specification <https://eips.ethereum.org/EIPS/eip-1559>`_
#:
GAS_PRICE_BUFFER_MULTIPLIER = 1.12

#: Per-chain minimum ``maxPriorityFeePerGas`` floors in wei, keyed by chain id.
#:
#: The ``eth_maxPriorityFeePerGas`` RPC suggestion follows recently included
#: transactions and on a quiet Ethereum mainnet can collapse to an effectively
#: zero tip (e.g. 375,524 wei = 0.0004 gwei). Block builders order transactions
#: by effective tip, so a near-zero tip transaction can linger unmined for
#: minutes when the base fee ticks up. This aborted a multichain Lagoon vault
#: deployment on 2026-06-10: a Safe ``addOwnerWithThreshold()`` transaction
#: with a 375,524 wei tip took ~3.5 minutes (17 blocks) to confirm, blowing
#: the 120 second receipt timeout.
#:
#: Floors:
#:
#: - Ethereum mainnet (1): 1 gwei. Practically guarantees next-block inclusion,
#:   while costing only fractions of a dollar for a typical transaction and a
#:   few dollars even for a multi-million gas contract deployment.
#:
#: - Polygon (137): 30 gwei, the protocol-enforced spam prevention minimum.
#:
#: Rollups (Arbitrum, Base, etc.) are deliberately not listed: their sequencers
#: include zero-tip transactions normally, and a flat floor would overpay
#: heavily relative to their tiny base fees.
#:
#: Override per call with the ``min_priority_fee`` argument of
#: :py:func:`estimate_gas_price`.
#:
MIN_PRIORITY_FEE_PER_CHAIN: dict[int, int] = {
    1: 1_000_000_000,
    137: 30_000_000_000,
}


class GasPriceMethod(enum.Enum):
    """What method we did use for setting the gas price."""

    #: Legacy chains
    legacy = "legacy"

    #: Post London hard work
    london = "london"


@dataclass
class GasPriceSuggestion:
    """Gas price details.

    Capture the necessary information for the gas price to used during the transaction building.

    - EIP-1559 London hard fork chains (Ethereumm mainnet)

    - Legacy EVM: Polygon, BNB Chain
    """

    #: How the gas price was determined
    method: GasPriceMethod

    #: Non London hard fork chains
    legacy_gas_price: Optional[int] = None

    #: London hard fork chains
    base_fee: Optional[int] = None

    #: London hard fork chains
    max_priority_fee_per_gas: Optional[int] = None

    #: London hard fork chains
    max_fee_per_gas: Optional[int] = None

    def __repr__(self):
        return f"<Gas pricing method:{self.method.name} base:{self.base_fee} priority:{self.max_priority_fee_per_gas} max:{self.max_fee_per_gas} legacy:{self.legacy_gas_price}>"

    def get_tx_gas_params(self) -> dict:
        """Get gas params as they are applied to ContractFunction.build_transaction()"""
        if self.base_fee is not None:
            return {"maxPriorityFeePerGas": self.max_priority_fee_per_gas, "maxFeePerGas": self.max_fee_per_gas}
        else:
            return {"gasPrice": self.legacy_gas_price}

    def pformat(self) -> str:
        """Pretty format for logging."""

        def _format(value: Optional[int]) -> str:
            if value is None:
                return "-"
            return f"{value / 10**9:.2f}G ({value:,})"

        data = {
            "Base Fee": _format(self.base_fee),
            "Max priority fee per gas": _format(self.max_priority_fee_per_gas),
            "Max fee per gas": _format(self.max_fee_per_gas),
        }
        return pformat(data)


def estimate_gas_price(
    web3: Web3,
    method=None,
    gas_price_buffer_multiplier: float = GAS_PRICE_BUFFER_MULTIPLIER,
    min_priority_fee: int | None = None,
) -> GasPriceSuggestion:
    """Get a good gas price for a transaction.

    Applies a safety buffer to ``maxFeePerGas`` to absorb base fee fluctuations
    between estimation and transaction submission.
    See :py:data:`GAS_PRICE_BUFFER_MULTIPLIER` for details.

    On EIP-1559 chains, the RPC suggested priority fee is floored to a per-chain
    minimum so transactions do not get stuck in the mempool with an effectively
    zero tip. See :py:data:`MIN_PRIORITY_FEE_PER_CHAIN` for details.

    :param web3:
        Web3 instance connected to a node.

    :param method:
        Force a specific gas pricing method.
        If ``None``, auto-detect based on whether the chain supports EIP-1559.

    :param gas_price_buffer_multiplier:
        Multiplier applied to ``maxFeePerGas`` to absorb base fee fluctuations.
        Defaults to :py:data:`GAS_PRICE_BUFFER_MULTIPLIER` (1.12 = 12% buffer).
        Set to ``1.0`` to disable the buffer.

    :param min_priority_fee:
        Minimum ``maxPriorityFeePerGas`` in wei for EIP-1559 chains.

        The RPC suggested priority fee is raised to at least this value.
        If ``None``, the per-chain default from
        :py:data:`MIN_PRIORITY_FEE_PER_CHAIN` is used, with no floor applied
        for chains not listed there. Set to ``0`` to always use the raw RPC
        suggestion.
    """

    last_block = web3.eth.get_block("latest")
    base_fee = last_block.get("baseFeePerGas")

    if method is None:
        if base_fee is not None:
            method = GasPriceMethod.london
        else:
            method = GasPriceMethod.legacy

    if method == GasPriceMethod.london:
        # London gas strategy
        # see https://github.com/ethereum/web3.py/blob/c70f7fbe1cfa98b1ce8597a08c99e05759a9667b/web3/_utils/transactions.py#L57
        # see https://github.com/ethereum/web3.py/blob/36adb16c68f570c343d01ecc8d0096cbac814172/web3/middleware/gas_price_strategy.py#L57
        if min_priority_fee is None:
            min_priority_fee = MIN_PRIORITY_FEE_PER_CHAIN.get(web3.eth.chain_id, 0)

        # Floor the RPC suggestion: a near-zero tip can leave the transaction
        # unmined for minutes, see MIN_PRIORITY_FEE_PER_CHAIN
        max_priority_fee_per_gas = max(min_priority_fee, web3.eth.max_priority_fee)

        max_fee_per_gas = max_priority_fee_per_gas + (2 * base_fee)

        # Apply safety buffer to absorb L2 base fee volatility between
        # estimation and submission. On Arbitrum the sequencer can adjust
        # the base fee in the time it takes to sign and broadcast, causing
        # "max fee per gas less than block base fee" rejections.
        max_fee_per_gas = int(max_fee_per_gas * gas_price_buffer_multiplier)

        # https://github.com/ethereum/go-ethereum/blob/2e478aab98c13577c66b4531ba240a601dbc1516/core/error.go#L87
        if max_priority_fee_per_gas > max_fee_per_gas:
            max_fee_per_gas = max_priority_fee_per_gas

        return GasPriceSuggestion(method=GasPriceMethod.london, base_fee=base_fee, max_priority_fee_per_gas=max_priority_fee_per_gas, max_fee_per_gas=max_fee_per_gas)
    else:
        # Legacy gas strategy
        return GasPriceSuggestion(method=GasPriceMethod.legacy, legacy_gas_price=web3.eth.generate_gas_price())


# Legacy
estimate_gas_fees = estimate_gas_price


def apply_gas(tx: dict, suggestion: GasPriceSuggestion) -> dict:
    """Apply gas fees to a raw transaction dict.

    Example:

    .. code-block::

        from web3 import Web3
        from web3._utils.transactions import fill_nonce
        from eth_account.signers.local import LocalAccount

        web3: Web3
        hot_wallet: LocalAccount

        # Move 10 tokens from deployer to user1
        tx = token.functions.transfer(hot_wallet.address, 10 * 10**18).build_transaction({
            "from": hot_wallet.address,
            'chainId': web3.eth.chain_id,
            "gas": 150_000,  # 150k gas should be more than enough for ERC20.transfer()
        })

        tx = fill_nonce(web3, tx)
        gas_fees = estimate_gas_fees(web3)
        apply_gas(tx, gas_fees)

        signed = hot_wallet.sign_transaction(tx)
        raw_bytes = get_tx_broadcast_data(signed)
        tx_hash = web3.eth.send_raw_transaction(raw_bytes)
        receipt = web3.eth.get_transaction_receipt(tx_hash)

    :return:
        Mutated dict

    """

    assert isinstance(tx, dict), f"Expected tx to be dict, got {type(tx)}"

    if suggestion.method == GasPriceMethod.london:
        tx["maxFeePerGas"] = suggestion.max_fee_per_gas
        tx["maxPriorityFeePerGas"] = suggestion.max_priority_fee_per_gas

        if "gasPrice" in tx:
            # Cannot have both maxFeePerGas + maxPriorityFeePerGas and gasPrice
            del tx["gasPrice"]
    else:
        tx["gasPrice"] = suggestion.legacy_gas_price

    return tx


def node_default_gas_price_strategy(web3: Web3, transaction_params: dict) -> int:
    """Gas price strategy for blockchains not supporting dynamic gas fees.

    This gas price strategy will query the JSON-RPC for the suggested flat fee.
    It works on chains that do not support EIP-1559 London hardfork style
    base fee + max fee dynamic pricing.

    These include

    - BNB Chain

    Example:

    .. code-block::

        from eth_defi.gas import node_default_gas_price_strategy
        web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)

    For more information see

    - https://web3py.readthedocs.io/en/stable/gas_price.html

    - https://www.blockchain-council.org/ethereum/eip-1559/
    """
    node_default_price = rpc_gas_price_strategy(web3)
    return node_default_price
