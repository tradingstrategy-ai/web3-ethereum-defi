"""Deploy a Lagoon vault on HyperEVM and exercise Hypercore deposit/withdrawal.

A quick script to test/simulate Lagoon vault deployment on HyperEVM,
with Hypercore deposit and withdrawal flows via multicall.

.. note ::

    THIS SCRIPT IS ONLY FOR TESTING PURPOSES. The Safe multisig
    is configured with random owners and cannot be used for production.

In ``SIMULATE`` mode the script forks HyperEVM mainnet via Anvil and deploys
mock CoreWriter/CoreDepositWallet contracts so no real funds are needed.
Without ``SIMULATE`` the script connects to the live network (mainnet or
testnet) and requires a funded deployer key.

Account funding for Hyperliquid testnet
---------------------------------------

1. Create a new private key and set ``PRIVATE_KEY`` env
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
- ``JSON_RPC_HYPERLIQUID``: HyperEVM RPC URL (required).
  For testnet use ``https://api.hyperliquid-testnet.xyz/evm``.
- ``PRIVATE_KEY``: Deployer private key (required on live network;
  defaults to Anvil account #0 in SIMULATE mode)
- ``SIMULATE``: Set to any value to fork via Anvil (default: unset)
- ``ACTION``: ``deposit``, ``withdraw``, or ``both`` (default: ``both``).
  On testnet you may need to wait 1 day between deposit and withdrawal
  due to the vault lock-up period, so run deposit first, then withdraw
  later.
- ``HYPERCORE_VAULT``: Hypercore vault address to deposit into.
  Defaults to ``0x1111111111111111111111111111111111111111`` (dummy address,
  fine for SIMULATE mode). Must be set to a real vault for live testnet.
- ``USDC_AMOUNT``: USDC amount in human units (default: ``1``)
- ``LOG_LEVEL``: Logging level (default: ``info``)

Usage::

    # Simulate on Anvil fork (no real funds needed)
    source .local-test.env && SIMULATE=true poetry run python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py

    # Testnet deposit only (wait 1 day before withdrawal)
    PRIVATE_KEY=0x... JSON_RPC_HYPERLIQUID="https://api.hyperliquid-testnet.xyz/evm" \\
        ACTION=deposit HYPERCORE_VAULT=0xabc... USDC_AMOUNT=5 \\
        poetry run python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py

    # Testnet withdrawal (after lock-up period)
    PRIVATE_KEY=0x... JSON_RPC_HYPERLIQUID="https://api.hyperliquid-testnet.xyz/evm" \\
        ACTION=withdraw HYPERCORE_VAULT=0xabc... USDC_AMOUNT=5 \\
        poetry run python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py
"""

import logging
import os

from eth_account import Account
from eth_typing import HexAddress, HexStr
from tabulate import tabulate
from web3 import Web3

from eth_defi.abi import get_abi_by_filename
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    LagoonConfig,
    LagoonDeploymentParameters,
    deploy_automated_lagoon_vault,
)
from eth_defi.hotwallet import HotWallet
from eth_defi.hyperliquid.core_writer import (
    CORE_DEPOSIT_WALLET_MAINNET,
    CORE_WRITER_ADDRESS,
    build_hypercore_deposit_multicall,
    build_hypercore_withdraw_multicall,
    get_core_deposit_wallet_contract,
)
from eth_defi.hyperliquid.guard_whitelist import get_core_deposit_wallet
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN, fetch_erc20_details
from eth_defi.trace import (
    TransactionAssertionError,
    assert_transaction_success_with_explanation,
)
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)

#: Anvil default account #0 private key
ANVIL_DEFAULT_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

#: Anvil default accounts #1 and #2 as Safe owners
OWNER_1 = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
OWNER_2 = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"

#: Default Hypercore vault address (dummy, only works in SIMULATE mode)
DEFAULT_VAULT = "0x1111111111111111111111111111111111111111"


def _load_deployed_bytecode(abi_filename: str) -> str:
    """Load deployed bytecode from an ABI JSON file."""
    abi_data = get_abi_by_filename(abi_filename)
    bytecode = abi_data["deployedBytecode"]["object"]
    if not bytecode.startswith("0x"):
        bytecode = "0x" + bytecode
    return bytecode


def _setup_anvil_mocks(web3: Web3) -> None:
    """Deploy mock CoreWriter and CoreDepositWallet on Anvil forks."""
    # MockCoreWriter at the system address
    cw_bytecode = _load_deployed_bytecode("guard/MockCoreWriter.json")
    cw_address = Web3.to_checksum_address(CORE_WRITER_ADDRESS)
    web3.provider.make_request("anvil_setCode", [cw_address, cw_bytecode])
    web3.provider.make_request(
        "anvil_setStorageAt",
        [cw_address, "0x" + "0" * 64, "0x" + "0" * 64],
    )
    logger.info("MockCoreWriter deployed at %s", cw_address)

    # MockCoreDepositWallet at the mainnet address
    cdw_bytecode = _load_deployed_bytecode("guard/MockCoreDepositWallet.json")
    cdw_address = Web3.to_checksum_address(CORE_DEPOSIT_WALLET_MAINNET)
    web3.provider.make_request("anvil_setCode", [cdw_address, cdw_bytecode])
    web3.provider.make_request(
        "anvil_setStorageAt",
        [cdw_address, "0x" + "0" * 64, "0x" + "0" * 64],
    )
    logger.info("MockCoreDepositWallet deployed at %s", cdw_address)

    # Fund deployer with HYPE for gas
    deployer = web3.eth.accounts[0]
    web3.provider.make_request("anvil_setBalance", [deployer, hex(100 * 10**18)])


def _fund_safe_usdc(web3: Web3, safe_address: str, usdc_address: str, amount: int):
    """Fund Safe with USDC by directly setting storage on Anvil."""
    web3.provider.make_request(
        "anvil_setStorageAt",
        [
            Web3.to_checksum_address(usdc_address),
            "0x"
            + Web3.solidity_keccak(
                ["uint256", "uint256"],
                [int(safe_address, 16), 9],
            ).hex(),
            "0x" + amount.to_bytes(32, "big").hex(),
        ],
    )
    logger.info("Funded Safe %s with %d USDC (wei)", safe_address, amount)


def _do_deposit(
    web3: Web3,
    module,
    usdc,
    cdw,
    core_writer,
    usdc_amount: int,
    hypercore_amount: int,
    vault_address: str,
    deployer_address: str,
    usdc_human: int,
):
    """Execute deposit via multicall."""
    logger.info("Executing multicall deposit (%d USDC)...", usdc_human)
    fn = build_hypercore_deposit_multicall(
        module=module,
        usdc_contract=usdc.contract,
        core_deposit_wallet=cdw,
        core_writer=core_writer,
        evm_usdc_amount=usdc_amount,
        hypercore_usdc_amount=hypercore_amount,
        vault_address=vault_address,
    )
    tx_hash = fn.transact({"from": deployer_address})
    receipt = assert_transaction_success_with_explanation(web3, tx_hash)

    deposit_results = [
        ["Transaction", tx_hash.hex()],
        ["Gas used", receipt["gasUsed"]],
        ["Block", receipt["blockNumber"]],
        ["USDC amount", f"{usdc_human:,}"],
        ["Vault", vault_address],
    ]
    print("\nDeposit results:")
    print(tabulate(deposit_results, tablefmt="simple"))


def _do_withdraw(
    web3: Web3,
    module,
    core_writer,
    hypercore_amount: int,
    vault_address: str,
    safe_address: str,
    deployer_address: str,
    usdc_human: int,
):
    """Execute withdrawal via multicall."""
    logger.info("Executing multicall withdrawal (%d USDC)...", usdc_human)
    fn = build_hypercore_withdraw_multicall(
        module=module,
        core_writer=core_writer,
        hypercore_usdc_amount=hypercore_amount,
        vault_address=vault_address,
        safe_address=safe_address,
    )
    try:
        tx_hash = fn.transact({"from": deployer_address})
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


def main():
    log_level = os.environ.get("LOG_LEVEL", "info")
    setup_console_logging(default_log_level=log_level)

    json_rpc = os.environ.get("JSON_RPC_HYPERLIQUID")
    assert json_rpc, "JSON_RPC_HYPERLIQUID environment variable required"

    simulate = os.environ.get("SIMULATE")
    action = os.environ.get("ACTION", "both").lower()
    assert action in ("deposit", "withdraw", "both"), f"ACTION must be 'deposit', 'withdraw', or 'both', got '{action}'"

    private_key = os.environ.get("PRIVATE_KEY", ANVIL_DEFAULT_KEY if simulate else None)
    assert private_key, "PRIVATE_KEY environment variable required (or set SIMULATE=true)"

    vault_address = HexAddress(HexStr(os.environ.get("HYPERCORE_VAULT", DEFAULT_VAULT)))
    usdc_human = int(os.environ.get("USDC_AMOUNT", "1"))

    # Connect to network
    anvil = None
    if simulate:
        logger.info("SIMULATE mode: forking HyperEVM via Anvil")
        anvil = fork_network_anvil(
            json_rpc,
            gas_limit=30_000_000,
        )
        web3 = create_multi_provider_web3(anvil.json_rpc_url, default_http_timeout=(3, 500.0))
    else:
        logger.info("Live network mode")
        web3 = create_multi_provider_web3(json_rpc, default_http_timeout=(3, 500.0))

    chain_id = web3.eth.chain_id
    logger.info("Connected to chain %d, block %d", chain_id, web3.eth.block_number)

    deployer_account = Account.from_key(private_key)
    deployer = HotWallet(deployer_account)
    deployer.sync_nonce(web3)
    logger.info("Deployer: %s", deployer.address)

    usdc_address = USDC_NATIVE_TOKEN[chain_id]
    usdc = fetch_erc20_details(web3, usdc_address)
    usdc_amount = usdc_human * 10**usdc.decimals
    hypercore_amount = usdc_human * 10**6  # HyperCore uses 6 decimals

    # Deploy mock contracts at system addresses (Anvil fork only)
    if simulate:
        _setup_anvil_mocks(web3)

    # Deploy Lagoon vault
    logger.info("Deploying Lagoon vault...")
    config = LagoonConfig(
        parameters=LagoonDeploymentParameters(
            underlying=usdc_address,
            name="HyperEVM Hypercore Manual Test",
            symbol="HHMT",
        ),
        asset_manager=deployer_account.address,
        safe_owners=[OWNER_1, OWNER_2],
        safe_threshold=2,
        any_asset=True,
        safe_salt_nonce=99,
    )

    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=deployer,
        config=config,
    )

    module = deploy_info.trading_strategy_module
    safe_address = deploy_info.safe.address

    logger.info("Vault:  %s", deploy_info.vault.address)
    logger.info("Safe:   %s", safe_address)
    logger.info("Module: %s", module.address)

    # Set up Hypercore whitelisting
    # In SIMULATE mode we impersonate the Safe; on live network the deployer
    # is a Safe owner (added automatically during deployment)
    if simulate:
        web3.provider.make_request("anvil_impersonateAccount", [safe_address])
        web3.provider.make_request("anvil_setBalance", [safe_address, hex(10 * 10**18)])
        whitelisting_sender = safe_address
    else:
        whitelisting_sender = deployer_account.address

    cdw_address = get_core_deposit_wallet(chain_id)

    tx_hash = module.functions.whitelistCoreWriter(
        Web3.to_checksum_address(CORE_WRITER_ADDRESS),
        Web3.to_checksum_address(cdw_address),
        "Hypercore vault trading",
    ).transact({"from": whitelisting_sender})
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = module.functions.whitelistHypercoreVault(
        Web3.to_checksum_address(vault_address),
        f"Hypercore vault: {vault_address}",
    ).transact({"from": whitelisting_sender})
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = module.functions.whitelistToken(
        Web3.to_checksum_address(usdc_address),
        "USDC for Hypercore bridging",
    ).transact({"from": whitelisting_sender})
    assert_transaction_success_with_explanation(web3, tx_hash)

    if simulate:
        web3.provider.make_request("anvil_stopImpersonatingAccount", [safe_address])

    logger.info("Hypercore whitelisting complete")

    # Fund Safe with USDC (Anvil only)
    if simulate:
        _fund_safe_usdc(web3, safe_address, usdc_address, usdc_amount)

    balance = usdc.contract.functions.balanceOf(safe_address).call()
    logger.info("Safe USDC balance: %s", balance / 10**usdc.decimals)

    # Prepare contract instances
    cdw = get_core_deposit_wallet_contract(web3, cdw_address)
    core_writer = web3.eth.contract(
        address=Web3.to_checksum_address(CORE_WRITER_ADDRESS),
        abi=get_abi_by_filename("guard/MockCoreWriter.json")["abi"],
    )

    # Execute actions
    if action in ("deposit", "both"):
        assert balance >= usdc_amount, f"Safe USDC balance {balance} insufficient, need {usdc_amount}"
        _do_deposit(
            web3,
            module,
            usdc,
            cdw,
            core_writer,
            usdc_amount,
            hypercore_amount,
            vault_address,
            deployer_account.address,
            usdc_human,
        )

    if action in ("withdraw", "both"):
        _do_withdraw(
            web3,
            module,
            core_writer,
            hypercore_amount,
            vault_address,
            safe_address,
            deployer_account.address,
            usdc_human,
        )

    # Summary
    final_balance = usdc.contract.functions.balanceOf(safe_address).call()
    summary = [
        ["Vault", deploy_info.vault.address],
        ["Safe", safe_address],
        ["Module", module.address],
        ["Chain ID", chain_id],
        ["Action", action],
        ["USDC amount", f"{usdc_human:,}"],
        ["Final USDC balance", f"{final_balance / 10**usdc.decimals:,.2f}"],
        ["Simulate", "yes" if simulate else "no"],
    ]
    print("\nSummary:")
    print(tabulate(summary, tablefmt="simple"))

    if anvil:
        anvil.close()
        logger.info("Anvil stopped")


if __name__ == "__main__":
    main()
