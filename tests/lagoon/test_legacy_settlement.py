"""Legacy Lagoon settlement on a separate, canonical pooled Base fork."""

import os
from decimal import Decimal

import flaky
import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LagoonDeploymentParameters, deploy_automated_lagoon_vault
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVersion
from eth_defi.hotwallet import HotWallet
from eth_defi.testing.fork_blocks import BASE_MIDNIGHT_BLOCK
from eth_defi.token import USDC_NATIVE_TOKEN, TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment

_FORK_ISOLATION_GROUP = "fork:base:lagoon-legacy-settlement"
pytestmark = [
    pytest.mark.slow,
    pytest.mark.xdist_group(_FORK_ISOLATION_GROUP),
    pytest.mark.skipif(not os.environ.get("JSON_RPC_BASE"), reason="JSON_RPC_BASE needed"),
]


@pytest.fixture(scope="module")
def test_block_number() -> int:
    """Select the shared fixed Base block and its warm RPC cache.

    Keep this legacy deployment independent of other Lagoon module state.

    :return: Canonical Base fork height.
    """
    return BASE_MIDNIGHT_BLOCK


@pytest.fixture(scope="module")
def anvil_fork_isolation_group() -> str:
    """Give legacy settlement its own pooled process.

    Reuse that process for any retry without inheriting earlier deployment load.

    :return: Pool partition matching this module's xdist group.
    """
    return _FORK_ISOLATION_GROUP


@pytest.fixture()
def depositor(web3: Web3, base_usdc: TokenDetails, usdc_holder: HexAddress) -> HexAddress:
    """Fund an unlocked depositor account on the fork.

    Use the shared USDC whale without depending on an existing deployed vault.

    :param web3: Connection to the pooled Base fork.
    :param base_usdc: USDC details for the deposit.
    :param usdc_holder: Unlocked USDC whale on the fork.
    :return: Funded depositor address.
    """
    address = web3.eth.accounts[5]
    tx_hash = base_usdc.transfer(address, Decimal(999)).transact({"from": usdc_holder, "gas": 100_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return address


# On 2026-10-26 CI stalled after 254 seconds on a reused deployment fork and
# then hung in teardown. All four original lifecycles passed locally in 9.86s.
@flaky.flaky
def test_lagoon_settle_v_0_1_0(
    web3: Web3,
    uniswap_v2: UniswapV2Deployment,
    base_usdc: TokenDetails,
    topped_up_asset_manager: HexAddress,
    depositor: HexAddress,
    deployer_hot_wallet: HotWallet,
    multisig_owners: list[str],
) -> None:
    """Deploy and settle the legacy vault on an isolated fixed Base fork.

    A preceding deployment must not leave this lifecycle using an Anvil process
    degraded by repeated snapshot/revert cycles. The pool retains the canonical
    block and its warm RPC cache while separating this legacy deployment.

    :param web3: Connection to the pooled Base fork.
    :param uniswap_v2: Base Uniswap V2 deployment.
    :param base_usdc: Deposit token details.
    :param topped_up_asset_manager: Funded valuation and settlement manager.
    :param depositor: Account funded with USDC for the real deposit.
    :param deployer_hot_wallet: Wallet deploying the Safe and vault.
    :param multisig_owners: Safe owners for the deployed vault.
    :return: None.
    """

    chain_id = web3.eth.chain_id
    asset_manager = topped_up_asset_manager
    usdc = base_usdc

    parameters = LagoonDeploymentParameters(
        underlying=USDC_NATIVE_TOKEN[chain_id],
        name="Example",
        symbol="EXA",
    )

    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=deployer_hot_wallet,
        asset_manager=asset_manager,
        parameters=parameters,
        safe_owners=multisig_owners,
        safe_threshold=2,
        uniswap_v2=uniswap_v2,
        uniswap_v3=None,
        any_asset=True,
        vault_abi="lagoon/Vault.json",  # Lagoon v0.1
        factory_contract=False,  # Lagoon v0.1
    )

    # We look correctly initialised, and
    # Safe it set to take the ownership
    vault = deploy_info.vault
    assert deploy_info.chain_id == 8453
    assert vault.version == LagoonVersion.legacy

    # We need to do the initial valuation at value 0
    bound_func = vault.post_new_valuation(Decimal(0))
    tx_hash = bound_func.transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Deposit 9.00 USDC into the vault
    usdc_amount = usdc.convert_to_raw(Decimal(9))
    tx_hash = usdc.contract.functions.approve(vault.address, usdc_amount).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)
    deposit_func = vault.request_deposit(depositor, usdc_amount)
    tx_hash = deposit_func.transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Deposit was registered
    receipt = web3.eth.get_transaction_receipt(tx_hash)
    # TODO: Why ABI signature mismatch
    assert len(receipt["logs"]) == 2  # Transfer + Deposit

    # We need to do the initial valuation at value 0
    valuation = Decimal(0)

    # Settle deposit queue 9 USDC -> 0 USDC
    settle_func = vault.settle_via_trading_strategy_module(valuation, abi_version="v0.1.0")
    tx_hash = settle_func.transact(
        {
            "from": asset_manager,
            "gas": 1_000_000,
        }
    )
    assert_transaction_success_with_explanation(web3, tx_hash)
