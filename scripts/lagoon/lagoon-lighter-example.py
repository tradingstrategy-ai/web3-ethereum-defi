"""Tutorial: Lighter L1 deposits/withdrawals through a Lagoon vault.

This script demonstrates the on-chain custody lifecycle for trading on
`Lighter <https://lighter.xyz>`__ (a zk-rollup perps DEX on Ethereum L1)
through an asset-managed Lagoon vault (Gnosis Safe + ``TradingStrategyModuleV0``):

1. Deploy a Lagoon vault with Lighter whitelisting enabled
2. Deposit USDC into the vault (ERC-7540 async deposit)
3. Deposit USDC from the vault into the Lighter ``ZkLighter`` L1 contract
   (``approve`` + ``deposit`` through the guard's ``performCall``)
4. (Off-chain) register the Lighter account and trade — see notes below
5. Withdraw USDC from Lighter back to the Safe (request + claim)

Scope / what can be simulated
-----------------------------

Lighter trading happens **off-chain** on the L2 sequencer (gasless, signed
orders), so — exactly like GMX keepers — it cannot be reproduced on a local
fork. What this script simulates on an Anvil Ethereum-mainnet fork is the
**on-chain L1 custody flow**: vault deployment, USDC deposit into the vault,
and the guard-validated ``deposit()`` into ``ZkLighter``.

The off-chain steps (account linking via an EIP-712 / EIP-1271 Safe signature,
order placement) and the withdrawal *claim* (which needs a sequencer-produced
zk-proof to create a pending balance) are documented but skipped in simulation.

USDC-only: this integration whitelists a single deposit asset (USDC). See
``eth_defi/lighter/README-lighter-guard.md``.

Simulation mode
---------------

Set ``SIMULATE=true`` to run against an Anvil mainnet fork of Ethereum. A test
wallet is created and funded with ETH (gas) and USDC (deposit asset) via Anvil
overrides — no real funds or private key required.

Example::

    SIMULATE=true JSON_RPC_ETHEREUM="https://eth.llamarpc.com" \
        python scripts/lagoon/lagoon-lighter-example.py

Real deployment
---------------

Without ``SIMULATE``, the script connects to Ethereum mainnet and signs with
``LIGHTER_PRIVATE_KEY``. Start with small amounts and verify contract addresses.

Environment variables
----------------------

``SIMULATE``
    ``true`` to use an Anvil Ethereum-mainnet fork. Default off (real mainnet).
``JSON_RPC_ETHEREUM``
    Ethereum mainnet RPC endpoint. Required.
``LIGHTER_PRIVATE_KEY``
    Funded deployer/asset-manager key. Required when not simulating.
``SIMULATE_FORK_BLOCK``
    Fork block (default 25000000, where the current ZkLighter implementation
    with ``USDC_ASSET_INDEX`` is active).
"""

import logging
import os
from decimal import Decimal

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    LagoonDeploymentParameters,
    deploy_automated_lagoon_vault,
)
from eth_defi.erc_4626.vault_protocol.lagoon.testing import fund_lagoon_vault
from eth_defi.hotwallet import HotWallet
from eth_defi.lighter.constants import LIGHTER_L1_CONTRACT, LIGHTER_USDC_ETHEREUM
from eth_defi.lighter.deployment import LighterDeployment
from eth_defi.provider.anvil import fork_network_anvil, fund_erc20_on_anvil, set_balance
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)

#: Deposit amount (USDC) used by the tutorial.
DEPOSIT_USDC = Decimal(100)


def setup_simulation_environment(json_rpc_url: str, fork_block: int) -> tuple:
    """Fork Ethereum mainnet and create a funded test wallet.

    :param json_rpc_url: Ethereum mainnet RPC to fork from.
    :param fork_block: Fixed fork block.
    :return: ``(web3, hot_wallet, anvil_launch)``.
    """
    print("\nStarting Anvil fork of Ethereum mainnet...")
    anvil_launch = fork_network_anvil(json_rpc_url, fork_block_number=fork_block)
    web3 = create_multi_provider_web3(anvil_launch.json_rpc_url, default_http_timeout=(3.0, 180.0))
    assert web3.eth.chain_id == 1, f"Expected Ethereum mainnet, got chain {web3.eth.chain_id}"

    print(f"  Fork running at: {anvil_launch.json_rpc_url}")
    print(f"  Forked at block: {web3.eth.block_number:,}")

    hot_wallet = HotWallet.create_for_testing(web3, test_account_n=0, eth_amount=0)
    hot_wallet.sync_nonce(web3)

    # Anvil overrides: ETH for gas + USDC for the deposit (no whale needed).
    set_balance(web3, hot_wallet.address, 10 * 10**18)
    fund_erc20_on_anvil(web3, LIGHTER_USDC_ETHEREUM, hot_wallet.address, int(DEPOSIT_USDC) * 10**6)

    print(f"\nSimulation wallet: {hot_wallet.address} (10 ETH, {DEPOSIT_USDC} USDC)")
    return web3, hot_wallet, anvil_launch


def deploy_lighter_vault(web3, hot_wallet: HotWallet, etherscan_api_key: str | None):
    """Deploy a Lagoon vault with Lighter L1 deposit whitelisting.

    The ``lighter_deployment`` parameter makes ``deploy_automated_lagoon_vault``
    deploy + link ``LighterLib`` and call ``whitelistLighter`` (ZkLighter
    contract + USDC + USDC asset index) plus ``allowReceiver(safe)``.
    """
    parameters = LagoonDeploymentParameters(
        underlying=LIGHTER_USDC_ETHEREUM,
        name="Lighter Trading Vault Tutorial",
        symbol="LIGHTER-VAULT",
    )

    print("\nDeploying Lagoon vault with Lighter integration...")
    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=hot_wallet,
        asset_manager=hot_wallet.address,
        parameters=parameters,
        safe_owners=[hot_wallet.address],
        safe_threshold=1,
        any_asset=False,
        lighter_deployment=LighterDeployment.create_ethereum(),
        use_forge=True,
        assets=[LIGHTER_USDC_ETHEREUM],
        etherscan_api_key=etherscan_api_key,
        between_contracts_delay_seconds=0.0,
    )
    vault = deploy_info.vault
    print(f"  Vault:  {vault.address}")
    print(f"  Safe:   {vault.safe_address}")
    print(f"  Module: {vault.trading_strategy_module_address}")
    return deploy_info


def deposit_usdc_into_lighter(web3, hot_wallet: HotWallet, vault) -> None:
    """Approve + deposit USDC from the Safe into the ZkLighter L1 contract.

    Both calls go through the guard via ``TradingStrategyModuleV0.performCall``
    (3-arg: target, callData, value). ``deposit``'s ``_to`` is the Safe, the
    only whitelisted Lighter receiver.
    """
    safe = vault.safe_address
    zk = get_deployed_contract(web3, "lighter/ZkLighter.json", LIGHTER_L1_CONTRACT)
    usdc = fetch_erc20_details(web3, LIGHTER_USDC_ETHEREUM)
    asset_index = zk.functions.USDC_ASSET_INDEX().call()
    amount_raw = usdc.convert_to_raw(DEPOSIT_USDC)

    module = get_deployed_contract(web3, "safe-integration/TradingStrategyModuleV0.json", vault.trading_strategy_module_address)
    asset_manager = hot_wallet.address

    print(f"\nDepositing {DEPOSIT_USDC} USDC into Lighter (asset index {asset_index})...")
    before = usdc.fetch_balance_of(safe)

    # 1. Safe approves USDC to ZkLighter (via the guard)
    approve_data = usdc.contract.functions.approve(LIGHTER_L1_CONTRACT, amount_raw)._encode_transaction_data()
    assert_transaction_success_with_explanation(web3, module.functions.performCall(usdc.address, approve_data, 0).transact({"from": asset_manager}))

    # 2. Safe deposits USDC into its Lighter account (via the guard)
    deposit_data = zk.functions.deposit(safe, asset_index, 0, amount_raw)._encode_transaction_data()
    assert_transaction_success_with_explanation(web3, module.functions.performCall(LIGHTER_L1_CONTRACT, deposit_data, 0).transact({"from": asset_manager}))

    after = usdc.fetch_balance_of(safe)
    print(f"  Safe USDC: {before} -> {after} (deposited into Lighter L1)")


def print_offchain_and_withdraw_notes() -> None:
    """Document the steps that cannot be simulated on a fork."""
    print("\nOff-chain / non-simulatable steps:\n  - Account registration: sign the Lighter linking message with the\n    Safe (EIP-712 / EIP-1271). No guard transaction.\n  - Trading: place orders off-chain via the Lighter L2 API / a\n    delegated trading key. Gasless, secured by zk-proofs.\n  - Withdraw back to the Safe: performCall ZkLighter.withdraw(...) to\n    move funds to the account's pending balance, then — after the\n    sequencer produces a zk-proof — performCall\n    ZkLighter.withdrawPendingBalance(safe, USDC_ASSET_INDEX, amount).\n    The pending balance only exists after settlement, so the claim is\n    skipped in simulation.")


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

    json_rpc_url = os.environ.get("JSON_RPC_ETHEREUM")
    if not json_rpc_url:
        raise ValueError("JSON_RPC_ETHEREUM is required")

    simulate = os.environ.get("SIMULATE", "").lower() in ("true", "1", "yes")
    etherscan_api_key = os.environ.get("ETHERSCAN_API_KEY")
    anvil_launch = None

    try:
        if simulate:
            fork_block = int(os.environ.get("SIMULATE_FORK_BLOCK", "25000000"))
            web3, hot_wallet, anvil_launch = setup_simulation_environment(json_rpc_url, fork_block)
        else:
            private_key = os.environ.get("LIGHTER_PRIVATE_KEY")
            if not private_key:
                raise ValueError("LIGHTER_PRIVATE_KEY is required when not simulating")
            web3 = create_multi_provider_web3(json_rpc_url)
            assert web3.eth.chain_id == 1, "Lighter deposits are on Ethereum mainnet"
            hot_wallet = HotWallet.from_private_key(private_key)
            hot_wallet.sync_nonce(web3)

        deploy_info = deploy_lighter_vault(web3, hot_wallet, etherscan_api_key)
        vault = deploy_info.vault

        # Fund the vault (ERC-7540 async deposit), then deposit into Lighter.
        print(f"\nDepositing {DEPOSIT_USDC} USDC into the vault...")
        fund_lagoon_vault(
            web3,
            vault_address=vault.address,
            asset_manager=hot_wallet.address,
            test_account_with_balance=hot_wallet.address,
            trading_strategy_module_address=vault.trading_strategy_module_address,
            amount=DEPOSIT_USDC,
            hot_wallet=hot_wallet,
        )

        deposit_usdc_into_lighter(web3, hot_wallet, vault)
        print_offchain_and_withdraw_notes()
        print("\nDone. On-chain Lighter deposit lifecycle simulated successfully.")
    finally:
        if anvil_launch is not None:
            anvil_launch.close()


if __name__ == "__main__":
    main()
