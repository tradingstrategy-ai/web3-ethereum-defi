"""Test 40acres vault metadata.

40acres is a cashflow lending protocol for veNFT collateral
with ERC-4626 USDC supply vaults on Avalanche, Base, and Optimism.
"""

import os
from collections.abc import Iterator
from decimal import Decimal

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.erc_4626.vault_protocol.forty_acres.deposit_redeem import FORTY_ACRES_INSUFFICIENT_LIQUIDITY_ERROR, PHARAOH_USDC_AVALANCHE_ADDRESS, FortyAcresDepositManager
from eth_defi.erc_4626.vault_protocol.forty_acres.vault import FortyAcresVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil, fund_erc20_on_anvil, set_balance
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.testing.fork_blocks import AVALANCHE_MIDNIGHT_BLOCK, BASE_MIDNIGHT_BLOCK
from eth_defi.token import USDC_WHALE
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable

JSON_RPC_AVALANCHE = os.environ.get("JSON_RPC_AVALANCHE")
JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

#: Exact 40acres Aerodrome deployment whose live redemption proves that the
#: Pharaoh direct-balance rule is not protocol-wide.
AERODROME_USDC_VAULT = "0xb99b6df96d4d5448cc0a5b3e0ef7896df9507cf5"


@pytest.fixture(scope="module")
def anvil_avalanche_fork() -> AnvilLaunch:
    """Fork Avalanche at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_AVALANCHE, fork_block_number=84244698)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_avalanche_fork: AnvilLaunch):
    web3 = create_multi_provider_web3(anvil_avalanche_fork.json_rpc_url, retries=2)
    return web3


@pytest.fixture(scope="module")
def pharaoh_avalanche_fork(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Share the canonical Avalanche fork for Pharaoh redemption capacity."""
    return anvil_fork_pool.get_launch(
        JSON_RPC_AVALANCHE,
        AVALANCHE_MIDNIGHT_BLOCK,
        unlocked_addresses=[PHARAOH_USDC_AVALANCHE_ADDRESS],
    )


@pytest.fixture(scope="module")
def pharaoh_avalanche_web3(pharaoh_avalanche_fork: AnvilLaunch) -> Web3:
    """Connect to the shared Avalanche Pharaoh fork."""
    return create_multi_provider_web3(pharaoh_avalanche_fork.json_rpc_url)


@pytest.fixture
def pharaoh_avalanche_snapshot(pharaoh_avalanche_fork: AnvilLaunch) -> Iterator[None]:
    """Restore the mutating Pharaoh fork after the liquidity test."""
    yield from evm_snapshot_revert(pharaoh_avalanche_fork)


@pytest.fixture(scope="module")
def aerodrome_base_fork(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Share the canonical Base fork with the USDC whale unlocked."""
    return anvil_fork_pool.get_launch(
        JSON_RPC_BASE,
        BASE_MIDNIGHT_BLOCK,
        unlocked_addresses=[USDC_WHALE[8453]],
    )


@pytest.fixture(scope="module")
def aerodrome_base_web3(aerodrome_base_fork: AnvilLaunch) -> Web3:
    """Connect to the shared Base Aerodrome fork."""
    return create_multi_provider_web3(aerodrome_base_fork.json_rpc_url)


@pytest.fixture
def aerodrome_base_snapshot(aerodrome_base_fork: AnvilLaunch) -> Iterator[None]:
    """Restore the mutating Aerodrome fork after the redemption test."""
    yield from evm_snapshot_revert(aerodrome_base_fork)


@pytest.mark.skipif(JSON_RPC_AVALANCHE is None, reason="JSON_RPC_AVALANCHE needed to run this test")
@flaky.flaky
def test_forty_acres_blackhole(
    web3: Web3,
):
    """Read 40acres Blackhole USDC vault metadata on Avalanche.

    1. Auto-detect the vault protocol from the hardcoded address
    2. Verify the vault instance type and protocol name
    3. Check the lender-facing fees are explicitly zero
    """

    # 1. Auto-detect the vault protocol
    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xc0485c4bafb594ae1457820fb6e5b67e8a04bcfd",
    )

    # 2. Verify instance type and protocol name
    assert isinstance(vault, FortyAcresVault)
    assert vault.get_protocol_name() == "40acres"
    assert ERC4626Feature.forty_acres_like in vault.features
    assert vault.is_whitelisted_deposit() is False

    # 3. Check fee methods. 40acres charges lenders no explicit management or
    # performance fee — the protocol's 5% treasury cut is taken from borrower
    # rewards, not from depositor principal or yield. So these read 0.0 ("no
    # fee"), not None, which the vault API reserves for "fee not exposed".
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0


@pytest.mark.skipif(JSON_RPC_AVALANCHE is None, reason="JSON_RPC_AVALANCHE needed to run this test")
@pytest.mark.xdist_group("fork:avalanche:midnight")
def test_pharaoh_refuses_redemption_without_direct_underlying_liquidity(
    pharaoh_avalanche_web3: Web3,
    pharaoh_avalanche_snapshot: None,
) -> None:
    """Pharaoh refuses a redemption that its direct USDC balance cannot pay.

    1. Fund and deposit USDC into the exact Pharaoh vault at the pinned block.
    2. Drain direct underlying on Anvil to model loan-deployed capital.
    3. Prove the measured partial capacity is accepted and one raw share above is refused.
    4. Verify the 40acres preflight refuses before any redeem transaction exists.
    """
    # 1. Fund and deposit USDC into the exact Pharaoh vault at the pinned block.
    assert pharaoh_avalanche_snapshot is None
    vault = create_vault_instance_autodetect(pharaoh_avalanche_web3, vault_address=PHARAOH_USDC_AVALANCHE_ADDRESS)
    assert isinstance(vault, FortyAcresVault)
    manager = vault.get_deposit_manager()
    assert isinstance(manager, FortyAcresDepositManager)
    owner = pharaoh_avalanche_web3.eth.accounts[0]
    raw_deposit_amount = vault.denomination_token.convert_to_raw(Decimal(1))
    fund_erc20_on_anvil(pharaoh_avalanche_web3, vault.denomination_token.address, owner, raw_deposit_amount)
    approval_hash = vault.denomination_token.approve(vault.address, Decimal(1)).transact({"from": owner})
    assert_transaction_success_with_explanation(pharaoh_avalanche_web3, approval_hash)
    manager.create_deposit_request(owner=owner, raw_amount=raw_deposit_amount).broadcast(from_=owner)
    raw_shares = vault.share_token.fetch_raw_balance_of(owner)
    assert raw_shares > 0
    assert manager.create_redemption_request(owner=owner, raw_shares=raw_shares).funcs

    # 2. Drain all but half a USDC unit on Anvil to model deployed loan capital.
    set_balance(pharaoh_avalanche_web3, vault.address, 10**18)
    idle_raw_assets = vault.denomination_token.fetch_raw_balance_of(vault.address)
    assert idle_raw_assets >= raw_deposit_amount
    remaining_raw_assets = raw_deposit_amount // 2
    drain_hash = vault.denomination_token.transfer(
        pharaoh_avalanche_web3.eth.accounts[1],
        vault.denomination_token.convert_to_decimals(idle_raw_assets - remaining_raw_assets),
    ).transact({"from": vault.address})
    assert_transaction_success_with_explanation(pharaoh_avalanche_web3, drain_hash)
    assert vault.denomination_token.fetch_raw_balance_of(vault.address) == remaining_raw_assets

    # 3. Construct a redemption at the real cap, then attempt one raw share above it.
    available_raw_shares = manager.fetch_redeemable_raw_shares(owner)
    assert 0 < available_raw_shares < raw_shares
    assert manager.create_redemption_request(owner=owner, raw_shares=available_raw_shares).funcs
    block_before_refusal = pharaoh_avalanche_web3.eth.block_number
    with pytest.raises(VaultFlowUnavailable) as exc_info:
        manager.create_redemption_request(owner=owner, raw_shares=available_raw_shares + 1)

    # 4. Verify the 40acres preflight refuses before any redeem transaction exists.
    error = exc_info.value
    assert error.preflight_result == "redemption_capacity_limited"
    assert error.decoded_error == FORTY_ACRES_INSUFFICIENT_LIQUIDITY_ERROR
    assert error.direction == "redeem"
    assert error.phase == "preflight"
    assert error.requested_raw_amount == available_raw_shares + 1
    assert error.available_raw_amount == available_raw_shares
    assert pharaoh_avalanche_web3.eth.block_number == block_before_refusal


@pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run this test")
@pytest.mark.xdist_group("fork:base:midnight")
def test_aerodrome_uses_generic_manager_and_redeems(
    aerodrome_base_web3: Web3,
    aerodrome_base_snapshot: None,
) -> None:
    """Aerodrome keeps the generic flow and completes a real redemption.

    Aerodrome has demonstrated a successful redemption despite little idle
    underlying, so it is the regression control for Pharaoh's address-scoped
    direct-balance preflight.
    """
    assert aerodrome_base_snapshot is None
    vault = create_vault_instance_autodetect(aerodrome_base_web3, vault_address=AERODROME_USDC_VAULT)
    assert isinstance(vault, FortyAcresVault)
    manager = vault.get_deposit_manager()
    assert isinstance(manager, ERC4626DepositManager)
    assert not isinstance(manager, FortyAcresDepositManager)
    owner = aerodrome_base_web3.eth.accounts[0]
    deposit_amount = Decimal(1)
    denomination_balance_before = vault.denomination_token.fetch_raw_balance_of(owner)
    funding_hash = vault.denomination_token.transfer(owner, deposit_amount).transact({"from": USDC_WHALE[8453]})
    assert_transaction_success_with_explanation(aerodrome_base_web3, funding_hash)
    approval_hash = vault.denomination_token.approve(vault.address, deposit_amount).transact({"from": owner})
    assert_transaction_success_with_explanation(aerodrome_base_web3, approval_hash)
    manager.create_deposit_request(owner=owner, amount=deposit_amount).broadcast(from_=owner)
    raw_shares = vault.share_token.fetch_raw_balance_of(owner)
    assert raw_shares > 0

    redemption_ticket = manager.create_redemption_request(owner=owner, raw_shares=raw_shares).broadcast(from_=owner)

    assert redemption_ticket.raw_shares == raw_shares
    assert vault.share_token.fetch_raw_balance_of(owner) == 0
    assert vault.denomination_token.fetch_raw_balance_of(owner) > denomination_balance_before
