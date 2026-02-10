"""Velora (ParaSwap) swap support for Lagoon vaults.

Velora is a DEX aggregator that executes swaps atomically, unlike CowSwap
which uses an offchain order book and presigning.

Flow:

1. Fetch quote from Velora API (GET /prices)
2. Build swap transaction from Velora API (POST /transactions/:network)
3. Approve TokenTransferProxy via vault's performCall()
4. Execute swap via swapAndValidateVelora() on TradingStrategyModuleV0

See `Velora developer documentation <https://developers.velora.xyz>`__ for more details.
"""

import datetime
import logging
from decimal import Decimal
from typing import TypeAlias, Callable, Any

from web3 import Web3
from web3.contract.contract import ContractFunction
from web3._utils.events import EventLogErrorFlags
from hexbytes import HexBytes
from eth_typing import HexAddress

from eth_defi.abi import get_contract
from eth_defi.hotwallet import HotWallet
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.token import TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.velora.api import get_augustus_swapper, get_token_transfer_proxy
from eth_defi.velora.quote import VeloraQuote, fetch_velora_quote
from eth_defi.velora.swap import VeloraSwapResult, VeloraSwapTransaction, fetch_velora_swap_transaction


logger = logging.getLogger(__name__)

#: How we broadcast and confirm transactions
#:
#: def callback(web3, asset_manager: HexAddress | HotWallet, func: ContractFunction) -> tx hash:
BroadcastCallback: TypeAlias = Callable[[Web3, Any, ContractFunction], HexBytes]


def _default_broadcast_callback(web3: Web3, asset_manager: HexAddress | HotWallet, func: ContractFunction) -> HexBytes:
    """Default broadcast callback which sends the signed transaction and waits for confirmation."""

    if isinstance(asset_manager, HotWallet):
        tx = asset_manager.sign_bound_call_with_new_nonce(func)
        tx_hash = web3.eth.send_raw_transaction(tx.raw_transaction)
    else:
        tx_hash = func.transact({"from": asset_manager})

    assert_transaction_success_with_explanation(web3, tx_hash)
    return tx_hash


def approve_velora(
    vault: LagoonVault,
    token: TokenDetails,
    amount: Decimal,
) -> ContractFunction:
    """Approve Velora TokenTransferProxy to spend tokens on behalf of the vault.

    .. warning::

        Approve TokenTransferProxy, NOT Augustus Swapper.
        Funds may be lost if approved to Augustus directly.

    :param vault:
        Lagoon vault instance

    :param token:
        Token to approve

    :param amount:
        Amount to approve (human-readable decimals)

    :return:
        Contract function to execute via performCall
    """
    assert isinstance(vault, LagoonVault), f"Not a Lagoon vault: {type(vault)}"
    assert isinstance(token, TokenDetails), f"Not a TokenDetails: {type(token)}"

    chain_id = vault.web3.eth.chain_id
    token_transfer_proxy = get_token_transfer_proxy(chain_id)

    func = token.approve(token_transfer_proxy, amount)
    return vault.transact_via_trading_strategy_module(func)


def build_velora_swap(
    vault: LagoonVault,
    buy_token: TokenDetails,
    sell_token: TokenDetails,
    amount_in: Decimal,
    min_amount_out: Decimal,
    augustus_calldata: HexBytes,
) -> ContractFunction:
    """Build swapAndValidateVelora() call on TradingStrategyModuleV0.

    :param vault:
        Lagoon vault instance

    :param buy_token:
        Token to receive

    :param sell_token:
        Token to sell

    :param amount_in:
        Amount of sell_token to swap (human-readable decimals)

    :param min_amount_out:
        Minimum amount of buy_token to receive (human-readable decimals)

    :param augustus_calldata:
        Raw calldata from Velora API to execute on Augustus Swapper

    :return:
        Contract function to execute
    """
    assert isinstance(vault, LagoonVault), f"Not a Lagoon vault: {type(vault)}"
    assert isinstance(buy_token, TokenDetails), f"Not a TokenDetails: {type(buy_token)}"
    assert isinstance(sell_token, TokenDetails), f"Not a TokenDetails: {type(sell_token)}"
    assert isinstance(amount_in, Decimal), f"Not a Decimal: {type(amount_in)}"
    assert isinstance(min_amount_out, Decimal), f"Not a Decimal: {type(min_amount_out)}"

    chain_id = vault.web3.eth.chain_id
    augustus_swapper = get_augustus_swapper(chain_id)

    amount_in_raw = sell_token.convert_to_raw(amount_in)
    min_amount_out_raw = buy_token.convert_to_raw(min_amount_out)

    trading_strategy_module = vault.trading_strategy_module
    assert trading_strategy_module is not None, f"Vault has no trading strategy module: {vault}"

    logger.info(
        "Velora swap %s -> %s for %f (min out %f) via vault %s",
        sell_token.symbol,
        buy_token.symbol,
        amount_in,
        min_amount_out,
        vault.vault_address,
    )

    return trading_strategy_module.functions.swapAndValidateVelora(
        augustus_swapper,
        sell_token.address,
        buy_token.address,
        amount_in_raw,
        min_amount_out_raw,
        augustus_calldata,
    )


def execute_velora_swap(
    asset_manager: HotWallet | HexAddress,
    vault: LagoonVault,
    buy_token: TokenDetails,
    sell_token: TokenDetails,
    amount_in: Decimal,
    min_amount_out: Decimal,
    augustus_calldata: HexBytes,
    broadcast_callback: BroadcastCallback = _default_broadcast_callback,
) -> VeloraSwapResult:
    """Execute a Velora swap through the vault.

    This builds and broadcasts the swapAndValidateVelora() transaction
    and extracts the result from the VeloraSwapExecuted event.

    :param asset_manager:
        Hot wallet or address of the asset manager

    :param vault:
        Lagoon vault instance

    :param buy_token:
        Token to receive

    :param sell_token:
        Token to sell

    :param amount_in:
        Amount of sell_token to swap (human-readable decimals)

    :param min_amount_out:
        Minimum amount of buy_token to receive (human-readable decimals)

    :param augustus_calldata:
        Raw calldata from Velora API to execute on Augustus Swapper

    :param broadcast_callback:
        Callback to broadcast the transaction

    :return:
        VeloraSwapResult with transaction hash and amounts
    """
    web3 = vault.web3

    bound_func = build_velora_swap(
        vault=vault,
        buy_token=buy_token,
        sell_token=sell_token,
        amount_in=amount_in,
        min_amount_out=min_amount_out,
        augustus_calldata=augustus_calldata,
    )

    tx_hash = broadcast_callback(web3, asset_manager, bound_func)

    # Extract VeloraSwapExecuted event
    receipt = web3.eth.get_transaction_receipt(tx_hash)

    TradingStrategyModuleV0 = get_contract(
        web3,
        "safe-integration/TradingStrategyModuleV0.json",
    )

    events = list(TradingStrategyModuleV0.events.VeloraSwapExecuted().process_receipt(receipt, EventLogErrorFlags.Discard))

    assert len(events) == 1, f"Expected exactly one VeloraSwapExecuted event, got {len(events)} for {receipt}"

    event_args = events[0]["args"]

    return VeloraSwapResult(
        tx_hash=tx_hash,
        buy_token=buy_token,
        sell_token=sell_token,
        amount_sold=event_args["amountIn"],
        amount_bought=event_args["amountOut"],
    )


def approve_and_execute_velora_swap(
    asset_manager_wallet: HotWallet,
    vault: LagoonVault,
    buy_token: TokenDetails,
    sell_token: TokenDetails,
    amount_in: Decimal,
    broadcast_callback: BroadcastCallback = _default_broadcast_callback,
    slippage_bps: int = 250,
    api_timeout: datetime.timedelta = datetime.timedelta(seconds=30),
) -> VeloraSwapResult:
    """High-level function: fetch quote, build tx, approve, and execute swap.

    This is the main entry point for executing Velora swaps through a Lagoon vault.

    Example:

    .. code-block:: python

        from decimal import Decimal
        from eth_defi.erc_4626.vault_protocol.lagoon.velora import approve_and_execute_velora_swap
        from eth_defi.token import fetch_erc20_details

        weth = fetch_erc20_details(web3, "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1")
        usdc = fetch_erc20_details(web3, "0xaf88d065e77c8cC2239327C5EDb3A432268e5831")

        result = approve_and_execute_velora_swap(
            asset_manager_wallet=hot_wallet,
            vault=lagoon_vault,
            buy_token=usdc,
            sell_token=weth,
            amount_in=Decimal("0.1"),
            slippage_bps=100,  # 1% slippage
        )

        print(f"Swapped {result.get_amount_sold_decimal()} WETH for {result.get_amount_bought_decimal()} USDC")

    :param asset_manager_wallet:
        Hot wallet of the asset manager

    :param vault:
        Lagoon vault instance

    :param buy_token:
        Token to receive

    :param sell_token:
        Token to sell

    :param amount_in:
        Amount of sell_token to swap (human-readable decimals)

    :param broadcast_callback:
        Callback to broadcast transactions

    :param slippage_bps:
        Allowed slippage in basis points (e.g., 250 = 2.5%)

    :param api_timeout:
        API request timeout

    :return:
        VeloraSwapResult with transaction hash and amounts

    :raise VeloraAPIError:
        If the Velora API returns an error
    """
    web3 = vault.web3

    # 1. Fetch quote
    logger.info("Fetching Velora quote for %s -> %s", sell_token.symbol, buy_token.symbol)
    quote = fetch_velora_quote(
        from_=vault.safe_address,
        buy_token=buy_token,
        sell_token=sell_token,
        amount_in=amount_in,
        api_timeout=api_timeout,
    )
    logger.info("Velora quote: %s", quote.pformat())

    # 2. Build swap transaction
    logger.info("Building Velora swap transaction with %d bps slippage", slippage_bps)
    swap_tx = fetch_velora_swap_transaction(
        quote=quote,
        user_address=vault.safe_address,
        slippage_bps=slippage_bps,
        api_timeout=api_timeout,
    )

    # 3. Approve TokenTransferProxy
    logger.info("Approving TokenTransferProxy for %s %s", amount_in, sell_token.symbol)
    approve_func = approve_velora(vault, sell_token, amount_in)
    broadcast_callback(web3, asset_manager_wallet, approve_func)

    # 4. Execute swap
    logger.info("Executing Velora swap")
    result = execute_velora_swap(
        asset_manager=asset_manager_wallet,
        vault=vault,
        buy_token=buy_token,
        sell_token=sell_token,
        amount_in=swap_tx.amount_in,
        min_amount_out=swap_tx.min_amount_out,
        augustus_calldata=swap_tx.calldata,
        broadcast_callback=broadcast_callback,
    )

    logger.info(
        "Velora swap completed: %s %s -> %s %s (tx: %s)",
        result.get_amount_sold_decimal(),
        sell_token.symbol,
        result.get_amount_bought_decimal(),
        buy_token.symbol,
        result.tx_hash.hex(),
    )

    return result
