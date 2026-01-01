"""A script to help interact with Orderly in delegate signing flow.

1. Deploy a Lagoon vault with Orderly support: `python scripts/lagoon/deploy-lagoon-orderly.py`
2. Delegate signer from Safe to a hot wallet: `python scripts/orderly/main.py delegate`
3. Register an Orderly key for the Safe using hot wallet as delegated signer: `python scripts/orderly/main.py register-key`
4. Deposit funds from Safe to the vault: `python scripts/orderly/main.py orderly-deposit`

More details below.

To delegate signer:

.. code-block:: shell

    export PRIVATE_KEY=...
    export JSON_RPC_URL=...
    export TRADING_STRATEGY_MODULE_ADDRESS=...
    export BROKER_ID=...
    export SIGNER_PRIVATE_KEY=...
    export LAGOON_VAULT_ADDRESS=...

    python scripts/orderly/main.py delegate
"""

import logging

import typer
from web3 import Web3

from eth_defi.chain import get_chain_name
from eth_defi.hotwallet import HotWallet
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault, VaultSpec
from eth_defi.orderly.api import OrderlyApiClient
from eth_defi.orderly.vault import OrderlyVault, deposit
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)

app = typer.Typer()


def get_orderly_vault_address(web3: Web3) -> str:
    # https://orderly.network/docs/build-on-omnichain/addresses
    return {
        421614: "0x0EaC556c0C2321BA25b9DC01e4e3c95aD5CDCd2f",
        8453: "0x816f722424B49Cf1275cc86DA9840Fbd5a6167e9",
    }[web3.eth.chain_id]


@app.command()
def delegate(
    *,
    private_key: str = typer.Option(..., envvar="PRIVATE_KEY", help="Private key for deployer wallet"),
    json_rpc_url: str = typer.Option(..., envvar="JSON_RPC_URL", help="JSON RPC URL"),
    trading_strategy_module_address: str = typer.Option(..., envvar="TRADING_STRATEGY_MODULE_ADDRESS", help="Trading strategy module address"),
    lagoon_vault_address: str = typer.Option(..., envvar="LAGOON_VAULT_ADDRESS", help="Vault address"),
    broker_id: str = typer.Option(..., envvar="BROKER_ID", help="Broker ID"),
    signer_private_key: str = typer.Option(..., envvar="SIGNER_PRIVATE_KEY", help="Signer private key"),
):
    """Delegate signer from TradingStrategyModuleV0 to a hot wallet."""
    setup_console_logging(default_log_level="info")

    web3 = create_multi_provider_web3(json_rpc_url)
    chain_id = web3.eth.chain_id
    chain_name = get_chain_name(chain_id).lower()

    logger.info(f"Connected to chain {chain_name}, last block is {web3.eth.block_number:,}")

    deployer_wallet = HotWallet.from_private_key(private_key)
    deployer_wallet.sync_nonce(web3)

    signer_wallet = HotWallet.from_private_key(signer_private_key)
    signer_address = signer_wallet.address

    balance_at_start = web3.eth.get_balance(Web3.to_checksum_address(deployer_wallet.address))
    logger.info("Deployer balance at start: %s", Web3.from_wei(balance_at_start, "ether"))

    lagoon_vault = LagoonVault(
        web3,
        VaultSpec(
            chain_id=chain_id,
            vault_address=lagoon_vault_address,
        ),
        trading_strategy_module_address=trading_strategy_module_address,
    )

    orderly_vault = OrderlyVault(web3, get_orderly_vault_address(web3))
    broker_hash = web3.keccak(text=broker_id)

    delegate_call = orderly_vault.contract.functions.delegateSigner((broker_hash, signer_address))

    module_tx = lagoon_vault.transact_via_trading_strategy_module(delegate_call)
    tx_params = module_tx.build_transaction(
        {
            "from": deployer_wallet.address,
            "gas": 500_000,
            "chainId": web3.eth.chain_id,
            "nonce": deployer_wallet.allocate_nonce(),
        }
    )

    signed_tx = deployer_wallet.account.sign_transaction(tx_params)

    logger.info("Broadcasting delegation transaction")
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    logger.info("Delegation transaction hash: %s", tx_hash.hex())

    client = OrderlyApiClient(
        account=signer_wallet.account,
        broker_id=broker_id,
        chain_id=web3.eth.chain_id,
        is_testnet=True if web3.eth.chain_id in {421614} else False,
    )

    logger.info("Confirming delegation")

    r = client.delegate_signer(
        delegate_contract=lagoon_vault.safe.address,
        delegate_tx_hash=tx_hash.hex(),
    )

    typer.echo(f"Confirmation result: {r}")


@app.command()
def confirm(
    tx_hash: str = typer.Argument(..., help="Transaction hash to confirm"),
    private_key: str = typer.Option(..., envvar="SIGNER_PRIVATE_KEY", help="Private key for signer wallet"),
    json_rpc_url: str = typer.Option(..., envvar="JSON_RPC_URL", help="JSON RPC URL"),
    safe_address: str = typer.Option(..., envvar="SAFE_ADDRESS", help="Safe address"),
    broker_id: str = typer.Option(..., envvar="BROKER_ID", help="Broker ID"),
):
    """DEPRECATED:Confirm signer delegation with transaction hash."""
    setup_console_logging(default_log_level="info")

    web3 = create_multi_provider_web3(json_rpc_url)

    signer_wallet = HotWallet.from_private_key(private_key)

    client = OrderlyApiClient(
        account=signer_wallet.account,
        broker_id=broker_id,
        chain_id=web3.eth.chain_id,
        is_testnet=True if web3.eth.chain_id in (421614,) else False,
    )

    r = client.delegate_signer(
        delegate_contract=safe_address,
        delegate_tx_hash=tx_hash,
    )

    typer.echo(f"Confirmation result: {r}")


@app.command()
def orderly_deposit(
    private_key: str = typer.Option(..., envvar="PRIVATE_KEY", help="Private key for deployer wallet"),
    json_rpc_url: str = typer.Option(..., envvar="JSON_RPC_URL", help="JSON RPC URL"),
    vault_address: str = typer.Option(..., envvar="LAGOON_VAULT_ADDRESS", help="Vault address"),
    trading_strategy_module_address: str = typer.Option(..., envvar="TRADING_STRATEGY_MODULE_ADDRESS", help="Trading strategy module address"),
    broker_id: str = typer.Option(..., envvar="BROKER_ID", help="Broker ID"),
    orderly_account_id: str = typer.Option(..., envvar="ORDERLY_ACCOUNT_ID", help="Orderly account ID"),
):
    setup_console_logging(default_log_level="info")

    web3 = create_multi_provider_web3(json_rpc_url)
    deployer_wallet = HotWallet.from_private_key(private_key)
    deployer_wallet.sync_nonce(web3)

    orderly_vault = OrderlyVault(web3, get_orderly_vault_address(web3))

    usdc = fetch_erc20_details(web3, "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913")

    lagoon_vault = LagoonVault(
        web3,
        VaultSpec(
            chain_id=web3.eth.chain_id,
            vault_address=vault_address,
        ),
        trading_strategy_module_address=trading_strategy_module_address,
    )

    approve_fn, get_deposit_fee_fn, deposit_fn = deposit(
        vault=orderly_vault,
        token=usdc.contract,
        amount=int(0.1 * 10**6),
        depositor_address=lagoon_vault.safe.address,
        orderly_account_id=orderly_account_id,
        broker_id=broker_id,
        token_id="USDC",
    )

    module_tx = lagoon_vault.transact_via_trading_strategy_module(approve_fn)
    tx_params = module_tx.build_transaction(
        {
            "from": deployer_wallet.address,
            "gas": 200_000,
            "chainId": web3.eth.chain_id,
            "nonce": deployer_wallet.allocate_nonce(),
        }
    )

    signed_tx = deployer_wallet.account.sign_transaction(tx_params)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    deposit_fee = get_deposit_fee_fn.call()
    logger.info(f"Deposit fee: {deposit_fee}")

    module_tx = lagoon_vault.transact_via_trading_strategy_module(deposit_fn, value=deposit_fee)
    tx_params = module_tx.build_transaction(
        {
            "from": deployer_wallet.address,
            "gas": 500_000,
            "chainId": web3.eth.chain_id,
            "nonce": deployer_wallet.allocate_nonce(),
        }
    )

    signed_tx = deployer_wallet.account.sign_transaction(tx_params)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    logger.info(f"Deposit tx hash: {tx_hash.hex()}")


@app.command()
def register_key(
    json_rpc_url: str = typer.Option(..., envvar="JSON_RPC_URL", help="JSON RPC URL"),
    broker_id: str = typer.Option(..., envvar="BROKER_ID", help="Broker ID"),
    signer_private_key: str = typer.Option(..., envvar="SIGNER_PRIVATE_KEY", help="Signer private key"),
    safe_address: str = typer.Option(..., envvar="SAFE_ADDRESS", help="Safe address"),
):
    setup_console_logging(default_log_level="info")

    web3 = create_multi_provider_web3(json_rpc_url)
    signer_wallet = HotWallet.from_private_key(signer_private_key)

    client = OrderlyApiClient(
        account=signer_wallet.account,
        broker_id=broker_id,
        chain_id=web3.eth.chain_id,
        is_testnet=True if web3.eth.chain_id in (421614,) else False,
    )

    r = client.register_key(
        delegate_contract=safe_address,
    )
    typer.echo(f"Register key result: {r}")


@app.command()
def check_balance(
    orderly_account_id: str = typer.Option(..., envvar="ORDERLY_ACCOUNT_ID", help="Orderly account ID"),
    orderly_secret: str = typer.Option(..., envvar="ORDERLY_SECRET", help="Orderly secret"),
):
    setup_console_logging(default_log_level="info")

    client = OrderlyApiClient(
        orderly_account_id=orderly_account_id,
        orderly_secret=orderly_secret,
        is_testnet=False,
    )

    r = client.get_balance()
    typer.echo(f"Balance: {r}")


if __name__ == "__main__":
    app()
