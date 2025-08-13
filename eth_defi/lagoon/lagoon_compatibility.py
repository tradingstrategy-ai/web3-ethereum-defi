"""Lagoon token compatibility checks."""
import datetime
import logging
import pickle
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from web3 import Web3

from eth_typing import HexAddress

from tqdm_loggable.auto import tqdm

from eth_defi.chain import get_chain_name
from eth_defi.lagoon.vault import LagoonVault
from eth_defi.provider.anvil import mine, launch_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation, TransactionAssertionError
from eth_defi.trade import TradeSuccess
from eth_defi.uniswap_v2.analysis import analyse_trade_by_receipt
from eth_defi.uniswap_v2.constants import UNISWAP_V2_DEPLOYMENTS
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment, fetch_deployment
from eth_defi.uniswap_v2.fees import estimate_buy_price, estimate_sell_price
from eth_defi.uniswap_v2.swap import swap_with_slippage_protection
from web3.contract.contract import ContractFunction

from tests.lagoon.test_token_compat import vault

from eth_defi.vault.base import VaultSpec

logger = logging.getLogger(__name__)


@dataclass
class LagoonTokenCompatibilityResponse:
    """Was an ERC-20 compatible with Lagoon Vault?

    - Vault can buy and sell the token
    - Only applicable to Uniswap v2 kind AMMs
    """

    #: What was the base address of the token
    token_address: str

    #: Used routing path
    path: list[str]

    buy_block_number: int

    buy_estimated_price: Decimal

    buy_result: TradeSuccess | None

    sell_block_number: int | None = None

    sell_estimated_price: Decimal | None = None

    sell_result: TradeSuccess | None = None

    #: Revert reason we captured
    error: str | None = None

    def is_compatible(self) -> bool:
        """Is the token compatible with Lagoon Vault?"""
        return self.buy_success and self.sell_success

    def is_buy_success(self) -> bool:
        """Was the buy operation successful?"""
        return self.buy_result is not None

    def is_sell_success(self) -> bool:
        """Was the sell operation successful?"""
        return self.sell_result is not None


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
) -> LagoonTokenCompatibilityResponse:
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
        case 3:
            # Uniswap V3
            quote_token = fetch_erc20_details(web3, path[0])
            base_token = fetch_erc20_details(web3, path[-1])
            intermediate_token = fetch_erc20_details(web3, path[1])
        case _:
            raise NotImplementedError(
                f"Unsupported path length: {len(path)}. Expected 2 or 3 tokens."
            )

    logger.info(f"Check Lagoon swap compatibility for {quote_token.symbol} -> {base_token.symbol} path: {path}")

    quote_token_buy_amount = Decimal(1)
    quote_token_buy_raw_amount = quote_token.convert_to_raw(quote_token_buy_amount)

    balance = quote_token.fetch_balance_of(vault.vault_address)
    assert balance > quote_token_buy_amount, f"Vault {vault.vault_address} does not have enough {quote_token.symbol} balance to buy {quote_token_buy_amount} {quote_token.symbol}, has: {balance} {quote_token.symbol}, needs: {quote_token_buy_amount} {quote_token.symbol}"

    buy_price = None
    sell_price = None
    buy_result = None
    sell_result = None
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

        buy_price = estimate_buy_price(
            uniswap_v2,
            base_token.contract,
            quote_token.contract,
            intermediate_token= intermediate_token.contract if intermediate_token else None,
            quantity= quote_token_buy_raw_amount,
        )

        contract_func = swap_with_slippage_protection(
            uniswap_v2,
            quote_token.contract,
            base_token.contract,
            quote_token_buy_raw_amount,
            recipient_address=vault.address,
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
            tx_hash=tx_hash,
        )

    # All good with buy, proceed to sell

    mine(
        web3,
        increase_timestamp=sell_delay,
    )

    sell_block_number = web3.eth.block_number

    if not revert_reason:
        sell_amount = base_token.fetch_balance_of(vault.address)
        sell_amount_raw = base_token.convert_to_raw(sell_amount)

        func = base_token.approve(
            uniswap_v2.router.address,
            sell_amount_raw,
        )
        tx_hash, revert_reason = _perform_tx(
            web3,
            vault,
            func,
            asset_manager,
        )

    if not revert_reason:
        sell_price = estimate_sell_price(
            uniswap_v2,
            quote_token.contract,
            base_token.contract,
            intermediate_token=intermediate_token.contract if intermediate_token else None,
            quantity=sell_amount_raw,
        )

        contract_func = swap_with_slippage_protection(
            uniswap_v2,
            base_token.contract,
            quote_token.contract,
            sell_amount_raw,
            recipient_address=vault.address,
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
            tx_hash=tx_hash,
        )

    return LagoonTokenCompatibilityResponse(
        token_address=base_token.address,
        path=path,
        buy_block_number=buy_block_number,
        buy_estimated_price=buy_price,
        buy_result=buy_result,
        sell_block_number=sell_block_number,
        sell_estimated_price=sell_price,
        sell_result=sell_result,
        error=revert_reason,
    )


@dataclass
class LagoonTokenCheckDatabase:
    """Database for storing Lagoon token compatibility checks."""

    #: Base token address -> LagoonTokenCompatibilityResponse
    report_by_token: dict[HexAddress, LagoonTokenCompatibilityResponse] = field(default=dict)


def check_lagoon_compatibility_with_database(
    web3: Web3,
    paths: list[list[str]],
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
    else:
        database = LagoonTokenCheckDatabase()
        database_file.parent.mkdir(parents=True, exist_ok=True)
        assert database_file.parent.exists(), f"Database directory {database_file.parent} does not exist"

    unchecked_paths = []
    for path in paths:
        base_token_address = path[-1]
        base_token_address = base_token_address.lower()
        if base_token_address not in database.tokens:
            unchecked_paths.append(path)

    logger.info("Total %d unchecked paths", len(unchecked_paths))

    anvil_web3 = create_multi_provider_web3(anvil.json_rpc_url)

    spec = VaultSpec(web3.eth.chain_id, vault_address)

    vault = LagoonVault(
        anvil_web3,
        spec=spec,
        trading_strategy_module_address=trading_strategy_module_address,
    )

    chain_name = get_chain_name(anvil_web3.eth.chain_id).lower90

    uniswap_v2 = fetch_deployment(
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

        database.report_by_token[base_token_address] = report

    pickle.dump(database, database_file.open("wb"))
    return database
