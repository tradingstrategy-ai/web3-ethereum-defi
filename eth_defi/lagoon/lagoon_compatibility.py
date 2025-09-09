"""Lagoon token compatibility checks."""

import datetime
import logging
import pickle
import textwrap
from dataclasses import dataclass, field, asdict
from decimal import Decimal
from pathlib import Path
from pprint import pformat
from statistics import mean

from web3 import Web3
from web3.contract.contract import ContractFunction
from eth_typing import HexAddress

from tqdm_loggable.auto import tqdm

from eth_defi.chain import get_chain_name
from eth_defi.lagoon.vault import LagoonVault
from eth_defi.provider.anvil import mine, launch_anvil, set_balance
from eth_defi.provider.fallback import FallbackProvider
from eth_defi.provider.mev_blocker import MEVBlockerProvider
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details, is_stablecoin_like
from eth_defi.trace import assert_transaction_success_with_explanation, TransactionAssertionError
from eth_defi.trade import TradeSuccess
from eth_defi.uniswap_v2.constants import UNISWAP_V2_DEPLOYMENTS
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment, fetch_deployment
from eth_defi.uniswap_v2.fees import estimate_buy_received_amount_raw, estimate_sell_received_amount_raw
from eth_defi.uniswap_v2.liquidity import get_liquidity
from eth_defi.uniswap_v2.swap import swap_with_slippage_protection
from eth_defi.vault.base import VaultSpec
from eth_defi.velvet.analysis import analyse_trade_by_receipt_generic


logger = logging.getLogger(__name__)


@dataclass
class LagoonTokenCompatibilityData:
    """Was an ERC-20 compatible with Lagoon Vault?

    - Vault can buy and sell the token
    - Only applicable to Uniswap v2 kind AMMs
    """

    created_at: datetime.datetime

    #: What was the base address of the token
    token_address: str

    pair_address: str

    #: Used routing path
    path: list[str]

    tokens: list[str]

    available_liquidity: Decimal

    minimum_liquidity_threshold: Decimal

    buy_block_number: int

    estimate_buy_received: int

    buy_amount_raw: int

    buy_result: TradeSuccess | None

    buy_real_received: int

    sell_block_number: int | None = None

    estimate_sell_received: int | None = None

    sell_result: TradeSuccess | None = None

    #: Revert reason we captured
    revert_reason: str | None = None

    #: Did we res
    cached: bool = False

    def get_base_token_symbol(self) -> str:
        return self.tokens[-1]

    def get_quote_token_symbol(self) -> str:
        return self.tokens[1] if len(self.tokens) >= 3 else self.tokens[0]

    def get_base_token_address(self) -> HexAddress:
        return self.path[-1]

    def is_compatible(self) -> bool:
        """Is the token compatible with Lagoon Vault?"""
        return self.has_liquidity() and self.is_buy_success() and self.is_sell_success()

    def has_liquidity(self) -> bool:
        return self.available_liquidity >= self.minimum_liquidity_threshold

    def is_buy_success(self) -> bool:
        """Was the buy operation successful?"""
        return isinstance(self.buy_result, TradeSuccess)

    def is_sell_success(self) -> bool:
        """Was the sell operation successful?"""
        return isinstance(self.sell_result, TradeSuccess)

    def get_round_trip_cost(self) -> float | None:
        """Get round trip cost in percents.

        E.g. 0.005 means we paid 50 bps

        :return:
            Round trip cost in percents or None if not avail
        """
        if not self.is_compatible():
            return None

        return abs(self.sell_result.amount_out - self.buy_amount_raw) / self.buy_amount_raw

    def is_stablecoin_quoted(self) -> bool:
        return is_stablecoin_like(self.get_quote_token_symbol())

    def pformat(self) -> str:
        """Diagnostics dump for debug"""
        d = asdict(self)
        return pformat(d)


@dataclass
class LagoonTokenCheckDatabase:
    """Database for storing Lagoon token compatibility checks."""

    #: Base token address -> LagoonTokenCompatibilityResponse
    report_by_token: dict[HexAddress, LagoonTokenCompatibilityData] = field(default_factory=dict)

    def get_count(self) -> int:
        return len(self.report_by_token)

    def calculate_stats(self) -> dict:
        stats = {
            "count": self.get_count(),
            "compatible": sum(r.is_compatible() for r in self.report_by_token.values()),
            "not_compatible": sum(not r.is_compatible() for r in self.report_by_token.values()),
            "liquidity_success": sum(r.has_liquidity() for r in self.report_by_token.values()),
            "buy_success": sum(r.is_buy_success() for r in self.report_by_token.values()),
            "sell_success": sum(r.is_sell_success() for r in self.report_by_token.values()),
            "min_cost": min((r.get_round_trip_cost() for r in self.report_by_token.values() if r.get_round_trip_cost() is not None), default=0),
            "max_cost": max((r.get_round_trip_cost() for r in self.report_by_token.values() if r.get_round_trip_cost() is not None), default=0),
            "mean_cost": mean([r.get_round_trip_cost() for r in self.report_by_token.values() if r.get_round_trip_cost() is not None] or [0]),
        }
        return stats

    def get_diagnostics(self, max_cell_width=30) -> list[dict]:
        """Prepare table output for manual output."""
        data = []

        for entry in self.report_by_token.values():
            revert_reason = entry.revert_reason
            if revert_reason:
                revert_reason = "\n".join(textwrap.wrap(revert_reason, width=max_cell_width))

            data.append(
                {
                    "base": entry.get_base_token_symbol(),
                    "quote": entry.get_quote_token_symbol(),
                    "address": entry.get_base_token_address(),
                    "compatible": "yes" if entry.is_compatible() else "no",
                    "liquidity": "yes" if entry.has_liquidity() else "no",
                    "buy": "yes" if entry.is_buy_success() else "no",
                    "sell": "yes" if entry.is_sell_success() else "no",
                    "reason": revert_reason if revert_reason else "-",
                    "cost": entry.get_round_trip_cost(),
                    "stablecoin_quoted": "yes" if entry.is_stablecoin_quoted() else "no",
                }
            )

        return data


def _get_revert_reason(web3, tx_hash: str) -> str | None:
    try:
        assert_transaction_success_with_explanation(web3, tx_hash, timeout=180)
        return None
    except TransactionAssertionError as e:
        # If the transaction reverted, we try to get the revert reason
        return e.revert_reason


def _perform_tx(
    web3: Web3,
    vault: LagoonVault,
    func: ContractFunction,
    asset_manager: HexAddress,
) -> tuple[str, str | None]:
    """Perform a transaction and return the revert reason."""

    vault_call = vault.transact_via_trading_strategy_module(func)

    # Use unlocked Anvil account for the spoof
    tx_hash = vault_call.transact(
        {
            "from": asset_manager,
        }
    )

    return tx_hash, _get_revert_reason(web3, tx_hash)


def check_compatibility(
    web3: Web3,
    vault: LagoonVault,
    asset_manager: HexAddress,
    uniswap_v2: UniswapV2Deployment,
    path: list[str],
    sell_delay=datetime.timedelta(seconds=3600),
) -> LagoonTokenCompatibilityData:
    """Check if the token is compatible with Lagoon Vault.

    - Attempt to buy and sell the token on Uniswap v2 like instance and see it works

    :param path:
        Uniswap routing path: [reserve token, intermediate token, base token]
    """

    assert isinstance(vault, LagoonVault), "Vault must be a LagoonVault instance"
    assert asset_manager.startswith("0x"), f"Asset manager address must start with 0x: {asset_manager}"
    for token_address in path:
        assert token_address.startswith("0x"), "Token address must start with 0x"

    match len(path):
        case 2:
            # quote, base
            quote_token = fetch_erc20_details(web3, path[0])
            base_token = fetch_erc20_details(web3, path[1])
            intermediate_token = None
            tokens = [quote_token.symbol, base_token.symbol]
            pair_address = uniswap_v2.pair_for(quote_token.address, base_token.address)[0]
        case 3:
            # quote, interm, base
            quote_token = fetch_erc20_details(web3, path[0])
            base_token = fetch_erc20_details(web3, path[-1])
            intermediate_token = fetch_erc20_details(web3, path[1])
            tokens = [quote_token.symbol, intermediate_token.symbol, base_token.symbol]
            pair_address = uniswap_v2.pair_for(intermediate_token.address, base_token.address)[0]
        case _:
            raise NotImplementedError(f"Unsupported path length: {len(path)}. Expected 2 or 3 tokens.")

    safe_address = vault.safe_address
    logger.info(f"Check Lagoon swap compatibility for {quote_token.symbol} -> {base_token.symbol} path: {path}, vault {vault.vault_address}, safe {safe_address}, module {vault.trading_strategy_module_address}, asset manager {asset_manager}")

    quote_token_buy_amount = Decimal(1)
    quote_token_buy_raw_amount = quote_token.convert_to_raw(quote_token_buy_amount)

    balance = quote_token.fetch_balance_of(safe_address)
    assert balance > quote_token_buy_amount, f"Vault {vault.vault_address} (Safe {safe_address}) does not have enough {quote_token.symbol} balance to buy {quote_token_buy_amount} {quote_token.symbol}, has: {balance} {quote_token.symbol}, needs: {quote_token_buy_amount} {quote_token.symbol}"

    estimate_buy_received = None
    estimate_sell_received = None
    buy_result = None
    sell_result = None
    buy_real_received = None
    buy_block_number = None
    sell_block_number = None

    #
    # Check liquidity
    #

    liquidity_result = get_liquidity(
        web3,
        pair_address,
    )

    if intermediate_token:
        liquidity_token = intermediate_token
    else:
        liquidity_token = quote_token

    if is_stablecoin_like(liquidity_token.symbol):
        minimum_liquidity_threshold = 10_000
    else:
        minimum_liquidity_threshold = 100

    raw_available_liquidity = liquidity_result.get_liquidity_for_token(liquidity_token.address)
    available_liquidity = liquidity_token.convert_to_decimals(raw_available_liquidity)

    revert_reason = None
    if available_liquidity < minimum_liquidity_threshold:
        revert_reason = f"Not enough liquidity for {quote_token.symbol} in the pool, has: {available_liquidity}, needs: {minimum_liquidity_threshold}"

    if not revert_reason:
        #
        # Start buy preparation
        #
        func = quote_token.approve(
            uniswap_v2.router.address,
            quote_token_buy_amount,
        )

        tx_hash, revert_reason = _perform_tx(
            web3,
            vault,
            func,
            asset_manager,
        )

    # Attempt to buy
    if not revert_reason:
        buy_block_number = web3.eth.block_number

        estimate_buy_received = estimate_buy_received_amount_raw(
            uniswap_v2,
            base_token_address=base_token.contract.address,
            quote_token_address=quote_token.contract.address,
            intermediate_token_address=intermediate_token.contract.address if intermediate_token else None,
            quantity_raw=quote_token_buy_raw_amount,
        )

        contract_func = swap_with_slippage_protection(
            uniswap_v2,
            base_token=base_token.contract,
            quote_token=quote_token.contract,
            amount_in=quote_token_buy_raw_amount,
            recipient_address=safe_address,
            intermediate_token=intermediate_token.contract if intermediate_token else None,
            max_slippage=0.99,
            support_token_tax=True,
        )

        tx_hash, revert_reason = _perform_tx(
            web3,
            vault,
            contract_func,
            asset_manager,
        )

    if not revert_reason:
        buy_result = analyse_trade_by_receipt_generic(
            web3,
            tx_hash=tx_hash,
            tx_receipt=None,
            intent_based=False,
        )

    # All good with buy, proceed to sell
    if not revert_reason:
        mine(
            web3,
            increase_timestamp=sell_delay.total_seconds(),
        )

        sell_block_number = web3.eth.block_number

        sell_amount = base_token.fetch_balance_of(safe_address)
        sell_amount_raw = base_token.convert_to_raw(sell_amount)

        buy_real_received = sell_amount_raw

        if sell_amount == 0:
            revert_reason = f"Token {base_token.symbol} {base_token.address}: balance is 0 after buy, cannot sell"

    if not revert_reason:
        func = base_token.approve(
            uniswap_v2.router.address,
            sell_amount,
        )
        tx_hash, revert_reason = _perform_tx(
            web3,
            vault,
            func,
            asset_manager,
        )

    if not revert_reason:
        # Flip base/quote
        estimate_sell_received = estimate_sell_received_amount_raw(
            uniswap_v2,
            base_token_address=quote_token.address,
            quote_token_address=base_token.address,
            intermediate_token_address=intermediate_token.contract.address if intermediate_token else None,
            quantity_raw=sell_amount_raw,
        )

        # Flip base/quote
        contract_func = swap_with_slippage_protection(
            uniswap_v2,
            base_token=quote_token.contract,
            quote_token=base_token.contract,
            amount_in=sell_amount_raw,
            recipient_address=safe_address,
            intermediate_token=intermediate_token.contract if intermediate_token else None,
            max_slippage=0.99,
            support_token_tax=True,
        )

        tx_hash, revert_reason = _perform_tx(
            web3,
            vault,
            contract_func,
            asset_manager,
        )

    if not revert_reason:
        sell_result = analyse_trade_by_receipt_generic(
            web3,
            tx_hash=tx_hash,
            tx_receipt=None,
            intent_based=False,
        )

    return LagoonTokenCompatibilityData(
        created_at=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None),
        token_address=base_token.address,
        pair_address=pair_address,
        available_liquidity=available_liquidity,
        minimum_liquidity_threshold=minimum_liquidity_threshold,
        path=path,
        tokens=tokens,
        buy_amount_raw=quote_token_buy_raw_amount,
        buy_block_number=buy_block_number,
        estimate_buy_received=estimate_buy_received,
        buy_result=buy_result,
        buy_real_received=buy_real_received,
        sell_block_number=sell_block_number,
        estimate_sell_received=estimate_sell_received,
        sell_result=sell_result,
        revert_reason=revert_reason,
        cached=False,
    )


def check_lagoon_compatibility_with_database(
    web3: Web3,
    paths: list[list[HexAddress]],
    vault_address: HexAddress,
    trading_strategy_module_address: HexAddress,
    asset_manager_address: HexAddress,
    database_file: Path = Path.home() / ".tradingstrategy" / "token-checks" / "lagoon_token_check.pickle",
    fork_block_number=None,
    retries=3,
) -> LagoonTokenCheckDatabase:
    """Check multiple tokens for compatibility with Lagoon Vault.

    - Uses a local pickle-file database to remember previous checks
    """

    chain_name = get_chain_name(web3.eth.chain_id).lower()
    logger.info("Checking token compatibility with Lagoon Vault, %d paths, database file: %s", len(paths), database_file)

    provider = web3.provider

    if isinstance(provider, MEVBlockerProvider):
        # Anvil cannot run against broadcast-only endpoints
        json_rpc_url = provider.call_provider.endpoint_uri
    elif isinstance(provider, FallbackProvider):
        # Pick randomly the active of many
        json_rpc_url = provider.endpoint_uri
    else:
        # Normal path
        json_rpc_url = provider.endpoint_uri

    assert " " not in json_rpc_url, f"JSON-RPC URL must not contain spaces: {json_rpc_url}, provider is {provider}"

    database = None
    if database_file.exists():
        # Load cached data
        try:
            database: LagoonTokenCheckDatabase = pickle.load(database_file.open("rb"))
            for entry in database.report_by_token.values():
                entry.cached = True
        except EOFError:
            pass

    if not database:
        database = LagoonTokenCheckDatabase()
        database_file.parent.mkdir(parents=True, exist_ok=True)
        assert database_file.parent.exists(), f"Database directory {database_file.parent} does not exist"

    unchecked_paths = []

    existing = set(key.lower() for key in database.report_by_token.keys())
    for path in paths:
        base_token_address = path[-1]
        base_token_address = base_token_address.lower()
        if base_token_address not in existing:
            unchecked_paths.append(path)

    logger.info("Total %d unchecked paths", len(unchecked_paths))

    # Work around Anvil bugs by restart
    # https://ethereum.stackexchange.com/questions/170480/anvil-read-timeout
    def _setup_anvil():
        _anvil = launch_anvil(
            fork_url=json_rpc_url,
            unlocked_addresses=[asset_manager_address],
            fork_block_number=fork_block_number,
        )

        _anvil_web3 = create_multi_provider_web3(_anvil.json_rpc_url, retries=retries)

        set_balance(
            _anvil_web3,
            asset_manager_address,
            99 * 10**18,
        )
        return _anvil, _anvil_web3

    spec = VaultSpec(web3.eth.chain_id, vault_address)

    for path in tqdm(unchecked_paths, desc="Checking Lagoon vault token swap compatibility", unit="token"):
        # Setup Anvil for each path to avoid issues with Anvil state
        anvil, anvil_web3 = _setup_anvil()

        base_token_address = path[-1]

        vault = LagoonVault(
            anvil_web3,
            spec=spec,
            trading_strategy_module_address=trading_strategy_module_address,
        )

        uniswap_v2 = fetch_deployment(
            anvil_web3,
            factory_address=UNISWAP_V2_DEPLOYMENTS[chain_name]["factory"],
            router_address=UNISWAP_V2_DEPLOYMENTS[chain_name]["router"],
            init_code_hash=UNISWAP_V2_DEPLOYMENTS[chain_name]["init_code_hash"],
        )

        try:
            report = check_compatibility(
                web3=anvil_web3,
                vault=vault,
                asset_manager=asset_manager_address,
                uniswap_v2=uniswap_v2,
                path=path,
            )
        except Exception as e:
            # It's likely Anvil is timing out due to an internal error.
            # Try to figure out why.
            stdout, stderr = anvil.close(log_level=logging.ERROR)
            stdout = stdout.decode("utf-8")
            stderr = stderr.decode("utf-8")
            logger.error("Anvil output:\n%s\n%s", stdout, stderr)
            raise RuntimeError(f"Failed to check Lagoon compatibility for path: {path} on chain {chain_name}: {e}") from e

        database.report_by_token[base_token_address.lower()] = report

        # Because the operation is so slow, we want to resave after each iteration
        pickle.dump(database, database_file.open("wb"))

        anvil.close()

    pickle.dump(database, database_file.open("wb"))
    return database
