"""Cross-chain gas feeding using LI.FI.

Check gas balances across multiple EVM chains and bridge native
tokens from a source chain to any target chain that is running low.

Example:

.. code-block:: python

    from decimal import Decimal
    from eth_defi.hotwallet import HotWallet
    from eth_defi.lifi.crosschain import prepare_crosschain_swaps, execute_crosschain_swaps
    from eth_defi.provider.multi_provider import create_multi_provider_web3

    source_web3 = create_multi_provider_web3(os.environ["JSON_RPC_ARBITRUM"])
    target_web3s = {
        8453: create_multi_provider_web3(os.environ["JSON_RPC_BASE"]),
        137: create_multi_provider_web3(os.environ["JSON_RPC_POLYGON"]),
    }

    wallet = HotWallet.from_private_key(os.environ["PRIVATE_KEY"])
    wallet.sync_nonce(source_web3)

    swaps = prepare_crosschain_swaps(
        wallet=wallet,
        source_web3=source_web3,
        target_web3s=target_web3s,
        min_gas_usd=Decimal("5"),
        top_up_usd=Decimal("20"),
    )

    for swap in swaps:
        print(swap)

    results = execute_crosschain_swaps(
        wallet=wallet,
        source_web3=source_web3,
        swaps=swaps,
    )
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from hexbytes import HexBytes
from tqdm_loggable.auto import tqdm
from web3 import Web3

from eth_defi.chain import get_chain_name
from eth_defi.confirmation import broadcast_and_wait_transactions_to_complete
from eth_defi.gas import apply_gas, estimate_gas_price
from eth_defi.hotwallet import HotWallet
from eth_defi.lifi.api import fetch_lifi_native_token_prices, fetch_lifi_token_price_usd
from eth_defi.lifi.constants import (
    DEFAULT_MIN_GAS_USD,
    DEFAULT_TOP_UP_GAS_USD,
    LIFI_NATIVE_TOKEN_ADDRESS,
)
from eth_defi.lifi.quote import LifiQuote, fetch_lifi_quote
from eth_defi.token import TokenDetails, fetch_erc20_details


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CrossChainGasConfig:
    """Per-chain gas configuration for cross-chain feeding.

    Allows overriding the default minimum gas threshold and
    top-up amount on a per-chain basis.
    """

    #: Chain ID this configuration applies to
    chain_id: int

    #: Minimum gas balance in USD before triggering a top-up
    min_gas_usd: Decimal

    #: Amount of gas to bridge (in USD) when topping up
    top_up_usd: Decimal


@dataclass(slots=True, frozen=True)
class CrossChainSwap:
    """A prepared cross-chain gas top-up swap.

    Contains all information needed to execute a bridge transaction
    from the source chain to a target chain.
    """

    #: Source chain ID where funds are bridged from
    source_chain_id: int

    #: Target chain ID where gas is needed
    target_chain_id: int

    #: Amount of source token to send (raw units: wei for native, or token decimals for ERC-20)
    from_amount_raw: int

    #: Value of the bridged amount in USD
    from_amount_usd: Decimal

    #: Current gas balance on the target chain in USD
    target_balance_usd: Decimal

    #: Minimum gas threshold that triggered this swap (USD)
    min_gas_usd: Decimal

    #: Requested top-up amount in USD
    top_up_usd: Decimal

    #: Ready-to-sign transaction request from LI.FI
    #:
    #: Contains ``from``, ``to``, ``data``, ``value``,
    #: ``gasLimit``, ``gasPrice``, ``chainId``.
    transaction_request: dict

    #: The LI.FI quote used to build this swap
    quote: LifiQuote

    def __str__(self) -> str:
        source_name = get_chain_name(self.source_chain_id)
        target_name = get_chain_name(self.target_chain_id)
        return f"CrossChainSwap: {source_name} -> {target_name}\n  Target balance: ${self.target_balance_usd:.2f} (min: ${self.min_gas_usd:.2f})\n  Bridge amount:  ${self.from_amount_usd:.2f} (top-up: ${self.top_up_usd:.2f})\n  From amount:    {self.from_amount_raw} raw\n  Est. duration:  {self.quote.execution_duration}s"


@dataclass(slots=True, frozen=True)
class CrossChainSwapResult:
    """Result of an executed cross-chain gas top-up."""

    #: The swap that was executed
    swap: CrossChainSwap

    #: Transaction hash on the source chain
    tx_hash: HexBytes

    def __str__(self) -> str:
        source_name = get_chain_name(self.swap.source_chain_id)
        target_name = get_chain_name(self.swap.target_chain_id)
        return f"CrossChainSwapResult: {source_name} -> {target_name}\n  Tx hash: {self.tx_hash.hex()}\n  Amount:  ${self.swap.from_amount_usd:.2f}"


def fetch_crosschain_gas_balances(
    target_web3s: dict[int, Web3],
    wallet_address: str,
    api_timeout: float = 30,
) -> tuple[dict[int, Decimal], dict[int, Decimal]]:
    """Fetch native token balances and their USD values across chains.

    :param target_web3s:
        Dict mapping chain_id to Web3 instance

    :param wallet_address:
        Wallet address to check balances for

    :param api_timeout:
        API request timeout in seconds

    :return:
        Tuple of (balances_native, balances_usd) where each is a
        dict mapping chain_id to balance.
        ``balances_native`` is in native token units (e.g. ETH),
        ``balances_usd`` is the USD equivalent.
    """
    chain_ids = list(target_web3s.keys())

    # Fetch native token prices from LI.FI
    prices = fetch_lifi_native_token_prices(chain_ids, api_timeout=api_timeout)

    balances_native = {}
    balances_usd = {}

    for chain_id, web3 in target_web3s.items():
        balance_wei = web3.eth.get_balance(wallet_address)
        balance_native = Decimal(balance_wei) / Decimal(10**18)
        balances_native[chain_id] = balance_native

        price_usd = prices.get(chain_id, Decimal("0"))
        balances_usd[chain_id] = balance_native * price_usd

        chain_name = get_chain_name(chain_id)
        logger.info(
            "Chain %s (%s): balance=%s native ($%s)",
            chain_id,
            chain_name,
            balance_native,
            balances_usd[chain_id],
        )

    return balances_native, balances_usd


def prepare_crosschain_swaps(
    wallet: HotWallet,
    source_web3: Web3,
    target_web3s: dict[int, Web3],
    min_gas_usd: Decimal = DEFAULT_MIN_GAS_USD,
    top_up_usd: Decimal = DEFAULT_TOP_UP_GAS_USD,
    source_token_address: str = LIFI_NATIVE_TOKEN_ADDRESS,
    chain_configs: dict[int, CrossChainGasConfig] | None = None,
    slippage: float = 0.03,
    api_timeout: float = 30,
    progress: bool = True,
) -> list[CrossChainSwap]:
    """Check gas balances and prepare cross-chain bridge swaps for chains that need topping up.

    Checks the native token balance on each target chain. If any chain
    has less than ``min_gas_usd`` worth of native token, prepares a
    LI.FI bridge quote to send tokens from the source chain.

    The source token can be either the native gas token (default) or an
    ERC-20 token such as USDC. When using an ERC-20 source token, the
    amount to bridge is calculated using the token's on-chain decimals
    via :py:class:`~eth_defi.token.TokenDetails`, and the USD price is
    fetched from the LI.FI token API. LI.FI handles the swap from the
    source token to the target chain's native token.

    Chains are identified by numeric EVM chain IDs (e.g. 1 for Ethereum,
    42161 for Arbitrum). These are our internal chain IDs from
    :py:data:`eth_defi.chain.CHAIN_NAMES` and are passed directly to the
    LI.FI API as ``fromChain``/``toChain`` parameters, which also uses
    numeric chain IDs. The corresponding RPC URLs are expected in
    ``JSON_RPC_{CHAIN_NAME}`` environment variables, resolved via
    :py:func:`eth_defi.provider.env.read_json_rpc_url`.

    :param wallet:
        Hot wallet that holds funds on the source chain

    :param source_web3:
        Web3 connection to the source chain.
        The chain ID is read from ``source_web3.eth.chain_id``.

    :param target_web3s:
        Dict mapping chain_id to Web3 connection for each target chain.
        Keys are numeric EVM chain IDs.

    :param min_gas_usd:
        Default minimum gas balance in USD. Chains below this trigger a top-up.

    :param top_up_usd:
        Default amount to bridge in USD when topping up.

    :param source_token_address:
        Address of the token to bridge from the source chain.
        Use :py:data:`~eth_defi.lifi.constants.LIFI_NATIVE_TOKEN_ADDRESS` (default)
        for native gas token, or an ERC-20 address (e.g. USDC from
        :py:data:`~eth_defi.token.USDC_NATIVE_TOKEN`).

    :param chain_configs:
        Optional per-chain overrides for min_gas_usd and top_up_usd.
        Keys are chain IDs.

    :param slippage:
        Maximum allowed slippage as a decimal (0.03 = 3%)

    :param api_timeout:
        API request timeout in seconds

    :param progress:
        Show a ``tqdm`` progress bar while fetching quotes (default ``True``)

    :return:
        List of prepared swaps. Empty if all chains have sufficient gas.
    """
    if chain_configs is None:
        chain_configs = {}

    source_chain_id = source_web3.eth.chain_id
    use_native_source = source_token_address == LIFI_NATIVE_TOKEN_ADDRESS

    # Fetch native token prices for all chains (needed for target balance checks)
    all_chain_ids = list(target_web3s.keys()) + [source_chain_id]
    prices = fetch_lifi_native_token_prices(all_chain_ids, api_timeout=api_timeout)

    # Determine source token price and details
    if use_native_source:
        source_price = prices.get(source_chain_id, Decimal("0"))
        source_token_details = None
    else:
        source_price = fetch_lifi_token_price_usd(
            chain_id=source_chain_id,
            token_address=source_token_address,
            api_timeout=api_timeout,
        )
        source_token_details = fetch_erc20_details(source_web3, source_token_address)
        logger.info(
            "Source token: %s (%s) on chain %s, price=$%s",
            source_token_details.symbol,
            source_token_address,
            source_chain_id,
            source_price,
        )

    if source_price == 0:
        raise ValueError(f"Could not fetch token price for source chain {source_chain_id}, token {source_token_address}")

    # Check balances on target chains
    swaps = []
    items = target_web3s.items()
    if progress:
        items = tqdm(list(items), desc="Fetching cross-chain quotes", unit="chain")
    for chain_id, web3 in items:
        balance_wei = web3.eth.get_balance(wallet.address)
        balance_native = Decimal(balance_wei) / Decimal(10**18)
        price_usd = prices.get(chain_id, Decimal("0"))
        balance_usd = balance_native * price_usd

        # Determine thresholds for this chain
        config = chain_configs.get(chain_id)
        chain_min_gas = config.min_gas_usd if config else min_gas_usd
        chain_top_up = config.top_up_usd if config else top_up_usd

        chain_name = get_chain_name(chain_id)
        logger.info(
            "Chain %s (%s): balance=$%.2f, min=$%.2f",
            chain_id,
            chain_name,
            balance_usd,
            chain_min_gas,
        )

        if balance_usd >= chain_min_gas:
            logger.info("Chain %s (%s): gas balance sufficient, skipping", chain_id, chain_name)
            continue

        # Calculate how much source token to bridge
        if use_native_source:
            from_amount_native = chain_top_up / source_price
            from_amount_raw = int(from_amount_native * Decimal(10**18))
        else:
            from_amount_decimal = chain_top_up / source_price
            from_amount_raw = source_token_details.convert_to_raw(from_amount_decimal)

        logger.info(
            "Chain %s (%s): needs top-up. Bridging $%.2f (%s raw from source)",
            chain_id,
            chain_name,
            chain_top_up,
            from_amount_raw,
        )

        # Fetch LI.FI quote: source token -> native token on target
        quote = fetch_lifi_quote(
            from_chain_id=source_chain_id,
            to_chain_id=chain_id,
            from_token=source_token_address,
            to_token=LIFI_NATIVE_TOKEN_ADDRESS,
            from_amount=from_amount_raw,
            from_address=wallet.address,
            slippage=slippage,
            api_timeout=api_timeout,
        )

        tx_request = quote.get_transaction_request()
        if not tx_request:
            logger.warning("No transaction request in LI.FI quote for chain %s, skipping", chain_id)
            continue

        swap = CrossChainSwap(
            source_chain_id=source_chain_id,
            target_chain_id=chain_id,
            from_amount_raw=from_amount_raw,
            from_amount_usd=chain_top_up,
            target_balance_usd=balance_usd,
            min_gas_usd=chain_min_gas,
            top_up_usd=chain_top_up,
            transaction_request=tx_request,
            quote=quote,
        )

        swaps.append(swap)
        logger.info("Prepared swap: %s", swap)

    return swaps


def execute_crosschain_swaps(
    wallet: HotWallet,
    source_web3: Web3,
    swaps: list[CrossChainSwap],
) -> list[CrossChainSwapResult]:
    """Execute prepared cross-chain gas top-up swaps sequentially.

    Signs and broadcasts each swap transaction on the source chain.
    Waits for each transaction to confirm before proceeding to the next.

    :param wallet:
        Hot wallet to sign transactions with

    :param source_web3:
        Web3 connection to the source chain

    :param swaps:
        List of prepared swaps from :py:func:`prepare_crosschain_swaps`

    :return:
        List of results with transaction hashes

    :raise Reverted:
        If any transaction fails on-chain
    """
    if not swaps:
        logger.info("No swaps to execute")
        return []

    wallet.sync_nonce(source_web3)
    results = []

    for i, swap in enumerate(swaps):
        target_name = get_chain_name(swap.target_chain_id)
        logger.info(
            "Executing swap %d/%d: -> %s ($%.2f)",
            i + 1,
            len(swaps),
            target_name,
            swap.from_amount_usd,
        )

        tx_request = swap.transaction_request.copy()

        def _parse_int(val) -> int:
            """Parse a hex string or int from LI.FI response."""
            if isinstance(val, str):
                return int(val, 16) if val.startswith("0x") else int(val)
            return int(val)

        # Convert hex strings from LI.FI response to int where needed
        tx = {
            "from": wallet.address,
            "to": Web3.to_checksum_address(tx_request["to"]),
            "data": tx_request["data"],
            "value": _parse_int(tx_request.get("value", 0)),
            "chainId": swap.source_chain_id,
        }

        # Gas limit from the quote
        if "gasLimit" in tx_request:
            tx["gas"] = _parse_int(tx_request["gasLimit"])

        # Always use fresh gas pricing from the node instead of the
        # quote values, which may be stale by the time we broadcast
        # (e.g. maxFeePerGas < baseFee). estimate_gas_price() includes
        # a safety buffer for base fee fluctuations.
        gas_price = estimate_gas_price(source_web3)
        apply_gas(tx, gas_price)

        signed_tx = wallet.sign_transaction_with_new_nonce(tx)

        receipts = broadcast_and_wait_transactions_to_complete(
            source_web3,
            [signed_tx],
            confirm_ok=True,
        )

        tx_hash = list(receipts.keys())[0]
        result = CrossChainSwapResult(swap=swap, tx_hash=tx_hash)
        results.append(result)

        logger.info("Swap %d/%d confirmed: %s", i + 1, len(swaps), result)

    return results
