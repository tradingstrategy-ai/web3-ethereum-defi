"""Accountable Capital protocol tests.

Monad does not support archive nodes — all tests use the latest block
and zero-relative assertions instead of hardcoded values.
"""

import datetime
import logging
import os
from decimal import Decimal
from pathlib import Path

import flaky
import pytest
from eth_typing import HexAddress, HexStr
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.accountable.deposit_redeem import ACCOUNTABLE_INSUFFICIENT_AMOUNT_SELECTOR, AccountableDepositManager, AccountableRedemptionTicket
from eth_defi.erc_4626.vault_protocol.accountable.offchain_metadata import (
    fetch_accountable_vaults,
)
from eth_defi.erc_4626.vault_protocol.accountable.vault import AccountableHistoricalReader, AccountableVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus, UnsupportedVaultSimulation, VaultFlowUnavailable

JSON_RPC_MONAD = os.environ.get("JSON_RPC_MONAD")
MONAD_USDC_WHALE = HexAddress(HexStr("0xf89d7b9c864f589bbF53a82105107622B35EaA40"))
DEPOSIT_AMOUNT = Decimal("1000")

pytestmark = pytest.mark.skipif(JSON_RPC_MONAD is None, reason="JSON_RPC_MONAD needed to run these tests")


@pytest.fixture(scope="module")
def anvil_monad_fork(request) -> AnvilLaunch:
    """Fork at the latest block — Monad RPCs do not support archive state."""
    launch = fork_network_anvil(JSON_RPC_MONAD, unlocked_addresses=[MONAD_USDC_WHALE])
    try:
        yield launch
    finally:
        launch.close(log_level=logging.INFO)


@pytest.fixture(scope="module")
def web3(anvil_monad_fork):
    web3 = create_multi_provider_web3(
        anvil_monad_fork.json_rpc_url,
        retries=3,
        default_http_timeout=(10, 60),
    )
    web3.provider.make_request("anvil_setBalance", [MONAD_USDC_WHALE, hex(10**20)])
    return web3


@pytest.mark.timeout(180)
@flaky.flaky
def test_accountable_susn_vault(
    web3: Web3,
    tmp_path: Path,
):
    """Test sUSN Delta Neutral Yield Vault detection.

    https://monadscan.com/address/0x58ba69b289De313E66A13B7D1F822Fc98b970554
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x58ba69b289De313E66A13B7D1F822Fc98b970554",
    )

    assert isinstance(vault, AccountableVault)
    assert ERC4626Feature.accountable_like in vault.features
    assert vault.get_protocol_name() == "Accountable"
    assert vault.denomination_token.symbol == "USDC"
    assert isinstance(vault.get_deposit_manager(), AccountableDepositManager)
    assert vault.get_deposit_manager_capability().as_dict() == {
        "can_deposit": True,
        "can_redeem": True,
        "deposit_flow": "synchronous",
        "redemption_flow": "asynchronous",
        "supports_anvil_settlement": False,
        "anvil_settlement_unsupported_reason": "accountable_redemption_settlement_is_strategy_operator_controlled",
    }

    # Management fee not available, performance fee from offchain metadata
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is not None

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Accountable doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False


@pytest.mark.timeout(180)
@flaky.flaky
def test_accountable_deposit_and_redemption_request_lifecycle(web3: Web3) -> None:
    """Execute the depositor-controlled Accountable lifecycle on the fork.

    :param web3:
        Monad Anvil fork connection with a native-USDC holder unlocked.
    """
    vault = create_vault_instance_autodetect(web3, vault_address="0x58ba69b289De313E66A13B7D1F822Fc98b970554")
    assert isinstance(vault, AccountableVault)
    manager = vault.get_deposit_manager()
    assert isinstance(manager, AccountableDepositManager)
    assert manager.has_synchronous_deposit() is True
    assert manager.has_synchronous_redemption() is False

    # The binding deposit minimum is the greater of the vault-level
    # MIN_AMOUNT_WEI and the strategy's per-loan minDeposit; a deposit clearing
    # only the vault minimum reverts InsufficientAmount() inside the strategy.
    vault_minimum = int(vault.vault_contract.functions.MIN_AMOUNT_WEI().call())
    strategy_minimum = manager._fetch_strategy_loan_min_deposit()
    minimum = max(vault_minimum, strategy_minimum or 0)
    assert minimum > vault_minimum, "This vault's strategy should raise the binding minimum above MIN_AMOUNT_WEI"
    with pytest.raises(VaultFlowUnavailable, match="below minimum") as exc_info:
        manager.create_deposit_request(owner=web3.eth.accounts[1], raw_amount=minimum - 1)
    assert exc_info.value.decoded_error == "InsufficientAmount"
    assert exc_info.value.preflight_result == "below_minimum"
    assert exc_info.value.error_selector == ACCOUNTABLE_INSUFFICIENT_AMOUNT_SELECTOR
    assert exc_info.value.minimum_raw_amount == minimum
    assert exc_info.value.available_raw_amount is None
    unchecked_request = manager.create_deposit_request(
        owner=web3.eth.accounts[1],
        raw_amount=minimum,
        check_max_deposit=False,
        check_enough_token=False,
    )
    assert unchecked_request.raw_amount == minimum

    synchronous_settlement = manager.force_settle(None)
    assert synchronous_settlement.settlement_required is False
    assert synchronous_settlement.transaction_hashes == ()

    owner = web3.eth.accounts[0]
    usdc: TokenDetails = vault.denomination_token
    funding_hash = usdc.transfer(owner, DEPOSIT_AMOUNT).transact({"from": MONAD_USDC_WHALE})
    assert_transaction_success_with_explanation(web3, funding_hash)
    approval_hash = usdc.approve(vault.address, DEPOSIT_AMOUNT).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, approval_hash)

    assert manager.estimate_deposit(owner, DEPOSIT_AMOUNT) > 0
    deposit_ticket = manager.create_deposit_request(owner=owner, amount=DEPOSIT_AMOUNT).broadcast(from_=owner)
    deposit_analysis = manager.analyse_deposit(deposit_ticket.tx_hash, deposit_ticket)
    assert deposit_analysis.denomination_amount == DEPOSIT_AMOUNT
    assert deposit_analysis.share_count > 0

    raw_shares = vault.share_token.fetch_raw_balance_of(owner)
    assert raw_shares > 0
    redemption_request = manager.create_redemption_request(owner=owner, raw_shares=raw_shares)
    assert len(redemption_request.funcs) == 1
    assert redemption_request.funcs[0].fn_name == "requestRedeem"
    assert redemption_request.funcs[0].args == (raw_shares, owner, owner)

    ticket = redemption_request.broadcast(from_=owner)
    assert isinstance(ticket, AccountableRedemptionTicket)
    assert ticket.owner == owner
    assert ticket.controller == owner
    assert ticket.to == owner
    assert ticket.raw_shares == raw_shares
    assert manager.reconstruct_redemption_ticket(manager.serialize_redemption_ticket(ticket)) == ticket
    assert manager.get_redemption_request_status(ticket) in {
        AsyncVaultRequestStatus.pending,
        AsyncVaultRequestStatus.claimable,
    }
    with pytest.raises(UnsupportedVaultSimulation, match="strategy-operator controlled") as exc_info:
        manager.force_settle(ticket)
    assert exc_info.value.unsupported_reason == "accountable_redemption_settlement_is_strategy_operator_controlled"


@pytest.mark.timeout(180)
@flaky.flaky
def test_accountable_redemption_request_rejects_contract_dust(web3: Web3) -> None:
    """Reject an amount the live Accountable contract would deterministically revert.

    :param web3:
        Monad Anvil fork connection.
    """
    vault = create_vault_instance_autodetect(web3, vault_address="0x58ba69b289De313E66A13B7D1F822Fc98b970554")
    assert isinstance(vault, AccountableVault)
    manager = vault.get_deposit_manager()
    minimum = int(vault.vault_contract.functions.MIN_AMOUNT_WEI().call())

    with pytest.raises(VaultFlowUnavailable, match="below minimum") as exc_info:
        manager.create_redemption_request(
            owner=web3.eth.accounts[1],
            raw_shares=minimum - 1,
            check_enough_token=False,
        )
    assert exc_info.value.decoded_error == "InsufficientAmount"
    assert exc_info.value.preflight_result == "below_minimum"
    assert exc_info.value.direction == "redeem"


@pytest.mark.timeout(180)
@flaky.flaky
def test_accountable_hyperithm_strategy_min_deposit(web3: Web3) -> None:
    """Refuse a below-strategy-minimum deposit on the live Hyperithm Monad vault.

    The Hyperithm Delta Neutral vault delegates to an open-term strategy whose
    ``loan().minDeposit`` (1,000 USDC) far exceeds the vault ``MIN_AMOUNT_WEI``.
    A deposit that clears the vault minimum but not the strategy minimum reverts
    ``InsufficientAmount()`` (`0x5945ea56`) inside ``strategy.onDeposit``; the
    manager must surface this as a typed preflight refusal instead.

    Monad retains only a moving recent historical-state window, so this uses
    current-head, state-relative assertions rather than a fixed block.

    :param web3:
        Monad Anvil fork connection.
    """
    vault = create_vault_instance_autodetect(web3, vault_address="0x7cd231120a60f500887444a9baf5e1bd753a5e59")
    assert isinstance(vault, AccountableVault)
    manager = vault.get_deposit_manager()
    assert isinstance(manager, AccountableDepositManager)

    vault_minimum = int(vault.vault_contract.functions.MIN_AMOUNT_WEI().call())
    strategy_minimum = manager._fetch_strategy_loan_min_deposit()
    assert strategy_minimum is not None and strategy_minimum > vault_minimum

    # Clears MIN_AMOUNT_WEI but is below the strategy loan minimum.
    below_strategy = strategy_minimum - 1
    assert below_strategy > vault_minimum
    with pytest.raises(VaultFlowUnavailable, match="below minimum") as exc_info:
        manager.create_deposit_request(
            owner=web3.eth.accounts[1],
            raw_amount=below_strategy,
            check_max_deposit=False,
            check_enough_token=False,
        )
    assert exc_info.value.decoded_error == "InsufficientAmount"
    assert exc_info.value.preflight_result == "below_minimum"
    assert exc_info.value.error_selector == ACCOUNTABLE_INSUFFICIENT_AMOUNT_SELECTOR
    assert exc_info.value.minimum_raw_amount == strategy_minimum
    assert exc_info.value.direction == "deposit"


@pytest.mark.timeout(180)
@flaky.flaky
def test_accountable_yuzu_vault(
    web3: Web3,
    tmp_path: Path,
):
    """Test Yuzu Money Vault detection.

    https://monadscan.com/address/0x3a2c4aAae6776dC1c31316De559598f2f952E2cB
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x3a2c4aAae6776dC1c31316De559598f2f952E2cB",
    )

    assert isinstance(vault, AccountableVault)
    assert ERC4626Feature.accountable_like in vault.features
    assert vault.get_protocol_name() == "Accountable"
    assert vault.denomination_token.symbol == "USDC"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Accountable doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False


@pytest.mark.timeout(180)
@flaky.flaky
def test_accountable_asia_credit_vault(
    web3: Web3,
    tmp_path: Path,
):
    """Test Asia Credit Yield Vault detection.

    https://monadscan.com/address/0x4C0d041889281531fF060290d71091401Caa786D
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x4C0d041889281531fF060290d71091401Caa786D",
    )

    assert isinstance(vault, AccountableVault)
    assert ERC4626Feature.accountable_like in vault.features
    assert vault.get_protocol_name() == "Accountable"
    assert vault.denomination_token.symbol == "USDC"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Accountable doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False


@pytest.mark.timeout(180)
@flaky.flaky
def test_accountable_aegis_vault(
    web3: Web3,
    tmp_path: Path,
):
    """Test Aegis Yield Vault detection and corrected NAV.

    Accountable's totalAssets() only returns idle liquidity, excluding deployed capital.
    fetch_total_assets() must use convertToAssets(totalSupply()) for the true NAV.

    https://monadscan.com/address/0x0a4AfB907672279926c73Dc1F77151931c2A55cC
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x0a4AfB907672279926c73Dc1F77151931c2A55cC",
    )

    assert isinstance(vault, AccountableVault)
    assert ERC4626Feature.accountable_like in vault.features
    assert vault.get_protocol_name() == "Accountable"
    assert vault.denomination_token.symbol == "USDC"

    # fetch_total_assets uses convertToAssets(totalSupply()) for the true NAV
    nav = vault.fetch_total_assets("latest")
    assert nav > 0

    # fetch_idle_capital returns the raw totalAssets() = idle liquidity only
    idle = vault.fetch_idle_capital()
    assert idle >= 0
    assert nav >= idle

    # fetch_available_liquidity delegates to fetch_idle_capital
    assert vault.fetch_available_liquidity() == idle

    # Utilisation = deployed capital / true NAV
    utilisation = vault.fetch_utilisation_percent()
    assert utilisation == pytest.approx(float((nav - idle) / nav), rel=0.001)
    assert utilisation > 0.90  # most capital is deployed

    # fetch_nav should match fetch_total_assets
    nav_from_fetch = vault.fetch_nav()
    assert nav_from_fetch == nav

    # Accountable doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False


@pytest.mark.timeout(180)
@flaky.flaky
def test_accountable_historical_reader(
    web3: Web3,
):
    """Test AccountableHistoricalReader computes correct NAV from multicall results.

    The historical reader must override total_assets with share_price * total_supply
    instead of using the raw totalAssets() value (which is only idle liquidity).
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x0a4AfB907672279926c73Dc1F77151931c2A55cC",
    )

    assert isinstance(vault, AccountableVault)

    # Verify Accountable-specific historical reader is returned
    reader = vault.get_historical_reader(stateful=False)
    assert isinstance(reader, AccountableHistoricalReader)

    # Read vault state at the fork block using the historical reader
    block_number = web3.eth.block_number
    block = web3.eth.get_block(block_number)
    timestamp = datetime.datetime.fromtimestamp(block["timestamp"], tz=datetime.timezone.utc).replace(tzinfo=None)

    calls = list(reader.construct_multicalls())
    call_results = [c.call_as_result(web3=web3, block_identifier=block_number) for c in calls]
    vault_read = reader.process_result(block_number, timestamp, call_results)

    assert vault_read.block_number == block_number
    assert vault_read.share_price > 0
    assert vault_read.total_supply > 0

    # total_assets from the reader is the corrected NAV (share_price * total_supply),
    # not the raw idle-only totalAssets() which would be much smaller
    assert vault_read.total_assets > 0
    assert vault_read.total_assets == pytest.approx(vault_read.share_price * vault_read.total_supply, rel=Decimal("0.001"))

    # available_liquidity is the raw totalAssets() = idle capital for withdrawals
    assert vault_read.available_liquidity >= 0

    # Utilisation reflects most capital deployed
    assert vault_read.utilisation is not None
    assert vault_read.utilisation > 0.90

    # The corrected NAV should match the direct fetch_total_assets call
    direct_nav = vault.fetch_total_assets(block_number)
    assert vault_read.total_assets == pytest.approx(direct_nav, rel=Decimal("0.001"))


@pytest.mark.timeout(180)
@flaky.flaky
def test_accountable_metadata(
    web3: Web3,
):
    """Read Accountable vault metadata from offchain yield app API.

    Uses the sUSN vault which is already detected in the Anvil fork.
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x58ba69b289De313E66A13B7D1F822Fc98b970554",
    )

    assert isinstance(vault, AccountableVault)
    assert vault.accountable_metadata is not None
    assert vault.description is not None
    assert len(vault.description) > 10
    assert vault.short_description is not None
    assert vault.accountable_metadata.get("company_name") is not None
    assert vault.manager_name == vault.accountable_metadata["company_name"]
    assert vault.accountable_metadata.get("performance_fee") is not None


@pytest.mark.timeout(180)
@flaky.flaky
def test_accountable_metadata_cache(tmp_path: Path):
    """Verify disk caching works for Accountable metadata."""
    vaults = fetch_accountable_vaults(cache_path=tmp_path)
    assert isinstance(vaults, dict)
    assert len(vaults) > 0

    # Should have cached the file
    cache_file = tmp_path / "accountable_vaults.json"
    assert cache_file.exists()
    assert cache_file.stat().st_size > 0

    # Second call should use cache (no API calls)
    vaults2 = fetch_accountable_vaults(cache_path=tmp_path)
    assert vaults2 == vaults

    # Check that at least one vault has a description
    has_description = any(v.get("description") for v in vaults.values())
    assert has_description, "Expected at least one Accountable vault with a description"
