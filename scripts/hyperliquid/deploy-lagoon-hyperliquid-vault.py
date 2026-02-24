"""Deploy a Lagoon vault on HyperEVM and exercise Hypercore vault deposit/withdrawal.

A quick example script to test/simulate Lagoon vault deployment on HyperEVM.
The deployment script deals with the lack of deployed protocol contracts (Lagoon, Safe) on HyperEVM testnet,
and also deals with HyperEVM big block limitation.

We recommend using HyperEVM mainnet for testing. It's cheap and it will be more hassle/costly to fund HyperEVM testnet accounts.

Because of the big block usage, this script may take several minutes to run.

In ``SIMULATE`` mode the script forks the selected network via Anvil and deploys
mock CoreWriter/CoreDepositWallet contracts so no real funds are needed.
Without ``SIMULATE`` the script connects to the live network and requires a
funded deployer key.

You can also reconnect to an existing Lagoon deployment by setting
``LAGOON_VAULT`` and ``TRADING_STRATEGY_MODULE`` environment variables.
This is useful for the testnet withdrawal test: deploy + deposit on day 1,
then come back on day 2 with the same addresses to run withdrawal only.

Account funding for HyperEVM testnet
------------------------------------

1. Create a new private key and set ``HYPERCORE_WRITER_TEST_PRIVATE_KEY`` env
2. Move ~$2 worth of ETH on Arbitrum to that address
3. Move ~$5 worth of USDC on Arbitrum to that address
4. Sign in to https://app.hyperliquid.xyz with the new account
5. Deposit $5 USDC (minimum)
6. Now you have an account on Hyperliquid mainnet
7. Visit https://app.hyperliquid-testnet.xyz/drip and claim
8. Now you have 1000 USDC on the Hypercore testnet
9. Buy 1 HYPE with the mock USDC (set max slippage to 99%,
   testnet orderbook is illiquid)
10. Visit https://app.hyperliquid-testnet.xyz/portfolio — click EVM <-> CORE
11. Move 100 USDC to HyperEVM testnet
12. Move 0.01 HYPE to HyperEVM testnet
13. Check HyperEVM testnet balance on EVM <-> CORE dialog
    (there is no working HyperEVM testnet explorer)

Environment variables
---------------------
- ``NETWORK``: ``mainnet`` or ``testnet`` (default: ``testnet``).
  Selects the RPC URL and chain parameters.
- ``JSON_RPC_HYPERLIQUID``: HyperEVM mainnet RPC URL.
  Read from environment when ``NETWORK=mainnet``.
- ``JSON_RPC_HYPERLIQUID_TESTNET``: HyperEVM testnet RPC URL.
  Defaults to ``https://rpc.hyperliquid-testnet.xyz/evm``
  when ``NETWORK=testnet``.
- ``HYPERCORE_WRITER_TEST_PRIVATE_KEY``: Deployer private key (required on live network;
  defaults to Anvil account #0 in SIMULATE mode)
- ``SIMULATE``: Set to any value to fork via Anvil (default: unset)
- ``ACTION``: ``deposit``, ``withdraw``, or ``both`` (default: ``both``).
  On testnet you may need to wait 1 day between deposit and withdrawal
  due to the vault lock-up period, so run deposit first, then withdraw
  later.
- ``HYPERCORE_VAULT``: Hypercore vault address to deposit into.
  Defaults to the HLP vault for the selected network
  (testnet: ``0xa15099a30bbf2e68942d6f4c43d70d04faeab0a0``,
  mainnet: ``0xdfc24b077bc1425ad1dea75bcb6f8158e10df303``).
- ``USDC_AMOUNT``: USDC amount in human units for the vault deposit (default: ``2``).
  On live networks, an additional 2 USDC is automatically added for
  HyperCore account activation (1 USDC creation fee + 1 USDC reaches spot).
- ``DEPOSIT_MODE``: ``two_phase`` (default) or ``batched``.
  ``two_phase`` splits bridge and vault deposit with an escrow wait;
  ``batched`` uses a single multicall but can silently fail under load.
- ``LOG_LEVEL``: Logging level (default: ``info``)

Reconnecting to an existing deployment:

- ``LAGOON_VAULT``: Existing Lagoon vault address. When set, skips
  deployment and whitelisting entirely.
- ``TRADING_STRATEGY_MODULE``: Existing TradingStrategyModuleV0 address
  (required when ``LAGOON_VAULT`` is set).

Usage::

    # Simulate on Anvil fork (no real funds needed)
    SIMULATE=true python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py

    # Simulate testnet on Anvil fork
    SIMULATE=true NETWORK=testnet python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py

    # Testnet deposit only (deploy + deposit, wait 1 day before withdrawal)
    NETWORK=testnet ACTION=deposit USDC_AMOUNT=1 python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py

    # Mainnet deposit only
    NETWORK=mainnet ACTION=deposit USDC_AMOUNT=1 python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py

    # Testnet withdrawal (reconnect to existing deployment after lock-up)
    HYPERCORE_WRITER_TEST_PRIVATE_KEY=0x... NETWORK=testnet \
        ACTION=withdraw HYPERCORE_VAULT=0xabc... USDC_AMOUNT=5 \
        LAGOON_VAULT=0xdef... TRADING_STRATEGY_MODULE=0x123... \
        poetry run python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py

Troubleshooting
---------------

If deposit lands on EVM but the vault position is missing, use
``check-hypercore-user.py`` to inspect the Safe's HyperCore state
(spot balances, EVM escrows, perp account, vault positions)::

    ADDRESS=<safe_address> NETWORK=mainnet \
        poetry run python scripts/hyperliquid/check-hypercore-user.py

Common issues:

- **USDC stuck in EVM escrow**: the bridge step succeeded but HyperCore
  has not yet processed the deposit. Wait and re-check.
- **USDC in spot but no vault position**: ``transferUsdClass`` or
  ``vaultTransfer`` failed silently on HyperCore. Re-submit phase 2.
- **USDC in perp but no vault position**: ``vaultTransfer`` failed
  (e.g. vault lock-up, wrong vault address). Check vault address.

For more information see `README-Hypercore-guard.md`.
"""

import logging
import os
import random
import time
from decimal import Decimal

from eth_account import Account
from eth_typing import HexAddress, HexStr
from safe_eth.safe.safe import Safe
from tabulate import tabulate
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LAGOON_BEACON_PROXY_FACTORIES, LagoonConfig, LagoonDeploymentParameters, deploy_automated_lagoon_vault
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.gas import estimate_gas_price
from eth_defi.hotwallet import HotWallet
from eth_defi.hyperliquid.api import fetch_user_vault_equities
from eth_defi.hyperliquid.core_writer import build_hypercore_deposit_multicall, build_hypercore_deposit_phase1, build_hypercore_deposit_phase2, build_hypercore_withdraw_multicall
from eth_defi.hyperliquid.evm_escrow import DEFAULT_ACTIVATION_AMOUNT, activate_account, is_account_activated, wait_for_evm_escrow_clear
from eth_defi.hyperliquid.session import HYPERLIQUID_API_URL, HYPERLIQUID_TESTNET_API_URL, create_hyperliquid_session
from eth_defi.hyperliquid.testing import setup_anvil_hypercore_mocks
from eth_defi.provider.anvil import ANVIL_PRIVATE_KEY, fork_network_anvil, fund_erc20_on_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.safe.execute import execute_safe_tx
from eth_defi.safe.safe_compat import create_safe_ethereum_client
from eth_defi.token import USDC_NATIVE_TOKEN, fetch_erc20_details
from eth_defi.trace import TransactionAssertionError, assert_transaction_success_with_explanation
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec

logger = logging.getLogger(__name__)

#: Default Hypercore vault address per network (HLP on each network)
DEFAULT_VAULTS = {
    "testnet": "0xa15099a30bbf2e68942d6f4c43d70d04faeab0a0",
    "mainnet": "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303",
}

#: Default public RPC for HyperEVM testnet
HYPERLIQUID_TESTNET_RPC = "https://rpc.hyperliquid-testnet.xyz/evm"


def _print_hypercore_balances(
    safe_address: str,
    network: str,
    simulate: bool,
) -> list:
    """Query Hyperliquid info API and print the Safe's Hypercore vault balances.

    Skipped in SIMULATE mode (Anvil mocks CoreWriter, no real Hypercore state).

    :return:
        List of :py:class:`~eth_defi.hyperliquid.api.UserVaultEquity` positions,
        or empty list in simulate mode.
    """
    if simulate:
        logger.info("Skipping Hypercore balance check in SIMULATE mode")
        return []

    api_url = HYPERLIQUID_TESTNET_API_URL if network == "testnet" else HYPERLIQUID_API_URL
    session = create_hyperliquid_session(api_url=api_url)
    equities = fetch_user_vault_equities(session, user=safe_address)
    if equities:
        rows = [[eq.vault_address, f"{eq.equity:,.6f}", eq.locked_until.isoformat()] for eq in equities]
        print("\nHypercore vault balances (Safe):")
        print(tabulate(rows, headers=["Vault", "Equity (USDC)", "Locked until (UTC)"], tablefmt="simple"))
    else:
        print("\nHypercore vault balances: none (Safe has no vault deposits on Hypercore)")

    return equities


def _do_deposit(
    lagoon_vault,
    usdc_amount: int,
    hypercore_amount: int,
    vault_address: str,
    deployer: HotWallet,
    usdc_human: int,
    network: str,
    simulate: bool,
    deposit_mode: str = "batched",
):
    """Execute deposit into a Hypercore vault via Lagoon.

    Supports two deposit modes (set via ``DEPOSIT_MODE`` env var):

    - ``batched`` (default): Single multicall with all 4 steps. The Safe
      must be activated on HyperCore first (done automatically).
    - ``two_phase``: Splits into bridge (phase 1) + escrow wait +
      vault deposit (phase 2). Safer under heavy HyperCore load.

    In SIMULATE mode, always uses batched (Anvil mocks, no real escrow).
    """
    web3 = lagoon_vault.web3

    # Build session with the correct API URL for escrow polling
    api_url = HYPERLIQUID_TESTNET_API_URL if network == "testnet" else HYPERLIQUID_API_URL
    session = create_hyperliquid_session(api_url=api_url) if not simulate else None

    # On live networks, ensure the Safe is activated on HyperCore
    if not simulate:
        if not is_account_activated(web3, user=lagoon_vault.safe_address):
            logger.info("Safe %s not activated on HyperCore, activating...", lagoon_vault.safe_address)
            activate_account(
                web3=web3,
                lagoon_vault=lagoon_vault,
                deployer=deployer,
                session=session,
            )
            deployer.sync_nonce(web3)

    if not simulate:
        assert deposit_mode != "batched", "Batched deposit mode is disabled on live networks. The batched multicall puts all 4 steps (approve, CDW.deposit, transferUsdClass, vaultTransfer) into a single EVM block. Steps 3-4 are CoreWriter actions that depend on the CDW bridge (step 2) having cleared the EVM escrow, but because they land in the same block, HyperCore may process the CoreWriter actions before the bridge clears — causing steps 3-4 to silently fail while the EVM transaction succeeds. The USDC ends up stuck in spot or perp with no vault position. Use DEPOSIT_MODE=two_phase (default) which waits for the escrow to clear between the bridge and the CoreWriter actions."

    if simulate or deposit_mode == "batched":
        # Batched: single multicall with all 4 steps (simulate only)
        label = "batched (simulate)" if simulate else "batched"
        logger.info("Executing %s deposit (%d USDC)...", label, usdc_human)

        fn = build_hypercore_deposit_multicall(
            lagoon_vault=lagoon_vault,
            evm_usdc_amount=usdc_amount,
            hypercore_usdc_amount=hypercore_amount,
            vault_address=vault_address,
            check_activation=not simulate,
        )

        if simulate:
            tx_hash = fn.transact({"from": deployer.address})
        else:
            tx_hash = deployer.transact_and_broadcast_with_contract(fn)

        receipt = assert_transaction_success_with_explanation(web3, tx_hash)
        deposit_results = [
            ["Transaction", tx_hash.hex()],
            ["Gas used", receipt["gasUsed"]],
            ["Block", receipt["blockNumber"]],
            ["USDC amount", f"{usdc_human:,}"],
            ["Vault", vault_address],
            ["Mode", label],
        ]
        print("\nDeposit results:")
        print(tabulate(deposit_results, tablefmt="simple"))

    elif deposit_mode == "two_phase":
        # Two-phase deposit: bridge first, wait for escrow, then batch
        # the spot-to-perp and perp-to-vault CoreWriter actions together.

        # Phase 1: bridge USDC from EVM to HyperCore spot
        logger.info("Phase 1: bridging %d USDC to HyperCore spot...", usdc_human)
        fn1 = build_hypercore_deposit_phase1(
            lagoon_vault=lagoon_vault,
            evm_usdc_amount=usdc_amount,
        )
        tx_hash = deployer.transact_and_broadcast_with_contract(fn1)
        receipt = assert_transaction_success_with_explanation(web3, tx_hash)
        logger.info("Phase 1 tx: %s (gas: %d)", tx_hash.hex(), receipt["gasUsed"])

        # Wait for EVM escrow to clear
        logger.info("Waiting for EVM escrow to clear...")
        wait_for_evm_escrow_clear(session, user=lagoon_vault.safe_address)

        # Phase 2: move USDC from spot to perp and deposit into vault
        logger.info("Phase 2: transferUsdClass + vaultTransfer...")
        deployer.sync_nonce(web3)
        fn2 = build_hypercore_deposit_phase2(
            lagoon_vault=lagoon_vault,
            hypercore_usdc_amount=hypercore_amount,
            vault_address=vault_address,
        )
        tx_hash = deployer.transact_and_broadcast_with_contract(fn2)
        receipt = assert_transaction_success_with_explanation(web3, tx_hash)

        deposit_results = [
            ["Phase 2 tx", tx_hash.hex()],
            ["Gas used", receipt["gasUsed"]],
            ["Block", receipt["blockNumber"]],
            ["USDC amount", f"{usdc_human:,}"],
            ["Vault", vault_address],
            ["Mode", "two_phase"],
        ]
        print("\nDeposit results:")
        print(tabulate(deposit_results, tablefmt="simple"))

    else:
        raise ValueError(f"Unknown deposit mode: {deposit_mode!r} (expected 'batched' or 'two_phase')")

    # CoreWriter actions are asynchronous: the EVM transaction only queues
    # the action, and HyperCore processes it with a few seconds delay.
    # Wait before checking balances so the deposit has time to land.
    if not simulate:
        logger.info("Waiting 10s for CoreWriter actions to settle on HyperCore...")
        time.sleep(10)
    equities = _print_hypercore_balances(lagoon_vault.safe_address, network, simulate)
    if not simulate:
        assert len(equities) > 0, f"Deposit failed: Safe {lagoon_vault.safe_address} has no vault positions on HyperCore after deposit"


def _do_withdraw(
    lagoon_vault,
    hypercore_amount: int,
    vault_address: str,
    deployer: HotWallet,
    usdc_human: int,
    network: str,
    simulate: bool,
):
    """Execute withdrawal via multicall."""
    web3 = lagoon_vault.web3
    logger.info("Executing multicall withdrawal (%d USDC)...", usdc_human)
    fn = build_hypercore_withdraw_multicall(
        lagoon_vault=lagoon_vault,
        hypercore_usdc_amount=hypercore_amount,
        vault_address=vault_address,
    )
    try:
        if simulate:
            tx_hash = fn.transact({"from": deployer.address})
        else:
            tx_hash = deployer.transact_and_broadcast_with_contract(fn)
        receipt = assert_transaction_success_with_explanation(web3, tx_hash)
        withdraw_results = [
            ["Transaction", tx_hash.hex()],
            ["Gas used", receipt["gasUsed"]],
            ["Block", receipt["blockNumber"]],
            ["USDC amount", f"{usdc_human:,}"],
        ]
        print("\nWithdrawal results:")
        print(tabulate(withdraw_results, tablefmt="simple"))
    except (TransactionAssertionError, Exception) as e:
        logger.warning(
            "Withdrawal failed (expected if vault has lock-up period): %s",
            str(e)[:200],
        )
        print(f"\nWithdrawal skipped: {str(e)[:200]}")
        return

    # CoreWriter actions are asynchronous: the EVM transaction only queues
    # the action, and HyperCore processes it with a few seconds delay.
    # Wait before checking balances so the withdrawal has time to settle.
    if not simulate:
        logger.info("Waiting 10s for CoreWriter actions to settle on HyperCore...")
        time.sleep(10)
    _print_hypercore_balances(lagoon_vault.safe_address, network, simulate)

    # Transfer remaining USDC from Safe back to deployer via Safe
    # multisig execTransaction (bypasses the guard, which would block
    # performCall transfers to non-whitelisted receivers).
    usdc_token = lagoon_vault.underlying_token
    safe_balance = usdc_token.fetch_balance_of(lagoon_vault.safe_address)
    if safe_balance > 0:
        logger.info("Transferring %s USDC from Safe back to deployer %s", safe_balance, deployer.address)
        raw_amount = usdc_token.convert_to_raw(safe_balance)
        transfer_data = usdc_token.contract.functions.transfer(
            deployer.address,
            raw_amount,
        ).build_transaction({"from": lagoon_vault.safe_address})["data"]

        if simulate:
            # In Anvil simulate mode, impersonate the Safe and call the
            # USDC contract directly (bypasses signer requirement).
            # Fund the Safe with HYPE for gas first.
            web3.provider.make_request("anvil_setBalance", [lagoon_vault.safe_address, hex(10**18)])
            web3.provider.make_request("anvil_impersonateAccount", [lagoon_vault.safe_address])
            tx_hash = web3.eth.send_transaction(
                {
                    "from": lagoon_vault.safe_address,
                    "to": usdc_token.address,
                    "data": transfer_data,
                }
            )
            web3.provider.make_request("anvil_stopImpersonatingAccount", [lagoon_vault.safe_address])
        else:
            ethereum_client = create_safe_ethereum_client(web3)
            safe = Safe(lagoon_vault.safe_address, ethereum_client)
            safe_tx = safe.build_multisig_tx(
                usdc_token.address,
                0,
                bytes.fromhex(transfer_data[2:]),
            )
            safe_tx.sign(deployer.private_key.hex())
            gas_estimate = estimate_gas_price(web3)
            deployer.sync_nonce(web3)
            tx_hash, _tx = execute_safe_tx(
                safe_tx,
                tx_sender_private_key=deployer.private_key.hex(),
                tx_gas=100_000,
                tx_nonce=deployer.allocate_nonce(),
                gas_fee=gas_estimate,
            )
        assert_transaction_success_with_explanation(web3, tx_hash)
        logger.info("USDC returned to deployer: tx %s", tx_hash.hex())


def main():
    log_level = os.environ.get("LOG_LEVEL", "info")
    setup_console_logging(default_log_level=log_level)

    network = os.environ.get("NETWORK", "testnet").lower()
    assert network in ("mainnet", "testnet"), f"NETWORK must be 'mainnet' or 'testnet', got '{network}'"

    if network == "testnet":
        json_rpc = os.environ.get("JSON_RPC_HYPERLIQUID_TESTNET", HYPERLIQUID_TESTNET_RPC)
    else:
        json_rpc = os.environ.get("JSON_RPC_HYPERLIQUID")
        assert json_rpc, "JSON_RPC_HYPERLIQUID environment variable required for mainnet"

    simulate = os.environ.get("SIMULATE")
    action = os.environ.get("ACTION", "both").lower()
    assert action in ("deposit", "withdraw", "both"), f"ACTION must be 'deposit', 'withdraw', or 'both', got '{action}'"

    private_key = os.environ.get("HYPERCORE_WRITER_TEST_PRIVATE_KEY", ANVIL_PRIVATE_KEY if simulate else None)
    assert private_key, "HYPERCORE_WRITER_TEST_PRIVATE_KEY environment variable required (or set SIMULATE=true)"

    vault_address = HexAddress(HexStr(os.environ.get("HYPERCORE_VAULT", DEFAULT_VAULTS[network])))
    # Minimum vault deposit is 5 USDC
    usdc_human = int(os.environ.get("USDC_AMOUNT", "5"))
    deposit_mode = os.environ.get("DEPOSIT_MODE", "two_phase").lower()
    assert deposit_mode in ("batched", "two_phase"), f"DEPOSIT_MODE must be 'batched' or 'two_phase', got '{deposit_mode}'"

    # Existing deployment addresses (skip deploy + whitelist when set)
    existing_lagoon_vault = os.environ.get("LAGOON_VAULT")
    existing_module = os.environ.get("TRADING_STRATEGY_MODULE")

    if existing_lagoon_vault:
        assert existing_module, "TRADING_STRATEGY_MODULE required when LAGOON_VAULT is set"

    # Connect to network
    anvil = None
    if simulate:
        logger.info("SIMULATE mode: forking HyperEVM %s via Anvil (RPC: %s)", network, json_rpc)
        anvil = fork_network_anvil(
            json_rpc,
            gas_limit=30_000_000,
        )
        web3 = create_multi_provider_web3(anvil.json_rpc_url, default_http_timeout=(3, 500.0))
    else:
        logger.info("Live %s mode (RPC: %s)", network, json_rpc)
        web3 = create_multi_provider_web3(json_rpc, default_http_timeout=(3, 500.0))

    chain_id = web3.eth.chain_id
    logger.info("Connected to chain %d, block %d", chain_id, web3.eth.block_number)

    deployer_account = Account.from_key(private_key)
    deployer = HotWallet(deployer_account)
    deployer.sync_nonce(web3)
    logger.info("Deployer: %s", deployer.address)

    usdc_address = USDC_NATIVE_TOKEN[chain_id]
    usdc = fetch_erc20_details(web3, usdc_address)
    usdc_amount = usdc.convert_to_raw(usdc_human)
    hypercore_amount = usdc_amount  # Hypercore uses same decimals as EVM USDC

    # On live networks doing deposits, the Safe needs extra USDC for
    # HyperCore account activation (2 USDC: 1 USDC fee + 1 USDC reaches spot).
    # In simulate mode, activation is skipped (no real HyperCore state).
    activation_raw = DEFAULT_ACTIVATION_AMOUNT if (not simulate and action in ("deposit", "both")) else 0
    activation_human = activation_raw / 10**6
    total_safe_funding_raw = usdc_amount + activation_raw
    total_safe_funding_human = usdc_human + activation_human

    # Check deployer has enough HYPE (gas) and USDC before doing anything expensive
    if not simulate:
        hype_balance = web3.eth.get_balance(deployer_account.address)
        hype_human = hype_balance / 10**18
        min_hype = 0.1
        assert hype_human >= min_hype, f"Deployer {deployer_account.address} has {hype_human:.4f} HYPE, need at least {min_hype} HYPE for gas"

        deployer_usdc_human = usdc.fetch_balance_of(deployer_account.address)
        min_usdc = total_safe_funding_human
        assert deployer_usdc_human >= min_usdc, f"Deployer {deployer_account.address} has {deployer_usdc_human:.2f} USDC, need at least {min_usdc:.0f} USDC ({usdc_human} deposit + {activation_human:.0f} activation)"
        logger.info("Deployer balances: %.4f HYPE, %.2f USDC", hype_human, deployer_usdc_human)

    # Track HYPE (gas) usage across all phases
    hype_start = web3.eth.get_balance(deployer_account.address)

    if existing_lagoon_vault:
        # Reconnect to an existing Lagoon deployment
        logger.info("Reconnecting to existing deployment: vault=%s module=%s", existing_lagoon_vault, existing_module)
        lagoon_vault = LagoonVault(
            web3,
            VaultSpec(chain_id, existing_lagoon_vault),
            trading_strategy_module_address=existing_module,
            default_block_identifier="latest",
        )
        safe_address = lagoon_vault.safe_address
        module = lagoon_vault.trading_strategy_module
        logger.info("Vault:  %s", lagoon_vault.vault_address)
        logger.info("Safe:   %s", safe_address)
        logger.info("Module: %s", module.address)
    else:
        # Fresh deployment via deploy_automated_lagoon_vault()
        if simulate:
            setup_anvil_hypercore_mocks(web3, deployer_account.address)

        logger.info("Deploying Lagoon vault...")
        # Deploy from scratch when there is no pre-deployed factory on the chain.
        # Testnet (998) has no factory; mainnet (999) has an OptinProxyFactory
        # at 0x90beB507A1BA7D64633540cbce615B574224CD84 so we use it.
        from_the_scratch = chain_id not in LAGOON_BEACON_PROXY_FACTORIES
        assert not (from_the_scratch and network == "mainnet"), f"Mainnet (chain {chain_id}) should have a Lagoon factory in LAGOON_BEACON_PROXY_FACTORIES — from-scratch deployment is not supported on mainnet"
        if from_the_scratch:
            logger.info("No Lagoon factory on chain %d, deploying from scratch", chain_id)

        config = LagoonConfig(
            parameters=LagoonDeploymentParameters(
                underlying=usdc_address,
                name="HyperEVM Hypercore Manual Test",
                symbol="TEST",
            ),
            asset_manager=deployer_account.address,
            safe_owners=[deployer_account.address],
            safe_threshold=1,
            any_asset=False,
            hypercore_vaults=[vault_address],
            safe_salt_nonce=random.randint(0, 1000) if not from_the_scratch else None,
            from_the_scratch=from_the_scratch,
            use_forge=from_the_scratch,  # Required for from_the_scratch
            between_contracts_delay_seconds=8.0,  # Speed up deployment by waiting less
        )

        deploy_info = deploy_automated_lagoon_vault(
            web3=web3,
            deployer=deployer,
            config=config,
        )

        lagoon_vault = deploy_info.vault
        module = deploy_info.trading_strategy_module
        safe_address = deploy_info.safe.address

        logger.info("Vault:  %s", lagoon_vault.vault_address)
        logger.info("Safe:   %s", safe_address)
        logger.info("Module: %s", module.address)

        hype_after_deploy = web3.eth.get_balance(deployer_account.address)
        deploy_cost = (hype_start - hype_after_deploy) / 10**18
        logger.info("Deployment gas cost: %.6f HYPE", deploy_cost)

        # Fund Safe with USDC (deposit amount + activation overhead on live)
        if simulate:
            fund_erc20_on_anvil(web3, usdc_address, safe_address, usdc_amount)
        else:
            # Wait for RPC nodes to catch up with the latest nonce after
            # deployment, then sync HotWallet's internal nonce counter
            time.sleep(2)
            deployer.sync_nonce(web3)

            # Transfer USDC from deployer to Safe on live network.
            # Includes activation overhead (2 USDC) so the Safe has enough
            # for both account activation and the vault deposit.
            safe_balance = usdc.fetch_balance_of(safe_address)
            if safe_balance < total_safe_funding_human:
                transfer_amount = Decimal(str(total_safe_funding_human)) - safe_balance
                logger.info(
                    "Transferring %s USDC from deployer to Safe %s (%d deposit + %d activation)",
                    transfer_amount,
                    safe_address,
                    usdc_human,
                    int(activation_human),
                )
                tx_hash = deployer.transact_and_broadcast_with_contract(
                    usdc.transfer(safe_address, transfer_amount),
                    gas_limit=100_000,
                )
                assert_transaction_success_with_explanation(web3, tx_hash)
                logger.info("USDC transfer to Safe complete: tx %s", tx_hash.hex())

    balance = usdc.fetch_balance_of(safe_address)
    logger.info("Safe USDC balance: %s", balance)

    # In SIMULATE mode, impersonate the deployer so eth_sendTransaction works
    # (the deployer may not be an Anvil-unlocked account if HYPERCORE_WRITER_TEST_PRIVATE_KEY is set)
    if simulate:
        web3.provider.make_request("anvil_impersonateAccount", [deployer_account.address])

    if action in ("deposit", "both"):
        balance_raw = usdc.contract.functions.balanceOf(Web3.to_checksum_address(safe_address)).call()
        assert balance_raw >= total_safe_funding_raw, f"Safe USDC balance {balance} ({balance_raw} raw) insufficient, need {total_safe_funding_human} ({total_safe_funding_raw} raw): {usdc_human} deposit + {activation_human} activation"
        _do_deposit(
            lagoon_vault,
            usdc_amount,
            hypercore_amount,
            vault_address,
            deployer,
            usdc_human,
            network=network,
            simulate=bool(simulate),
            deposit_mode=deposit_mode,
        )

    if action in ("withdraw", "both"):
        _do_withdraw(
            lagoon_vault,
            hypercore_amount,
            vault_address,
            deployer,
            usdc_human,
            network=network,
            simulate=bool(simulate),
        )

    if simulate:
        web3.provider.make_request("anvil_stopImpersonatingAccount", [deployer_account.address])

    # Summary
    hype_end = web3.eth.get_balance(deployer_account.address)
    total_hype_spent = (hype_start - hype_end) / 10**18
    final_balance = usdc.fetch_balance_of(safe_address)
    summary = [
        ["Network", network],
        ["Vault", lagoon_vault.vault_address],
        ["Safe", safe_address],
        ["Module", module.address],
        ["Chain ID", chain_id],
        ["Action", action],
        ["USDC amount", f"{usdc_human:,}"],
        ["Final USDC balance", f"{final_balance:,.2f}"],
        ["HYPE spent (gas)", f"{total_hype_spent:.6f}"],
        ["Simulate", "yes" if simulate else "no"],
    ]
    print("\nSummary:")
    print(tabulate(summary, tablefmt="simple"))

    if anvil:
        anvil.close()
        logger.info("Anvil stopped")


if __name__ == "__main__":
    main()
