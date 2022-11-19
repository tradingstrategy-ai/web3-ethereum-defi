"""Gas price strategies.

`Web3.py no longer support gas price strategies post London hard work <https://web3py.readthedocs.io/en/stable/gas_price.html>`_.
"""
import enum
from dataclasses import dataclass
from typing import Optional

from web3 import Web3
from web3.gas_strategies.rpc import rpc_gas_price_strategy


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


def estimate_gas_fees(web3: Web3) -> GasPriceSuggestion:
    """Get a good gas price for a transaction.

    TODO: This is non-optimal, first draft implementation.
    """

    last_block = web3.eth.get_block("latest")
    base_fee = last_block.get("baseFeePerGas")
    if base_fee is not None:
        # London gas strategy
        # see https://github.com/ethereum/web3.py/blob/c70f7fbe1cfa98b1ce8597a08c99e05759a9667b/web3/_utils/transactions.py#L57
        # see https://github.com/ethereum/web3.py/blob/36adb16c68f570c343d01ecc8d0096cbac814172/web3/middleware/gas_price_strategy.py#L57
        base_fee = base_fee
        max_fee_per_gas = web3.eth.max_priority_fee + (2 * base_fee)
        max_priority_fee_per_gas = web3.eth.max_priority_fee
        return GasPriceSuggestion(method=GasPriceMethod.london, base_fee=base_fee, max_priority_fee_per_gas=max_priority_fee_per_gas, max_fee_per_gas=max_fee_per_gas)
    else:
        # Legacy gas strategy
        return GasPriceSuggestion(method=GasPriceMethod.legacy, legacy_gas_price=web3.eth.generate_gas_price())


def apply_gas(tx: dict, suggestion: GasPriceSuggestion):
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
        tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = web3.eth.get_transaction_receipt(tx_hash)

    """
    if suggestion.method == GasPriceMethod.london:
        tx["maxFeePerGas"] = suggestion.max_fee_per_gas
        tx["maxPriorityFeePerGas"] = suggestion.max_priority_fee_per_gas
    else:
        tx["gasPrice"] = suggestion.legacy_gas_price


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
