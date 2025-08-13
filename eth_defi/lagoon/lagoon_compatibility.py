"""Lagoon token compatibility checks."""
import datetime
import logging
import pickle
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from statistics import mean

from web3 import Web3

from eth_typing import HexAddress

from tqdm_loggable.auto import tqdm

from eth_defi.chain import get_chain_name
from eth_defi.lagoon.vault import LagoonVault
from eth_defi.provider.anvil import mine, launch_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details, TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation, TransactionAssertionError
from eth_defi.trade import TradeSuccess
from eth_defi.uniswap_v2.analysis import analyse_trade_by_receipt
from eth_defi.uniswap_v2.constants import UNISWAP_V2_DEPLOYMENTS
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment, fetch_deployment
from eth_defi.uniswap_v2.fees import estimate_buy_price, estimate_sell_price, estimate_buy_received_amount_raw, estimate_sell_received_amount_raw
from eth_defi.uniswap_v2.swap import swap_with_slippage_protection
from web3.contract.contract import ContractFunction
from eth_defi.vault.base import VaultSpec

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

    #: Used routing path
    path: list[str]

    tokens: list[str]

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

    def is_compatible(self) -> bool:
        """Is the token compatible with Lagoon Vault?"""
        return self.is_buy_success() and self.is_sell_success()

    def is_buy_success(self) -> bool:
        """Was the buy operation successful?"""
        return self.buy_result is not None

    def is_sell_success(self) -> bool:
        """Was the sell operation successful?"""
        return self.sell_result is not None

    def get_round_trip_cost(self) -> float | None:
        """Get round trip cost in percents.

        E.g. 0.005 means we paid 50 bps

        :return:
            Round trip cost in percents or None if not avail
        """
        if not self.is_compatible():
            return None

        return abs(self.sell_result.amount_out - self.buy_amount_raw) / self.buy_amount_raw





@dataclass
class LagoonTokenCheckDatabase:
    """Database for storing Lagoon token compatibility checks."""

    #: Base token address -> LagoonTokenCompatibilityResponse
    report_by_token: dict[HexAddress, LagoonTokenCompatibilityData] = field(default_factory=dict)

    def calculate_stats(self) -> dict:
        stats = {
            "compatible": sum(r.is_compatible() for r in self.report_by_token.values()),
            "not_compatible": sum(not r.is_compatible() for r in self.report_by_token.values()),
            "buy_success": sum(r.is_buy_success() for r in self.report_by_token.values()),
            "sell_success": sum(r.is_sell_success() for r in self.report_by_token.values()),
            "min_cost": min(r.get_round_trip_cost() for r in self.report_by_token.values() if r.get_round_trip_cost() is not None),
            "max_cost": max(r.get_round_trip_cost() for r in self.report_by_token.values() if r.get_round_trip_cost() is not None),
            "meanx_cost": mean(r.get_round_trip_cost() for r in self.report_by_token.values() if r.get_round_trip_cost() is not None),
        }
        return stats


def _get_revert_reason(web3, tx_hash: str) -> str | None:
    try:
        assert_transaction_success_with_explanation(web3, tx_hash)
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

    vault_call = vault.transact_via_trading_strategy_module(
        func
    )

    # Use unlocked Anvil account for the spoof
    tx_hash = vault_call.transact({
        "from": asset_manager,
    })

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
            # Uniswap V2
            quote_token = fetch_erc20_details(web3, path[0])
            base_token = fetch_erc20_details(web3, path[1])
            intermediate_token = None
            tokens = [quote_token.symbol, base_token.symbol]
        case 3:
            # Uniswap V3
            quote_token = fetch_erc20_details(web3, path[0])
            base_token = fetch_erc20_details(web3, path[-1])
            intermediate_token = fetch_erc20_details(web3, path[1])
            tokens = [quote_token.symbol, intermediate_token.symbol, base_token.symbol]
        case _:
            raise NotImplementedError(
                f"Unsupported path length: {len(path)}. Expected 2 or 3 tokens."
            )

    logger.info(f"Check Lagoon swap compatibility for {quote_token.symbol} -> {base_token.symbol} path: {path}")

    safe_address = vault.safe_address

    quote_token_buy_amount = Decimal(1)
    quote_token_buy_raw_amount = quote_token.convert_to_raw(quote_token_buy_amount)

    balance = quote_token.fetch_balance_of(safe_address)
    assert balance > quote_token_buy_amount, f"Vault {vault.vault_address} (Safe {safe_address}) does not have enough {quote_token.symbol} balance to buy {quote_token_buy_amount} {quote_token.symbol}, has: {balance} {quote_token.symbol}, needs: {quote_token_buy_amount} {quote_token.symbol}"

    estimate_buy_received = None
    estimate_sell_received = None
    buy_result = None
    sell_result = None

    #
    # Start buy preparation
    #

    buy_block_number = web3.eth.block_number

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
        )

        tx_hash, revert_reason = _perform_tx(
            web3,
            vault,
            contract_func,
            asset_manager,
        )

    if not revert_reason:
        buy_success = True
        buy_result = analyse_trade_by_receipt(
            web3,
            uniswap_v2,
            tx=None,
            tx_hash=tx_hash,
            tx_receipt=None,
        )

    # All good with buy, proceed to sell

    mine(
        web3,
        increase_timestamp=sell_delay.total_seconds(),
    )

    sell_block_number = web3.eth.block_number

    if not revert_reason:
        sell_amount = base_token.fetch_balance_of(safe_address)
        sell_amount_raw = base_token.convert_to_raw(sell_amount)

        buy_real_received = sell_amount_raw

        assert sell_amount > 0

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
        )

        tx_hash, revert_reason = _perform_tx(
            web3,
            vault,
            contract_func,
            asset_manager,
        )

    if not revert_reason:
        sell_result = analyse_trade_by_receipt(
            web3,
            uniswap_v2,
            tx=None,
            tx_hash=tx_hash,
            tx_receipt=None,
        )

    return LagoonTokenCompatibilityData(
        created_at=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None),
        token_address=base_token.address,
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
) -> LagoonTokenCheckDatabase:
    """Check multiple tokens for compatibility with Lagoon Vault.

    - Uses a local pickle-file database to remember previous checks
    """

    logger.info("Checking token compatibility with Lagoon Vault, %d paths, database file: %s", len(paths), database_file)

    json_rpc_url = web3.provider.endpoint_uri
    anvil = launch_anvil(
        fork_url=json_rpc_url,
        unlocked_addresses=[asset_manager_address],
        fork_block_number=fork_block_number,
    )

    if database_file.exists():
        # Load cached data
        database: LagoonTokenCheckDatabase = pickle.load(database_file.open("rb"))
        for entry in database.report_by_token.values():
            entry.cached = True
    else:
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

    anvil_web3 = create_multi_provider_web3(anvil.json_rpc_url)

    spec = VaultSpec(web3.eth.chain_id, vault_address)

    vault = LagoonVault(
        anvil_web3,
        spec=spec,
        trading_strategy_module_address=trading_strategy_module_address,
    )

    chain_name = get_chain_name(anvil_web3.eth.chain_id).lower()

    uniswap_v2 = fetch_deployment(
        anvil_web3,
        factory_address=UNISWAP_V2_DEPLOYMENTS[chain_name]["factory"],
        router_address=UNISWAP_V2_DEPLOYMENTS[chain_name]["router"],
        init_code_hash=UNISWAP_V2_DEPLOYMENTS[chain_name]["init_code_hash"],
    )

    logger.info("Checking with Uniswap v2 deployment: %s", uniswap_v2)

    for path in tqdm(unchecked_paths, desc="Checking Lagoon vault token swap compatibility", unit="token"):
        base_token_address = path[-1]
        report = check_compatibility(
            web3=anvil_web3,
            vault=vault,
            asset_manager=asset_manager_address,
            uniswap_v2=uniswap_v2,
            path=path,
        )

        database.report_by_token[base_token_address.lower()] = report

    pickle.dump(database, database_file.open("wb"))
    return database
