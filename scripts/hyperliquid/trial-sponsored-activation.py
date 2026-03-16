"""Trial: sponsored HyperCore account activation via deployer EOA.

Tests whether the deployer EOA calling ``CoreDepositWallet.depositFor(target, amount, SPOT_DEX)``
directly (not through the Safe's trading strategy module) can activate a
contract address on HyperCore.

This is a workaround for the testnet issue where ``depositFor`` called through
a Safe multisig does not create HyperCore accounts for contract addresses.
See https://github.com/hyperliquid-dex/node/issues/138

Environment variables:

- ``NETWORK``: ``testnet`` (default) or ``mainnet``
- ``TARGET_ADDRESS``: Address to activate. If not set and ``DEPLOY_FRESH=true``,
  deploys a fresh Lagoon vault and uses its Safe address.
- ``DEPLOY_FRESH``: Set to ``true`` to deploy a fresh Lagoon vault on testnet.
  Requires ``HYPERCORE_WRITER_TEST_PRIVATE_KEY``.
- ``ACTIVATION_AMOUNT``: USDC amount in human units (default: ``5``).
- ``ACTIVATION_TIMEOUT``: Seconds to wait for activation (default: ``180``).
- ``HYPERCORE_WRITER_TEST_PRIVATE_KEY``: Deployer private key.
- ``JSON_RPC_HYPERLIQUID``: Mainnet RPC URL (required for mainnet).
- ``LOG_LEVEL``: Logging level (default: ``info``).

Usage::

    # Trial 1: testnet with fresh Safe
    source .local-test.env && \\
    NETWORK=testnet DEPLOY_FRESH=true ACTIVATION_AMOUNT=5 ACTIVATION_TIMEOUT=180 \\
        poetry run python scripts/hyperliquid/trial-sponsored-activation.py

    # Trial 2: mainnet with existing address
    source .local-test.env && \\
    NETWORK=mainnet TARGET_ADDRESS=0x... ACTIVATION_AMOUNT=2 ACTIVATION_TIMEOUT=60 \\
        poetry run python scripts/hyperliquid/trial-sponsored-activation.py
"""

import logging
import os
import random
import time

from eth_account import Account
from eth_typing import HexAddress, HexStr
from web3 import Web3

from eth_defi.hotwallet import HotWallet
from eth_defi.hyperliquid.evm_escrow import activate_account_sponsored, is_account_activated
from eth_defi.hyperliquid.session import HYPERLIQUID_API_URL, HYPERLIQUID_TESTNET_API_URL, create_hyperliquid_session
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN, fetch_erc20_details
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)

HYPERLIQUID_TESTNET_RPC = "https://rpc.hyperliquid-testnet.xyz/evm"

#: Default Hypercore vault address per network (HLP on each network)
DEFAULT_VAULTS = {
    "testnet": "0xa15099a30bbf2e68942d6f4c43d70d04faeab0a0",
    "mainnet": "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303",
}


def deploy_fresh_safe(web3: Web3, deployer: HotWallet, network: str) -> str:
    """Deploy a fresh Lagoon vault and return the Safe address.

    :return:
        The Safe address as a hex string.
    """
    from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
        LAGOON_BEACON_PROXY_FACTORIES,
        LagoonConfig,
        LagoonDeploymentParameters,
        deploy_automated_lagoon_vault,
    )
    from eth_defi.hyperliquid.testing import setup_anvil_hypercore_mocks

    chain_id = web3.eth.chain_id
    usdc_address = USDC_NATIVE_TOKEN[chain_id]
    vault_address = DEFAULT_VAULTS[network]

    from_the_scratch = chain_id not in LAGOON_BEACON_PROXY_FACTORIES
    if from_the_scratch:
        logger.info("No Lagoon factory on chain %d, deploying from scratch", chain_id)

    config = LagoonConfig(
        parameters=LagoonDeploymentParameters(
            underlying=usdc_address,
            name="Sponsored Activation Trial",
            symbol="TRIAL",
        ),
        asset_manager=None,
        asset_managers=[deployer.address],
        safe_owners=[deployer.address],
        safe_threshold=1,
        any_asset=False,
        hypercore_vaults=[vault_address],
        safe_salt_nonce=random.randint(0, 1000) if not from_the_scratch else None,
        from_the_scratch=from_the_scratch,
        use_forge=from_the_scratch,
        between_contracts_delay_seconds=8.0,
    )

    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=deployer,
        config=config,
    )

    safe_address = deploy_info.safe.address
    logger.info("Deployed fresh Lagoon vault")
    logger.info("  Vault:  %s", deploy_info.vault.vault_address)
    logger.info("  Safe:   %s", safe_address)
    logger.info("  Module: %s", deploy_info.trading_strategy_module.address)

    return safe_address


def main():
    log_level = os.environ.get("LOG_LEVEL", "info")
    setup_console_logging(default_log_level=log_level)

    network = os.environ.get("NETWORK", "testnet").lower()
    assert network in ("mainnet", "testnet"), f"NETWORK must be 'mainnet' or 'testnet', got '{network}'"

    if network == "testnet":
        json_rpc = os.environ.get("JSON_RPC_HYPERLIQUID_TESTNET", HYPERLIQUID_TESTNET_RPC)
        api_url = HYPERLIQUID_TESTNET_API_URL
    else:
        json_rpc = os.environ.get("JSON_RPC_HYPERLIQUID")
        assert json_rpc, "JSON_RPC_HYPERLIQUID environment variable required for mainnet"
        api_url = HYPERLIQUID_API_URL

    private_key = os.environ.get("HYPERCORE_WRITER_TEST_PRIVATE_KEY")
    assert private_key, "HYPERCORE_WRITER_TEST_PRIVATE_KEY environment variable required"

    activation_human = int(os.environ.get("ACTIVATION_AMOUNT", "5"))
    activation_timeout = float(os.environ.get("ACTIVATION_TIMEOUT", "180"))
    deploy_fresh = os.environ.get("DEPLOY_FRESH", "").lower() == "true"
    target_address = os.environ.get("TARGET_ADDRESS")

    assert target_address or deploy_fresh, "Either TARGET_ADDRESS or DEPLOY_FRESH=true is required"

    # Connect
    web3 = create_multi_provider_web3(json_rpc, default_http_timeout=(3, 500.0))
    chain_id = web3.eth.chain_id
    logger.info("Connected to %s chain %d, block %d", network, chain_id, web3.eth.block_number)

    deployer_account = Account.from_key(private_key)
    deployer = HotWallet(deployer_account)
    deployer.sync_nonce(web3)
    logger.info("Deployer: %s", deployer.address)

    # Check deployer balances
    hype_balance = web3.eth.get_balance(deployer_account.address)
    hype_human = hype_balance / 10**18
    logger.info("Deployer HYPE balance: %.6f", hype_human)

    usdc_address = USDC_NATIVE_TOKEN[chain_id]
    usdc = fetch_erc20_details(web3, usdc_address)
    deployer_usdc = usdc.fetch_balance_of(deployer_account.address)
    logger.info("Deployer EVM USDC balance: %.2f", deployer_usdc)

    activation_raw = activation_human * 10**6

    assert hype_human >= 0.01, f"Deployer needs at least 0.01 HYPE for gas, has {hype_human:.6f}"
    assert deployer_usdc >= activation_human, f"Deployer needs at least {activation_human} EVM USDC for activation, has {deployer_usdc:.2f}"

    # Deploy fresh Safe if requested
    if deploy_fresh:
        logger.info("Deploying fresh Lagoon vault + Safe...")
        target_address = deploy_fresh_safe(web3, deployer, network)
        # Sync nonce after deployment
        time.sleep(2)
        deployer.sync_nonce(web3)
    else:
        target_address = Web3.to_checksum_address(target_address)

    # Check current activation status
    already_activated = is_account_activated(web3, target_address)
    logger.info("Target %s currently activated: %s", target_address, already_activated)
    if already_activated:
        print(f"\nTarget {target_address} is ALREADY activated on HyperCore ({network}). Nothing to do.")
        return

    # Create API session for escrow checks
    session = create_hyperliquid_session(api_url=api_url)

    # Run sponsored activation
    print(f"\n{'=' * 60}")
    print(f"Trial: sponsored activation on {network}")
    print(f"Deployer:   {deployer.address}")
    print(f"Target:     {target_address}")
    print(f"Amount:     {activation_human} USDC ({activation_raw} raw)")
    print(f"Timeout:    {activation_timeout}s")
    print(f"{'=' * 60}\n")

    try:
        activate_account_sponsored(
            web3=web3,
            target_address=target_address,
            deployer=deployer,
            usdc_address=usdc_address,
            session=session,
            activation_amount=activation_raw,
            timeout=activation_timeout,
            poll_interval=2.0,
        )
        print(f"\nSUCCESS: Account {target_address} activated on HyperCore ({network})!")
    except TimeoutError as e:
        print(f"\nFAILED: {e}")
        print("\nThe sponsored depositFor did not activate the account.")
        print("This confirms the testnet bug affects the recipient regardless of caller.")

    # Final status check
    final_activated = is_account_activated(web3, target_address)
    print(f"\nFinal coreUserExists check: {final_activated}")

    # Print the target address for use in trial 2
    print(f"\nTarget address for trial 2: TARGET_ADDRESS={target_address}")


if __name__ == "__main__":
    main()
