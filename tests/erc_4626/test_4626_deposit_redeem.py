import os
from decimal import Decimal
from typing import cast
import pytest
from web3 import Web3
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil, mine
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details, TokenDetails, USDC_WHALE
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_typing import HexAddress

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

CI = os.environ.get("CI", None) is not None

pytestmark = pytest.mark.skipif(not JSON_RPC_BASE, reason="No JSON_RPC_BASE environment variable")


@pytest.fixture()
def anvil_base_fork(request) -> AnvilLaunch:
    """Create a testable fork of live BNB chain.

    :return: JSON-RPC URL for Web3
    """
    assert JSON_RPC_BASE, "JSON_RPC_BASE not set"
    launch = fork_network_anvil(
        JSON_RPC_BASE,
        fork_block_number=35_094_246,
        unlocked_addresses=[USDC_WHALE[8453]],
    )
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture()
def web3(anvil_base_fork) -> Web3:
    """Create a web3 connector.

    - By default use Anvil forked Base

    - Eanble Tenderly testnet with `JSON_RPC_TENDERLY` to debug
      otherwise impossible to debug Gnosis Safe transactions
    """

    tenderly_fork_rpc = os.environ.get("JSON_RPC_TENDERLY", None)

    if tenderly_fork_rpc:
        web3 = create_multi_provider_web3(tenderly_fork_rpc)
    else:
        web3 = create_multi_provider_web3(
            anvil_base_fork.json_rpc_url,
            retries=0,
        )
    assert web3.eth.chain_id == 8453
    return web3


@pytest.fixture()
def usdc(web3) -> TokenDetails:
    return fetch_erc20_details(
        web3,
        "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    )


@pytest.fixture()
def vault(web3) -> ERC4626Vault:
    """Harvest USDC Autopilot on IPOR on Base"""
    # https://app.ipor.io/fusion/base/0x0d877dc7c8fa3ad980dfdb18b48ec9f8768359c4
    # (ChainId.base, "0x0d877Dc7C8Fa3aD980DfDb18B48eC9F8768359C4".lower()),
    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x0d877Dc7C8Fa3aD980DfDb18B48eC9F8768359C4",
    )
    return cast(ERC4626Vault, vault)


@pytest.fixture()
def test_user(web3, usdc):
    account = web3.eth.accounts[0]
    tx_hash = usdc.transfer(account, Decimal(10_000)).transact({"from": USDC_WHALE[web3.eth.chain_id]})
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert web3.eth.get_balance(account) > 10**18
    return account


def test_erc_4626_deposit(
    vault: ERC4626Vault,
    test_user: HexAddress,
    usdc: TokenDetails,
):
    """Use DepositManager interface to deposit into Morpho vault"""
    deposit_manager = vault.deposit_manager
    assert isinstance(deposit_manager, ERC4626DepositManager)
    assert deposit_manager.has_synchronous_deposit()
    amount = Decimal(1_000)

    estimated = deposit_manager.estimate_deposit(test_user, amount)
    assert estimated == pytest.approx(Decimal("961.55736568"))

    tx_hash = usdc.approve(
        vault.address,
        amount,
    ).transact({"from": test_user})
    assert_transaction_success_with_explanation(vault.web3, tx_hash)
    request = deposit_manager.create_deposit_request(
        test_user,
        amount=amount,
    )
    request.broadcast()
    assert vault.share_token.fetch_balance_of(test_user) > 0


@pytest.mark.skipif(CI, reason="Flaky on CI due to RPC errors")
def test_erc_4626_redeem(
    web3: Web3,
    vault: ERC4626Vault,
    test_user: HexAddress,
    usdc: TokenDetails,
):
    """Use DepositManager interface to deposit into Morpho vault"""
    deposit_manager = vault.deposit_manager
    assert isinstance(deposit_manager, ERC4626DepositManager)
    amount = Decimal(1_000)
    tx_hash = usdc.approve(
        vault.address,
        amount,
    ).transact({"from": test_user})
    assert_transaction_success_with_explanation(vault.web3, tx_hash)
    deposit_request = deposit_manager.create_deposit_request(
        test_user,
        amount=amount,
    )
    deposit_request.broadcast()
    shares = vault.share_token.fetch_balance_of(test_user)
    assert shares > 0

    # Ipor lock
    mine(
        web3,
        increase_timestamp=3600,
    )

    estimated = deposit_manager.estimate_redeem(test_user, shares)
    assert estimated == pytest.approx(Decimal("999.999657"))

    redemption_request = deposit_manager.create_redemption_request(
        test_user,
        shares=shares,
    )
    redemption_request.broadcast()
    shares = vault.share_token.fetch_balance_of(test_user)
    assert shares == 0
