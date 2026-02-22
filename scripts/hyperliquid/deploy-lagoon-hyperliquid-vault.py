"""Deploy a Lagoon vault on HyperEVM and exercise Hypercore deposit/withdrawal.

Runs against a local Anvil fork of HyperEVM mainnet.

Prerequisites
-------------
- Anvil running as HyperEVM fork::

    anvil --fork-url $JSON_RPC_HYPERLIQUID --gas-limit 30000000

- OR: HyperEVM testnet account with funds

Account funding (testnet)
-------------------------
- **HYPE for gas**: Use the Hyperliquid testnet faucet at
  https://app.hyperliquid-testnet.xyz/drip or bridge from the testnet L1.
  The deployer account needs HYPE on HyperEVM for transaction gas.
- **USDC for deposits**: Bridge USDC from Hyperliquid testnet L1 to HyperEVM
  via the CoreDepositWallet, or use the testnet USDC faucet.
  The Safe needs USDC for vault deposits.
- On Anvil forks, both balances are set automatically via ``anvil_setBalance``
  and ``anvil_setStorageAt``.

Environment variables
---------------------
- ``JSON_RPC_HYPERLIQUID``: HyperEVM RPC URL (required)
- ``HYPERCORE_WRITER_TEST_PRIVATE_KEY``: Deployer private key
  (defaults to Anvil account #0: ``0xac0974...``)
- ``HYPERCORE_VAULT``: Vault address to deposit into
  (defaults to a test address ``0x1111...1111``)
- ``USDC_AMOUNT``: USDC amount in human units (default: ``10000``)
- ``LOG_LEVEL``: Logging level (default: ``info``)

Usage::

    # Anvil fork (default)
    source .local-test.env && poetry run python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py

    # Custom vault and amount
    HYPERCORE_VAULT=0xabc... USDC_AMOUNT=5000 poetry run python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py
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


def _load_deployed_bytecode(abi_filename: str) -> str:
    """Load deployed bytecode from an ABI JSON file."""
    abi_data = get_abi_by_filename(abi_filename)
    bytecode = abi_data["deployedBytecode"]["object"]
    if not bytecode.startswith("0x"):
        bytecode = "0x" + bytecode
    return bytecode


def main():
    log_level = os.environ.get("LOG_LEVEL", "info")
    setup_console_logging(default_log_level=log_level)

    json_rpc = os.environ.get("JSON_RPC_HYPERLIQUID")
    assert json_rpc, "JSON_RPC_HYPERLIQUID environment variable required"

    private_key = os.environ.get("HYPERCORE_WRITER_TEST_PRIVATE_KEY", ANVIL_DEFAULT_KEY)
    vault_address = HexAddress(HexStr(os.environ.get("HYPERCORE_VAULT", "0x1111111111111111111111111111111111111111")))
    usdc_human = int(os.environ.get("USDC_AMOUNT", "10000"))

    web3 = create_multi_provider_web3(json_rpc, default_http_timeout=(3, 500.0))
    chain_id = web3.eth.chain_id
    logger.info("Connected to chain %d", chain_id)

    deployer_account = Account.from_key(private_key)
    deployer = HotWallet(deployer_account)
    deployer.sync_nonce(web3)
    logger.info("Deployer: %s", deployer.address)

    usdc_address = USDC_NATIVE_TOKEN[chain_id]
    usdc = fetch_erc20_details(web3, usdc_address)
    usdc_amount = usdc_human * 10**usdc.decimals
    hypercore_amount = usdc_human * 10**6  # HyperCore uses 6 decimals

    # Deploy mock contracts at system addresses (Anvil fork only)
    is_anvil = _setup_anvil_mocks(web3)

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

    # Set up Hypercore whitelisting (impersonate Safe as owner)
    web3.provider.make_request("anvil_impersonateAccount", [safe_address])
    web3.provider.make_request("anvil_setBalance", [safe_address, hex(10 * 10**18)])

    tx_hash = module.functions.whitelistCoreWriter(
        Web3.to_checksum_address(CORE_WRITER_ADDRESS),
        Web3.to_checksum_address(CORE_DEPOSIT_WALLET_MAINNET),
        "Hypercore vault trading",
    ).transact({"from": safe_address})
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = module.functions.whitelistHypercoreVault(
        Web3.to_checksum_address(vault_address),
        f"Hypercore vault: {vault_address}",
    ).transact({"from": safe_address})
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = module.functions.whitelistToken(
        Web3.to_checksum_address(usdc_address),
        "USDC for Hypercore bridging",
    ).transact({"from": safe_address})
    assert_transaction_success_with_explanation(web3, tx_hash)

    web3.provider.make_request("anvil_stopImpersonatingAccount", [safe_address])
    logger.info("Hypercore whitelisting complete")

    # Fund Safe with USDC (Anvil only)
    if is_anvil:
        _fund_safe_usdc(web3, safe_address, usdc_address, usdc_amount)

    balance = usdc.contract.functions.balanceOf(safe_address).call()
    logger.info("Safe USDC balance: %s", balance / 10**usdc.decimals)
    assert balance >= usdc_amount, f"Safe USDC balance {balance} insufficient, need {usdc_amount}"

    # Deposit via multicall
    logger.info("Executing multicall deposit (%d USDC)...", usdc_human)
    cdw = get_core_deposit_wallet_contract(web3, CORE_DEPOSIT_WALLET_MAINNET)
    core_writer = web3.eth.contract(
        address=Web3.to_checksum_address(CORE_WRITER_ADDRESS),
        abi=get_abi_by_filename("guard/MockCoreWriter.json")["abi"],
    )

    fn = build_hypercore_deposit_multicall(
        module=module,
        usdc_contract=usdc.contract,
        core_deposit_wallet=cdw,
        core_writer=core_writer,
        evm_usdc_amount=usdc_amount,
        hypercore_usdc_amount=hypercore_amount,
        vault_address=vault_address,
    )
    tx_hash = fn.transact({"from": deployer_account.address})
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

    # Withdraw via multicall
    logger.info("Executing multicall withdrawal (%d USDC)...", usdc_human)
    fn = build_hypercore_withdraw_multicall(
        module=module,
        core_writer=core_writer,
        hypercore_usdc_amount=hypercore_amount,
        vault_address=vault_address,
        safe_address=safe_address,
    )
    try:
        tx_hash = fn.transact({"from": deployer_account.address})
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

    # Summary
    final_balance = usdc.contract.functions.balanceOf(safe_address).call()
    summary = [
        ["Vault", deploy_info.vault.address],
        ["Safe", safe_address],
        ["Module", module.address],
        ["Chain ID", chain_id],
        ["Initial USDC", f"{usdc_human:,}"],
        ["Final USDC balance", f"{final_balance / 10**usdc.decimals:,.2f}"],
    ]
    print("\nSummary:")
    print(tabulate(summary, tablefmt="simple"))


def _setup_anvil_mocks(web3: Web3) -> bool:
    """Deploy mock CoreWriter and CoreDepositWallet on Anvil forks.

    :return:
        True if running on Anvil, False otherwise.
    """
    try:
        web3.provider.make_request("anvil_nodeInfo", [])
    except Exception:
        return False

    # MockCoreWriter
    cw_bytecode = _load_deployed_bytecode("guard/MockCoreWriter.json")
    cw_address = Web3.to_checksum_address(CORE_WRITER_ADDRESS)
    web3.provider.make_request("anvil_setCode", [cw_address, cw_bytecode])
    web3.provider.make_request(
        "anvil_setStorageAt",
        [cw_address, "0x" + "0" * 64, "0x" + "0" * 64],
    )
    logger.info("MockCoreWriter deployed at %s", cw_address)

    # MockCoreDepositWallet
    cdw_bytecode = _load_deployed_bytecode("guard/MockCoreDepositWallet.json")
    cdw_address = Web3.to_checksum_address(CORE_DEPOSIT_WALLET_MAINNET)
    web3.provider.make_request("anvil_setCode", [cdw_address, cdw_bytecode])
    web3.provider.make_request(
        "anvil_setStorageAt",
        [cdw_address, "0x" + "0" * 64, "0x" + "0" * 64],
    )
    logger.info("MockCoreDepositWallet deployed at %s", cdw_address)

    # Fund deployer
    deployer = web3.eth.accounts[0]
    web3.provider.make_request("anvil_setBalance", [deployer, hex(100 * 10**18)])
    return True


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


if __name__ == "__main__":
    main()
