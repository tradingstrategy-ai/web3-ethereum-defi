"""A script to help delegate signer from TradingStrategyModuleV0 to a hot wallet.

To run:

.. code-block:: shell

    export PRIVATE_KEY=...
    export JSON_RPC_URL=$JSON_RPC_BINANCE
    export TRADING_STRATEGY_MODULE_ADDRESS=...
    export BROKER_ID=...
    export SIGNER_ADDRESS=...
    export SIGNER_PRIVATE_KEY=...
    export DELEGATE_TX_HASH=...

    python scripts/orderly/delegate.py delegate

    # If you have a delegate tx hash, you can confirm the signer:
    python scripts/orderly/delegate.py confirm --tx-hash <tx_hash>
"""

import logging

import typer
from web3 import Web3

from eth_defi.abi import get_deployed_contract
from eth_defi.chain import get_chain_name
from eth_defi.hotwallet import HotWallet
from eth_defi.orderly.api import OrderlyApiClient
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)

app = typer.Typer()


def get_orderly_vault_address(web3: Web3) -> str:
    return {
        421614: "0x0EaC556c0C2321BA25b9DC01e4e3c95aD5CDCd2f",
    }[web3.eth.chain_id]


@app.command()
def delegate(
    private_key: str = typer.Option(..., envvar="PRIVATE_KEY", help="Private key for deployer wallet"),
    json_rpc_url: str = typer.Option(..., envvar="JSON_RPC_URL", help="JSON RPC URL"),
    trading_strategy_module_address: str = typer.Option(..., envvar="TRADING_STRATEGY_MODULE_ADDRESS", help="Trading strategy module address"),
    broker_id: str = typer.Option(..., envvar="BROKER_ID", help="Broker ID"),
    signer_address: str = typer.Option(..., envvar="SIGNER_ADDRESS", help="Signer address"),
):
    """Delegate signer from TradingStrategyModuleV0 to a hot wallet."""
    setup_console_logging(default_log_level="info")

    web3 = create_multi_provider_web3(json_rpc_url)
    chain_id = web3.eth.chain_id
    chain_name = get_chain_name(chain_id).lower()

    logger.info(f"Connected to chain {chain_name}, last block is {web3.eth.block_number:,}")

    deployer_wallet = HotWallet.from_private_key(private_key)
    deployer_wallet.sync_nonce(web3)

    balance_at_start = web3.eth.get_balance(Web3.to_checksum_address(deployer_wallet.address))
    logger.info("Deployer balance at start: %s", Web3.from_wei(balance_at_start, "ether"))

    ts_module = get_deployed_contract(web3, "safe-integration/TradingStrategyModuleV0.json", trading_strategy_module_address)

    orderly_vault_address = get_orderly_vault_address(web3)
    broker_hash = web3.keccak(text=broker_id)
    tx_params = ts_module.functions.orderlyDelegateSigner(orderly_vault_address, (broker_hash, signer_address)).build_transaction(
        {
            "from": deployer_wallet.address,  # TODO: double check this
            "gas": 500_000,
            "chainId": web3.eth.chain_id,
        }
    )

    signed_tx = deployer_wallet.account.sign_transaction(tx_params)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    typer.echo(f"Transaction hash: {tx_hash.hex()}")


@app.command()
def confirm(
    tx_hash: str = typer.Argument(..., help="Transaction hash to confirm"),
    private_key: str = typer.Option(..., envvar="SIGNER_PRIVATE_KEY", help="Private key for signer wallet"),
    json_rpc_url: str = typer.Option(..., envvar="JSON_RPC_URL", help="JSON RPC URL"),
    trading_strategy_module_address: str = typer.Option(..., envvar="TRADING_STRATEGY_MODULE_ADDRESS", help="Trading strategy module address"),
    broker_id: str = typer.Option(..., envvar="BROKER_ID", help="Broker ID"),
):
    """Confirm signer delegation with transaction hash."""
    web3 = create_multi_provider_web3(json_rpc_url)

    signer_wallet = HotWallet.from_private_key(private_key)

    client = OrderlyApiClient(
        account=signer_wallet.account,
        broker_id=broker_id,
        chain_id=web3.eth.chain_id,
        is_testnet=True,
    )

    r = client.delegate_signer(
        delegate_contract=trading_strategy_module_address,
        delegate_tx_hash=tx_hash,
    )

    typer.echo(f"Confirmation result: {r}")


if __name__ == "__main__":
    app()
