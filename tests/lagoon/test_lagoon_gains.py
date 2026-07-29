"""Lagoon deposit/withdrawal from other ERC-7540 vaults tests."""

import os
from collections.abc import Iterator
from decimal import Decimal

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.gains.testing import force_next_gains_epoch
from eth_defi.erc_4626.vault_protocol.gains.vault import GainsVault
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LagoonDeploymentParameters, deploy_automated_lagoon_vault
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.token import USDC_NATIVE_TOKEN, USDC_WHALE, TokenDetails, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
FORK_BLOCK = 375_216_652

CI = os.environ.get("CI") == "true"

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run this test"),
    pytest.mark.xdist_group("fork:arbitrum:375216652"),
]


@pytest.fixture(scope="module")
def anvil_arbitrum_fork(anvil_fork_pool: AnvilForkPool, asset_manager: HexAddress) -> AnvilLaunch:
    """Share the fixed Arbitrum fork required by the historical Gains epoch."""
    return anvil_fork_pool.get_launch(
        JSON_RPC_ARBITRUM,
        FORK_BLOCK,
        unlocked_addresses=[USDC_WHALE[42161], asset_manager],
    )


@pytest.fixture
def web3(anvil_arbitrum_fork: AnvilLaunch) -> Web3:
    """Connect to the fixed Arbitrum Gains fork."""
    return create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url)


@pytest.fixture(autouse=True)
def _evm_snapshot(anvil_arbitrum_fork: AnvilLaunch) -> Iterator[None]:
    """Restore the historical Gains fork after the lifecycle test."""
    yield from evm_snapshot_revert(anvil_arbitrum_fork)


@pytest.fixture
def topped_up_asset_manager(web3: Web3, asset_manager: HexAddress) -> HexAddress:
    """Fund the Lagoon asset manager with native gas tokens."""
    # Topped up with some ETH
    tx_hash = web3.eth.send_transaction(
        {
            "to": asset_manager,
            "from": web3.eth.accounts[0],
            "value": 9 * 10**18,
        }
    )
    assert_transaction_success_with_explanation(web3, tx_hash)
    return asset_manager


@pytest.fixture
def usdc(web3: Web3) -> TokenDetails:
    """Open native Arbitrum USDC."""
    return fetch_erc20_details(
        web3,
        USDC_NATIVE_TOKEN[42161],
    )


@pytest.fixture
def gains_vault(web3: Web3) -> GainsVault:
    """Open the gTrade USDC vault on Arbitrum."""
    vault_address = "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0"
    vault = create_vault_instance_autodetect(web3, vault_address)
    assert isinstance(vault, GainsVault)
    return vault


@pytest.fixture
def new_depositor(web3: Web3, usdc: TokenDetails) -> HexAddress:
    """User with some USDC ready to deposit.

    - Start with 500 USDC
    """
    new_depositor = web3.eth.accounts[5]
    usdc_holder = USDC_WHALE[42161]
    tx_hash = usdc.transfer(new_depositor, Decimal(500)).transact({"from": usdc_holder, "gas": 100_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return new_depositor


@pytest.mark.skipif(CI, reason="Too Flaky on CI because of RPC issues with Anvil")
def test_lagoon_gains(
    web3: Web3,
    usdc: TokenDetails,
    topped_up_asset_manager: HexAddress,
    gains_vault: GainsVault,
    deployer_hot_wallet: HotWallet,
    multisig_owners: list[HexAddress],
    new_depositor: HexAddress,
    asset_manager: HexAddress,
) -> None:
    """Exercise a Gains deposit and redemption owned by a Lagoon Safe.

    1. Deploy a Lagoon vault whose GuardV0 allows the Gains vault.
    2. Deposit USDC into Lagoon and settle its deposit queue.
    3. Deposit Lagoon's USDC into Gains through its strategy module.
    4. Request redemption through the same guarded module.
    5. Advance Gains epochs until the request is claimable.
    6. Claim through the module and analyse the canonical Gains event.
    """

    # 1. Deploy new Lagoon vault where the target vault is whitelisted on the guard
    chain_id = web3.eth.chain_id
    asset_manager = topped_up_asset_manager
    assert asset_manager.startswith("0x")
    depositor = new_depositor
    target_vault = gains_vault

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
        uniswap_v2=None,
        uniswap_v3=None,
        any_asset=False,
        erc_4626_vaults=[target_vault],
        from_the_scratch=True,
        use_forge=True,
    )

    # 2. Deposit USDC into Lagoon and settle its deposit queue.
    vault = deploy_info.vault
    our_address = vault.safe_address
    assert not vault.trading_strategy_module.functions.anyAsset().call()

    # We need to do the initial valuation at value 0
    bound_func = vault.post_new_valuation(Decimal(0))
    tx_hash = bound_func.transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Deposit 9.00 USDC into the vault
    lagoon_deposit_amount = Decimal(9)
    raw_lagoon_deposit_amount = usdc.convert_to_raw(lagoon_deposit_amount)
    tx_hash = usdc.approve(vault.address, lagoon_deposit_amount).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)
    deposit_func = vault.request_deposit(depositor, raw_lagoon_deposit_amount)
    tx_hash = deposit_func.transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # We need to do the initial valuation at value 0
    valuation = Decimal(0)
    bound_func = vault.post_new_valuation(valuation)
    tx_hash = bound_func.transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Settle deposit queue 9 USDC -> 0 USDC
    settle_func = vault.settle_via_trading_strategy_module(valuation)
    tx_hash = settle_func.transact(
        {
            "from": asset_manager,
            "gas": 1_000_000,
        }
    )
    assert_transaction_success_with_explanation(
        web3,
        tx_hash,
        func=settle_func,
        tracing=True,
    )

    # 3. Deposit Lagoon's USDC into Gains through its strategy module.
    deposit_manager = target_vault.deposit_manager

    assert deposit_manager.can_create_deposit_request(our_address)

    # Request deposit to the target vault from our vault
    deposit_request = deposit_manager.create_deposit_request(our_address, amount=lagoon_deposit_amount)
    fn_calls = [
        usdc.approve(target_vault.vault_address, lagoon_deposit_amount),
        deposit_request.funcs[0],
    ]
    for fn_call in fn_calls:
        moduled_tx = vault.transact_via_trading_strategy_module(fn_call)
        tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash, func=fn_call)

    # We got our shares
    share_token = target_vault.share_token
    share_amount = share_token.fetch_balance_of(our_address)

    # 4. Request redemption through the same guarded module.
    # Clear the current epoch before opening the request.
    force_next_gains_epoch(
        target_vault,
        asset_manager,
    )

    assert deposit_manager.can_create_redemption_request(our_address)

    redeem_request = deposit_manager.create_redemption_request(
        our_address,
        shares=share_amount,
    )
    fn_calls = [
        share_token.approve(target_vault.vault_address, share_amount),
        redeem_request.funcs[0],
    ]
    for fn_call in fn_calls:
        moduled_tx = vault.transact_via_trading_strategy_module(fn_call)
        tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash, func=fn_call)

    redemption_ticket = redeem_request.parse_redeem_transaction([tx_hash])

    # Cannot redeem yet, need to wait for the next epoch
    assert deposit_manager.can_finish_redeem(redemption_ticket) is False

    # 5. Advance Gains epochs until the request is claimable.
    for _ in range(3):
        force_next_gains_epoch(
            target_vault,
            asset_manager,
        )

    assert target_vault.fetch_current_epoch() >= 200

    assert deposit_manager.can_finish_redeem(redemption_ticket) is True

    # 6. Claim through the module and analyse the canonical Gains event.
    fn_calls = [deposit_manager.finish_redemption(redemption_ticket)]
    for fn_call in fn_calls:
        moduled_tx = vault.transact_via_trading_strategy_module(fn_call)
        tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash, func=fn_call)

    # The outer target is Lagoon's module, while the Gains event identifies the
    # Safe owner and receiver. Analysis must not confuse these addresses.
    assert web3.eth.get_transaction(tx_hash)["to"] == vault.trading_strategy_module.address
    assert vault.trading_strategy_module.address != redemption_ticket.owner
    redemption_analysis = deposit_manager.analyse_redemption(tx_hash, redemption_ticket)
    assert redemption_analysis.from_ == redemption_ticket.owner
    assert redemption_analysis.to == redemption_ticket.to
    assert share_amount == Decimal("7.338782")
    assert redemption_analysis.share_count == Decimal("7.338782")
    assert redemption_analysis.denomination_amount == Decimal("8.999999")
