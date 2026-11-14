"""Regressions for limits that an optimistic ERC-4626 maximum cannot establish."""

import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from eth_abi import encode
from web3 import Web3
from web3.exceptions import ContractLogicError

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature, GENERIC_ERC4626_PROTOCOL_NAME, get_vault_protocol_name
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.erc_4626.vault_protocol.ipor.deposit_redeem import IPORDepositManager, IPOR_EXTERNAL_STATE_PRE_HOOK_STALE_SELECTOR
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault, LagoonVersion
from eth_defi.erc_4626.vault_protocol.lagoon.deposit_redeem import LagoonDepositManager
from eth_defi.erc_4626.vault_protocol.upshift.vault import UpshiftVault
from eth_defi.erc_4626.vault_protocol.vault_morpho.vault import VaultMorphoVault, VaultMorphoDepositManager
from eth_defi.erc_4626.vault_protocol.morpho.deposit_redeem import MorphoV2DepositManager
from eth_defi.erc_4626.vault_protocol.lagoon.testing import force_lagoon_settle
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable, UnsupportedVaultSimulation
from eth_defi.vault.risk import get_vault_risk
from eth_defi.erc_4626.vault import ERC4626HistoricalReader
from eth_defi.event_reader.multicall_batcher import EncodedCallResult
from eth_defi.vault.base import VaultHistoricalRead
from eth_defi.vault.strategy_tag import StrategyTag


OWNER = "0x1111111111111111111111111111111111111111"


def test_upshift_legacy_pause_wins_over_positive_maximum() -> None:
    """A legacy pause must stop deposits even when the reported maximum is positive.

    1. Model the verified legacy interface without making RPC calls.
    2. Check the explicit pause takes precedence over maxDeposit.
    3. Reopen deposits and retain the ordinary capacity result.
    """
    # 1. Mock only contract transport so the actual closure reader is exercised.
    vault = object.__new__(UpshiftVault)
    vault.features = {ERC4626Feature.upshift_like}
    vault.default_block_identifier = 123
    contract = Mock()
    vault.vault_contract = contract
    contract.functions.maxDeposit.return_value.call.return_value = 1000
    # 2. A positive maximum must not bypass the explicit pause.
    contract.functions.depositsPaused.return_value.call.return_value = True
    assert vault.fetch_deposit_closed_reason() == "Upshift depositsPaused() is true"
    contract.functions.depositsPaused.return_value.call.assert_called_once_with(block_identifier=123)
    contract.functions.maxDeposit.assert_not_called()
    # 3. Reopening preserves the existing generic maximum check.
    contract.functions.depositsPaused.return_value.call.return_value = False
    assert vault.fetch_deposit_closed_reason() is None
    contract.functions.maxDeposit.return_value.call.return_value = 0
    assert vault.fetch_deposit_closed_reason() is not None


def test_lagoon_capacity_includes_pending_assets() -> None:
    """Queued deposits consume v0.6 capacity before they enter totalAssets.

    1. Model a cap with both accepted and pending denomination assets.
    2. Read remaining capacity from a single selected block.
    3. Check full capacity and unsupported versions without using maxDeposit.
    """
    # 1. Mock transport rather than deploying an unrelated Lagoon version.
    functions = Mock()
    balance = Mock()
    functions.maxCap.return_value.call.return_value = 100
    functions.totalAssets.return_value.call.return_value = 80
    balance.call.return_value = 15
    vault = SimpleNamespace(version=LagoonVersion.v_0_6_0, default_block_identifier=123, vault_contract=SimpleNamespace(functions=functions), denomination_token=SimpleNamespace(contract=SimpleNamespace(functions=SimpleNamespace(balanceOf=lambda _owner: balance))), silo_address=OWNER)
    # 2. Pending assets leave only five units for a new deposit.
    assert LagoonVault.fetch_deposit_capacity(vault) == 5
    functions.maxCap.return_value.call.assert_called_with(block_identifier=123)
    functions.totalAssets.return_value.call.assert_called_with(block_identifier=123)
    balance.call.assert_called_with(block_identifier=123)
    functions.maxDeposit.assert_not_called()
    # 3. Saturate instead of underflowing and retain older-version behaviour.
    balance.call.return_value = 25
    assert LagoonVault.fetch_deposit_capacity(vault) == 0
    vault.version = LagoonVersion.v_0_5_0
    assert LagoonVault.fetch_deposit_capacity(vault) is None


def test_lagoon_request_enforces_cap_without_changing_claim_limit() -> None:
    """A zero or small new-deposit cap must stop request construction.

    1. Supply capacity separately from the asynchronous claim entitlement.
    2. Check a nonzero size limit and a global zero-cap refusal.
    3. Build an admissible immediate deposit using the existing call.
    """
    # 1. Mock the contract boundary; access-policy behaviour has separate coverage.
    vault = Mock(spec=LagoonVault)
    vault.address = OWNER
    vault.denomination_token.convert_to_decimals.side_effect = lambda raw: raw
    manager = LagoonDepositManager(vault)
    # 2. Refuse before encoding a transaction and retain the available amount.
    with patch.object(manager, "_assert_deposit_request_available"), patch.object(manager, "has_synchronous_deposit", return_value=True):
        for capacity in (5, 0):
            vault.fetch_deposit_capacity.return_value = capacity
            with pytest.raises(VaultFlowUnavailable) as exc:
                manager.create_deposit_request(OWNER, raw_amount=6)
            assert exc.value.available_raw_amount == capacity
            assert exc.value.preflight_result == ("deposit_closed" if capacity == 0 else "deposit_capacity_limited")
        # 3. A valid amount keeps the ordinary syncDeposit path.
        vault.fetch_deposit_capacity.return_value = 5
        request = manager.create_deposit_request(OWNER, raw_amount=5)
        assert request.raw_amount == 5
        vault.vault_contract.functions.syncDeposit.assert_called_once()


def test_ipor_stale_state_is_not_hidden_by_positive_capacity() -> None:
    """A positive maximum cannot prove that an IPOR external observation is fresh.

    1. Supply a decoded pre-hook failure without an external provider.
    2. Request an exit whose measured capacity covers every share.
    3. Check deposit and redemption failures retain timestamps and raw evidence.
    """
    # 1. Stub only calls outside the preflight classification under test.
    payload = IPOR_EXTERNAL_STATE_PRE_HOOK_STALE_SELECTOR + encode(["uint256", "uint256", "uint256"], [100, 4000, 3600])
    manager = object.__new__(IPORDepositManager)
    manager.vault = SimpleNamespace(address=OWNER, get_redeem_function_selector=lambda: b"test", get_deposit_function_selector=lambda: b"test")
    manager._assert_immediate_access = Mock()
    manager.fetch_redeemable_raw_shares = Mock(return_value=100)
    manager.fetch_redeem_simulation = Mock(return_value=(False, payload))
    # 2. This used to skip the exact call when the maximum covered the request.
    with pytest.raises(VaultFlowUnavailable) as exc:
        manager.create_redemption_request(OWNER, raw_shares=100)
    manager.fetch_redeem_simulation.assert_called_once_with(OWNER, 100)
    assert exc.value.available_raw_amount == 100
    # 3. Both directions identify stale observations rather than liquidity.
    assert exc.value.decoded_error == "ExternalStatePreHookStale"
    assert "oldest update 100" in str(exc.value)
    assert "maximum age 3600 seconds" in str(exc.value)
    assert exc.value.raw_revert_data == payload
    func = Mock()
    func.call.side_effect = ContractLogicError("execution reverted", data=payload.hex())
    request = SimpleNamespace(funcs=[func], raw_amount=100)
    with patch.object(ERC4626DepositManager, "create_deposit_request", return_value=request):
        with pytest.raises(VaultFlowUnavailable) as exc:
            manager.create_deposit_request(OWNER, raw_amount=100)
    assert exc.value.direction == "deposit"
    assert exc.value.decoded_error == "ExternalStatePreHookStale"


def test_lagoon_guardrail_refusal_needs_operator_valuation() -> None:
    """An operator valuation cannot be replaced by a NAV chosen to pass the guard.

    1. Model the real guardrail error on a disposable fork.
    2. Attempt settlement using the supplied valuation.
    3. Check the refusal occurs before a valuation transaction is sent.
    """
    # 1. Mock transaction transport; deployed guard arithmetic is checked separately.
    vault = Mock(spec=LagoonVault)
    vault.version = LagoonVersion.v_0_6_0
    vault.address = OWNER
    vault.web3 = Mock()
    vault.web3.eth.get_balance.return_value = 10**18
    vault.vault_contract.functions.isTotalAssetsValid.return_value.call.return_value = False
    valuation = vault.vault_contract.functions.updateNewTotalAssets.return_value
    valuation.call.side_effect = ContractLogicError("execution reverted", data="0x21aa421f")
    # 2. Refuse the known guardrail failure without modifying its configuration.
    with patch("eth_defi.erc_4626.vault_protocol.lagoon.testing.is_anvil", return_value=True):
        with pytest.raises(UnsupportedVaultSimulation) as exc:
            force_lagoon_settle(vault, OWNER, raw_nav=280393673720)
    # 3. Preserve an explicit operator dependency, not a fabricated settlement.
    assert exc.value.unsupported_reason == "lagoon_valuation_guardrails"
    valuation.transact.assert_not_called()


def test_vault_morpho_cap_does_not_change_canonical_morpho() -> None:
    """The fork's extra cap must not become a requirement for ordinary MetaMorpho.

    1. Resolve each implementation from its detected features without RPC.
    2. Model a positive advertised maximum and a smaller global TVL cap.
    3. Check the cap remains bounded at zero when current assets exceed it.
    """
    # 1. Only transport is mocked; factory selection and capacity readers are real.
    provider = Mock()
    provider.eth.chain_id = 1
    vault = create_vault_instance(provider, OWNER, features={ERC4626Feature.morpho_like, ERC4626Feature.vault_morpho_like})
    canonical = create_vault_instance(provider, OWNER, features={ERC4626Feature.morpho_like})
    assert get_vault_protocol_name(vault.features) == GENERIC_ERC4626_PROTOCOL_NAME
    assert not isinstance(vault, type(canonical))
    assert vault.get_fee_mode() is None
    assert get_vault_risk(vault.get_protocol_name(), OWNER) is None
    assert "app.morpho.org" not in vault.get_link()
    assert not hasattr(vault, "morpho_api_result")
    with pytest.raises(NotImplementedError):
        vault.is_whitelisted_deposit()
    assert isinstance(vault, VaultMorphoVault)
    assert not isinstance(canonical, VaultMorphoVault)
    # 2. Read related values from one pinned block and honour the tighter cap.
    vault.default_block_identifier = 123
    contract = Mock()
    vault.vault_contract = contract
    contract.functions.tvlCap.return_value.call.return_value = 100
    contract.functions.totalAssets.return_value.call.return_value = 90
    contract.functions.maxDeposit.return_value.call.return_value = 1000
    manager = vault.get_deposit_manager()
    assert isinstance(manager, VaultMorphoDepositManager)
    assert manager.fetch_depositable_raw_assets(OWNER) == 10
    contract.functions.tvlCap.return_value.call.assert_called_with(block_identifier=123)
    contract.functions.totalAssets.return_value.call.assert_called_with(block_identifier=123)
    # 3. An already exhausted cap is a genuine global refusal.
    contract.functions.totalAssets.return_value.call.return_value = 101
    assert manager.fetch_depositable_raw_assets(OWNER) == 0
    assert vault.fetch_deposit_closed_reason() == "VaultMorpho tvlCap reached"


def test_morpho_market_adapter_zero_cap_is_not_a_funding_failure() -> None:
    """Use the configured adapter's IDs to expose zero-cap routes before funding.

    1. Model the verified market adapter, with no funded wallet or approval.
    2. Refuse a zero absolute cap and identify the failing allocation ID.
    3. Leave an admitted route to normal request validation.
    """
    # 1. Mock contract transport only; the real manager chooses and checks IDs.
    adapter = "0x2222222222222222222222222222222222222222"
    outer, market = Mock(), Mock()
    outer.functions.liquidityAdapter.return_value.call.return_value = adapter
    params = ("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", OWNER, OWNER, OWNER, 86)
    outer.functions.liquidityData.return_value.call.return_value = encode(["(address,address,address,address,uint256)"], [params])
    market.functions.parentVault.return_value.call.return_value = OWNER
    allocation_id = b"\x03" * 32
    market.functions.ids.return_value.call.return_value = [allocation_id]
    manager = object.__new__(MorphoV2DepositManager)
    manager.vault = SimpleNamespace(address=OWNER, web3=Mock())
    manager.web3.eth.call.return_value = b""
    bind = lambda _web3, abi, _address: outer if abi == "morpho/IVaultV2.json" else market
    # 2. The absence of allowance cannot hide this independently proven cap.
    with patch("eth_defi.erc_4626.vault_protocol.morpho.deposit_redeem.get_deployed_contract", side_effect=bind), patch.object(ERC4626DepositManager, "create_deposit_request", return_value="request") as base:
        outer.functions.absoluteCap.return_value.call.return_value = 0
        with pytest.raises(VaultFlowUnavailable) as exc:
            manager.create_deposit_request(OWNER, raw_amount=100)
        assert exc.value.decoded_error == "ZeroAbsoluteCap"
        assert exc.value.requested_raw_amount == 100
        assert exc.value.available_raw_amount == 0
        assert allocation_id.hex() in str(exc.value)
        base.assert_not_called()
        # 3. A positive cap adds no alternative amount or duplicated allocation model.
        outer.functions.absoluteCap.return_value.call.return_value = 1000
        assert manager.create_deposit_request(OWNER, raw_amount=100) == "request"
        assert market.functions.ids.call_args.args[0][0] == "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


def test_morpho_arithmetic_and_exit_gate_failures_remain_distinct() -> None:
    """A panic must not be called illiquidity, and a denied owner must stay denied.

    1. Supply the two captured revert payloads without a provider.
    2. Run the existing exact-redemption preflight.
    3. Preserve the error and original share quantity for each refusal.
    """
    # 1. The common request builder is covered separately; stub its valid request.
    manager = object.__new__(MorphoV2DepositManager)
    manager.vault = SimpleNamespace(address=OWNER, chain_id=1, get_protocol_name=lambda: "Morpho")
    func = Mock()
    request = SimpleNamespace(raw_shares=100, funcs=[func])
    cases = [(bytes.fromhex("4e487b71") + (17).to_bytes(32), "Panic(0x11)", "redemption_call_reverted"), (bytes.fromhex("876736d1"), "CannotSendShares", "whitelisting-needed")]
    # 2. Exercise classification through the same exact-call path used in execution.
    with patch.object(ERC4626DepositManager, "create_redemption_request", return_value=request):
        for payload, decoded, result in cases:
            func.call.side_effect = ContractLogicError("execution reverted", data="0x" + payload.hex())
            with pytest.raises(VaultFlowUnavailable) as exc:
                manager.create_redemption_request(OWNER, raw_shares=100)
            # 3. These are known protocol failures, not a guessed liquidity shortage.
            assert exc.value.decoded_error == decoded
            assert exc.value.preflight_result == result
            assert exc.value.requested_raw_amount == 100
            assert exc.value.raw_revert_data == payload


@pytest.mark.parametrize("failure", [None, ContractLogicError("allowance not yet approved"), ValueError({"code": -32000, "message": "execution reverted", "data": "0x12345678"})])
def test_ipor_deposit_keeps_fresh_and_unrelated_revert_paths(failure: Exception | None) -> None:
    """Only a proven stale observation should refuse the new deposit preflight.

    1. Build a normal request before any approval transaction is submitted.
    2. Model a fresh call or an unrelated contract refusal.
    3. Retain the request for normal execution and approval checks.
    """
    manager = object.__new__(IPORDepositManager)
    manager.vault = SimpleNamespace(address=OWNER, get_deposit_function_selector=lambda: b"test")
    manager._assert_immediate_access = Mock()
    func = Mock()
    func.call.side_effect = failure
    request = SimpleNamespace(funcs=[func], raw_amount=100)
    with patch.object(ERC4626DepositManager, "create_deposit_request", return_value=request):
        assert manager.create_deposit_request(OWNER, raw_amount=100) is request
    func.call.assert_called_once_with({"from": OWNER})


def test_ipor_deposit_does_not_hide_provider_failure() -> None:
    """A transport failure contains no evidence that the request was simulated.

    1. Model a provider error without revert data.
    2. Attempt the new deposit preflight.
    3. Preserve the provider failure for the operator to retry.
    """
    manager = object.__new__(IPORDepositManager)
    manager.vault = SimpleNamespace(address=OWNER, get_deposit_function_selector=lambda: b"test")
    manager._assert_immediate_access = Mock()
    func = Mock()
    func.call.side_effect = ValueError("provider unavailable")
    request = SimpleNamespace(funcs=[func], raw_amount=100)
    with patch.object(ERC4626DepositManager, "create_deposit_request", return_value=request):
        with pytest.raises(ValueError, match="provider unavailable"):
            manager.create_deposit_request(OWNER, raw_amount=100)


def test_vault_morpho_retains_measured_lending_without_protocol_defaults() -> None:
    """Wrong attribution must not erase independently measured lending behaviour.

    1. Use the real ABI with a mocked token-balance RPC response.
    2. Preserve the metadata snapshot, lending tag and historical idle measurement.
    3. Leave fees and failed idle observations unknown rather than inventing zeroes.
    """
    # 1. Only transport and the separately covered core historical decoder are replaced.
    provider = Mock()
    provider.eth.chain_id = 1
    vault = create_vault_instance(provider, OWNER, features={ERC4626Feature.morpho_like, ERC4626Feature.vault_morpho_like})
    vault.first_seen_at_block = 1
    transport = Web3()
    token = SimpleNamespace(address=OWNER, contract=get_deployed_contract(transport, "ERC20MockDecimals.json", OWNER), convert_to_decimals=lambda raw: Decimal(raw) / Decimal(10**6), convert_to_raw=lambda amount: int(amount * 10**6))
    vault.denomination_token = token
    raw_idle = 25_000_000
    # 2. The existing shared snapshot helper and historical codec retain the same units.
    with patch.object(transport.eth, "call", return_value=raw_idle.to_bytes(32, "big")):
        liquidity, utilisation = vault.fetch_lending_snapshot(Decimal(100), 123)
    assert liquidity == pytest.approx(Decimal(25))
    assert utilisation == pytest.approx(0.75)
    assert vault.get_strategy_tags() == {StrategyTag.lending}
    reader = vault.get_historical_reader(stateful=False)
    with patch.object(ERC4626HistoricalReader, "construct_multicalls", return_value=iter(())):
        calls = list(reader.construct_multicalls())
    assert len(calls) == 1
    assert calls[0].extra_data["function"] == "idle_assets"
    timestamp = datetime.datetime(2026, 11, 14)
    core = VaultHistoricalRead(vault=vault, block_number=123, timestamp=timestamp, share_price=Decimal(1), total_assets=Decimal(100), total_supply=Decimal(100), performance_fee=None, management_fee=None, errors=None)
    idle = EncodedCallResult(call=calls[0], success=True, result=raw_idle.to_bytes(32, "big"), block_identifier=123)
    with patch.object(ERC4626HistoricalReader, "process_result", return_value=core):
        result = reader.process_result(123, timestamp, [idle])
        assert result.available_liquidity == pytest.approx(Decimal(25))
        assert result.utilisation == pytest.approx(0.75)
        assert result.share_price == pytest.approx(Decimal(1))
        # 3. Missing measurements and unverified fees retain their unknown values.
        assert result.management_fee is result.performance_fee is None
        for success in (False, True):
            failed_idle = EncodedCallResult(call=calls[0], success=success, result=b"", block_identifier=123)
            failed = reader.process_result(123, timestamp, [failed_idle])
            assert failed.available_liquidity is failed.utilisation is None
