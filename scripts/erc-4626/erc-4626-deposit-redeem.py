"""ERC-4626 vault deposit and redeem script.

- This is a script that performs deposit and redeem operations on an ERC-4626 vault
- It can be run in simulation mode (:ref:`Anvil` mainnet fork)
- The chain id and vault are given as a command line arguments
- The script it is multichain: it will automatically pick JSON-RPC connection
  for the given chain id
- Currently only USDC deposits supported

To run:

.. code-block:: shell

    # Test Harvest finance USDC Autopilot vault on IPOR on Base mainnet fork.
    # You need your own Base JSON-RPC provider.
    export JSON_RPC_BASE=...
    python scripts/erc-4626/erc-4626-deposit-redeem.py \
        --simulate \
        --vault 8453,0x0d877Dc7C8Fa3aD980DfDb18B48eC9F8768359C4

Another example using Spark USDC vault on Base mainnet fork:

.. code-block:: shell

    # Test Harvest finance USDC Autopilot vault on IPOR on Base mainnet fork.
    # You need your own Base JSON-RPC provider.
    export JSON_RPC_BASE=...
    python scripts/erc-4626/erc-4626-deposit-redeem.py \
        --simulate \
        --vault 8453,0x7bfa7c4f149e7415b73bdedfe609237e29cbf34a

"""

import logging
import os
import datetime
from decimal import Decimal
import argparse
from typing import cast

from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.chain import get_chain_name, get_block_time
from eth_defi.erc_4626.classification import create_vault_instance, detect_vault_features
from eth_defi.erc_4626.flow import approve_and_deposit_4626, approve_and_redeem_4626
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import fork_network_anvil, create_fork_funded_wallet, is_anvil, mine
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.provider.named import get_provider_name
from eth_defi.timestamp import get_block_timestamp
from eth_defi.token import fetch_erc20_details, USDC_NATIVE_TOKEN, LARGE_USDC_HOLDERS
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec, VaultBase


logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="ERC-4626 vault deposit and redeem script.")
    parser.add_argument("--vault", type=str, required=True, help="Vault chain id and contract address. E.g. 123,12312312")
    parser.add_argument("--usdc-forked-wallet", type=str, required=False, help="For simulationm, onchain address holding balance for USDC which we are going to use")
    parser.add_argument("--json-rpc-url", type=str, required=False, help="Give JSON-RPC URL - otherwise picked from environment variables")
    parser.add_argument("--simulate", action="store_true", help="Run in simulation mode by doing Anvil work")
    parser.add_argument("--fork-block-number", type=int, required=False, help="Fork the mainnet at a specific block number (example: 341830407)")
    parser.add_argument("--private-key", type=str, required=False, help="Private key for the hot wallet when not simulated, must start 0x")
    parser.add_argument("--deposit-value", type=str, required=False, help="Test deposit value in USDC, e.g. 1000.0", default="10.00")
    parser.add_argument("--simplified-logging", action="store_true", help="Use simplified output without timestamps")
    parser.add_argument("--redeem-wait-seconds", type=float, required=False, help="How many blocks to mine for redeem timelock on the vault", default=1800)
    return parser.parse_args()


def deposit_redeem(
    web3: Web3,
    vault: VaultBase,
    hot_wallet: HotWallet,
    deposit_value: Decimal,
    redeem_wait_seconds: float | None,
) -> dict[str, Decimal]:
    """Perform deposit and redeem transactions.

    - 4 transactions total with approves
    - `See here for IPOR error codes <https://www.codeslaw.app/contracts/base/0x12e9b15ad32faeb1a02f5ddd99254309faf5f2f8?tab=abi>`__

    :return:
        Dict for slippage analysis
    """

    vault = cast(ERC4626Vault, vault)

    # Anvil transactions should confirm relatively quickly,
    # so do not wait for long time if it is going to crash
    timeout = 10 if is_anvil(web3) else 60

    # If we live in a forked universe, time can be whatever
    block_number = web3.eth.block_number
    now_ = get_block_timestamp(web3, block_number)

    # Check for non-instant deposit/redemem cycle
    try:
        redemption_delay = vault.get_redemption_delay()
    except NotImplementedError:
        redemption_delay = "<unimplemented>"

    logger.info("Vault %s (%s) redemption delay: %s", vault.name, vault.address, redemption_delay)

    def _perform_tx(func: ContractFunction):
        signed_tx = hot_wallet.sign_bound_call_with_new_nonce(
            func,
            web3=web3,
            fill_gas_price=True,
        )
        logger.info(
            "Broadcasting transaction %s(): %s",
            func.fn_name,
            signed_tx.hash.hex(),
        )
        web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        assert_transaction_success_with_explanation(
            web3,
            signed_tx.hash.hex(),
            timeout=timeout
        )

    logger.info("Depositing...")

    func_1, func_2 = approve_and_deposit_4626(
        vault=vault,
        from_=hot_wallet.address,
        amount=deposit_value,
    )

    _perform_tx(func_1)
    _perform_tx(func_2)

    share_count = vault.share_token.fetch_balance_of(hot_wallet.address)
    logger.info("We received %f %s", share_count, vault.share_token.symbol)

    try:
        redemption_over = vault.get_redemption_delay_over(hot_wallet.address)
        redemption_delay = redemption_over - now_
        redemption_delay_seconds = redemption_delay.total_seconds()
    except NotImplementedError:
        redemption_delay = "<unimplemented>"
        redemption_delay_seconds = None

    logger.info("After deposit, address has %s redemption over at: %s (%s seconds)", hot_wallet.address, redemption_delay, redemption_delay_seconds)

    if redemption_delay_seconds:
        logger.info("Simulating redeem delay. Using vault-given redemption_delay_seconds: %s", redemption_delay_seconds)
        mine(web3, increase_timestamp=redemption_delay_seconds + 1)
    elif redeem_wait_seconds:
        logger.info("Simulating redeem delay. Using default redemption_delay_seconds: %s", redeem_wait_seconds)
        mine(web3, increase_timestamp=redeem_wait_seconds)

    func_3, func_4 = approve_and_redeem_4626(
        vault=vault,
        from_=hot_wallet.address,
        amount=share_count,
    )
    before_redeemed_value = vault.denomination_token.fetch_balance_of(hot_wallet.address)
    _perform_tx(func_3)
    _perform_tx(func_4)
    redeemed_value = vault.denomination_token.fetch_balance_of(hot_wallet.address) - before_redeemed_value

    return {
        "deposit_value": deposit_value,
        "redeemed_value": redeemed_value,
        "share_count": share_count,
    }



def main():
    """Main entry point for the script."""
    args = parse_args()

    vault = args.vault
    funding_wallet = args.usdc_forked_wallet
    simulate = args.simulate
    json_rpc_url = args.json_rpc_url
    fork_block_number = args.fork_block_number
    private_key = args.private_key
    simplified_logging = args.simplified_logging
    redeem_wait_seconds = args.redeem_wait_seconds

    try:
        deposit_value = Decimal(args.deposit_value)
    except Exception as e:
        raise ValueError(f"Invalid deposit value: {args.deposit_value}") from e

    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "info"),
        simplified_logging=simplified_logging,
    )

    spec = VaultSpec.parse_string(vault)
    chain_name = get_chain_name(spec.chain_id)

    if not json_rpc_url:
        json_rpc_url = read_json_rpc_url(spec.chain_id)

    # Fish out provider namy without API KEY
    web3_dummy = create_multi_provider_web3(json_rpc_url)
    logger.info(
        "Using JSON RPC provider %s for chain %s",
        get_provider_name(web3_dummy.provider),
        chain_name,
    )

    usdc_address = USDC_NATIVE_TOKEN.get(spec.chain_id)
    assert usdc_address, f"USDC address not found for chain {spec.chain_id}"

    if simulate:

        # TODO: Currently only USDC autofunding supported
        if funding_wallet is None:
            funding_wallet = LARGE_USDC_HOLDERS[spec.chain_id]

        assert funding_wallet, f"No large USDC folder for {chain_name} known. For simulation, please provide --usdc-forked-wallet address holding USDC balance"

        # Addresses we need to take control to simulate GMX offchain Keeper fuctionality
        unlocked_addresses = [
            funding_wallet,
        ]

        logger.info(f"Forking %s with Anvil", chain_name)
        anvil = fork_network_anvil(
            json_rpc_url,
            unlocked_addresses=unlocked_addresses,
            fork_block_number=fork_block_number,  # Always simulate against a fixed statel
            log_wait=False,
        )
        web3 = create_multi_provider_web3(
            anvil.json_rpc_url,
            default_http_timeout=(10.0, 60.0),  # Increase default timeouts if your Anvil is slow
            retries=0,  # If Anvil RPC call fails, retries won't help
        )

        hot_wallet = create_fork_funded_wallet(
            web3,
            usdc_address=usdc_address,
            large_usdc_holder=funding_wallet,
            usdc_amount=deposit_value,
        )

    else:
        logger.info("Base production deployment")
        web3 = create_multi_provider_web3(json_rpc_url)
        assert private_key, "Private key must be set in environment variable PRIVATE_KEY"
        hot_wallet = None
        raise NotImplementedError("TODO: Unfinished")

    features = detect_vault_features(web3, spec.vault_address, verbose=False)
    logger.info("Detected vault features: %s", features)

    vault = create_vault_instance(
        web3,
        spec.vault_address,
        features=features,
    )

    vault = cast(ERC4626Vault, vault)
    assert vault.is_valid(), f"Vault contract does not look like ERC-4626: {vault.address}"

    logger.info("Using vault %s (%s), our proxy class is %s", vault.name, vault.address, vault.__class__.__name__)

    usdc = fetch_erc20_details(web3, usdc_address)

    balance = usdc.fetch_balance_of(hot_wallet.address)
    gas_balance = web3.eth.get_balance(hot_wallet.address)

    assert vault.denomination_token == usdc, f"Vault denomination token {vault.denomination_token} does not match USDC {usdc.address}"

    logger.info("Gas balance: %s ETH", gas_balance / 10**18)
    logger.info("USDC balance: %s", balance)

    assert balance >= deposit_value, f"Not enough USDC to deposit {deposit_value} (balance: {balance})"

    logger.info("Depositing %s USDC to vault %s", deposit_value, spec.vault_address)

    analysis = deposit_redeem(
        web3=web3,
        vault=vault,
        hot_wallet=hot_wallet,
        deposit_value=deposit_value,
        redeem_wait_seconds=0 and redeem_wait_seconds,
    )

    slippage = (analysis["redeemed_value"] - analysis["deposit_value"]) / analysis["deposit_value"]
    logger.info("Deposit value: %s %s", analysis["deposit_value"], usdc.symbol)
    logger.info("Redeem value: %s %s", analysis["redeemed_value"], usdc.symbol)
    logger.info("Share count: %s %s", analysis["share_count"], vault.share_token.symbol)
    logger.info("Slippage: %.4f%%", slippage * 100)

    logger.info("All done")


if __name__ == "__main__":
    main()
