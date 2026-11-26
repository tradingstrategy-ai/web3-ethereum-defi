"""Harvest USDC cash withdrawals through the reviewed Base strategy."""

import datetime
import os
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from requests.exceptions import ConnectionError as RequestConnectionError
from web3.exceptions import ContractLogicError

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails
from eth_defi.vault import withdrawal_capacity_readers
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import VaultFlag, get_notes, get_vault_special_flags
from eth_defi.vault.withdrawal_capacity import assess_withdrawal_capacity, enrich_withdrawal_capacity

HARVEST_VAULT = "0xC777031D50F632083Be7080e51E390709062263E"
STRATEGY = "0x1d59868D7767d703929393bDaB313302840f533c"
HOLDER = "0x3763bA0c11F35Ea982675dA8223C77Ce5A09Ac94"
REVIEW_BLOCK = 62_552_749
POOL = "0xB99B6dF96d4d5448cC0a5B3e0ef7896df9507Cf5"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


def test_harvest_withdrawal_notice() -> None:
    """Only the reviewed Base deployment receives the cash withdrawal notice."""
    note = get_notes(HARVEST_VAULT, chain_id=8453, include_defaults=False)
    assert note and "larger cash withdrawals may have to wait for borrowers to repay" in note
    assert get_notes(HARVEST_VAULT, chain_id=1, include_defaults=False) is None
    assert VaultFlag.illiquid not in get_vault_special_flags(HARVEST_VAULT)


@pytest.mark.skipif(not os.environ.get("JSON_RPC_BASE"), reason="JSON_RPC_BASE archive provider needed")
def test_harvest_cash_withdrawal_at_review_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the cash estimate and the actual holder's redemption boundary."""
    web3 = create_multi_provider_web3(read_json_rpc_url(8453))
    vault = create_vault_instance(web3, HARVEST_VAULT, features={ERC4626Feature.harvest_finance}, default_block_identifier=REVIEW_BLOCK)
    result = vault.fetch_withdrawal_capacity_limit(REVIEW_BLOCK)

    assert result.current_tvl == Decimal("1639494.876053")
    assert result.current_capacity == Decimal("101469.922989")
    assert result.capacity_warning is True
    assert result.observation["capacity_warning_source"] == "both"
    assert result.pause_warning is None
    assert result.observation["redeem_in_kind_enabled"] is True

    wrapper = get_deployed_contract(web3, "harvest/VaultV2InKind.json", HARVEST_VAULT)
    pool = get_deployed_contract(web3, "forty_acres/Vault.json", POOL)
    reported_max_withdraw = 1_639_495_342_820
    assert pool.functions.maxWithdraw(STRATEGY).call(block_identifier=REVIEW_BLOCK) == reported_max_withdraw

    # Share rounding and fee accrual leave a few USDC base units below the gross buffer.
    shares = 83_652_132_128
    received_usdc_raw = 101_469_922_980
    assert wrapper.functions.redeem(shares, HOLDER, HOLDER).call({"from": HOLDER}, block_identifier=REVIEW_BLOCK) == received_usdc_raw
    with pytest.raises((ContractLogicError, ExtraValueError), match="transfer amount exceeds balance"):
        wrapper.functions.redeem(shares + 1, HOLDER, HOLDER).call({"from": HOLDER}, block_identifier=REVIEW_BLOCK)

    assert wrapper.functions.redeemInKind(1_000_000, HOLDER, HOLDER).call({"from": HOLDER}, block_identifier=REVIEW_BLOCK) == [0, 577]

    # Exercise the export path with the scanner observation at its own timestamp.
    identity = VaultSpec(8453, HARVEST_VAULT)
    database = SimpleNamespace(rows={identity: {"_withdrawal_capacity_observation": result.observation}})
    exported = [{"chain_id": 8453, "address": HARVEST_VAULT}]
    observed_at = datetime.datetime.fromisoformat(result.observation["observed_at"])
    enrich_withdrawal_capacity(exported, database, now=observed_at)
    assert exported[0]["withdrawal_capacity_limit"]["current_capacity"] == "101469.922989"
    assert exported[0]["withdrawal_capacity_observation"]["capacity_warning_source"] == "both"
    enrich_withdrawal_capacity(exported, database, now=observed_at + datetime.timedelta(hours=27))
    assert exported[0]["withdrawal_capacity_limit"]["current_capacity"] is None
    assert exported[0]["withdrawal_capacity_observation"]["capacity_warning_source"] == "structural"

    # An unavailable proxy read and a confirmed implementation change are distinct outcomes.
    with monkeypatch.context() as context:
        context.setattr(web3.eth, "get_storage_at", Mock(side_effect=RequestConnectionError("proxy state unavailable")))
        inconclusive = vault.fetch_withdrawal_capacity_limit(REVIEW_BLOCK)
        assert inconclusive.observation["status"] == "failed"
        assert inconclusive.current_capacity is None
        assert "could not be verified" in inconclusive.capacity_reason

    monkeypatch.setattr(web3.eth, "get_storage_at", Mock(return_value=bytes(32)))
    replacement = vault.fetch_withdrawal_capacity_limit(REVIEW_BLOCK)
    assert replacement.current_capacity is None
    assert replacement.capacity_warning is None
    assert replacement.observation["status"] == "unsupported"
    assert replacement.observation["structural_warning"] is False
    assert "vault code changed" in replacement.capacity_reason


@pytest.mark.parametrize(
    "pool_cash,position,capacity,source",
    [
        (0, 500_000_000, "0", "both"),
        (300_000_000, 150_000_000, "150", "structural"),
    ],
)
def test_harvest_capacity_bounds(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pool_cash: int,
    position: int,
    capacity: str,
    source: str,
) -> None:
    """Feed raw contract balances to the verified route's reader and assessor."""
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=8453))
    wrapper = Mock(address=HARVEST_VAULT)
    wrapper.functions.totalAssets.return_value.call.return_value = 1_000_000_000
    wrapper.functions.compoundOnWithdraw.return_value.call.return_value = False
    wrapper.functions.redeemInKindEnabled.return_value.call.return_value = True
    pool = Mock(address=POOL)
    pool.functions.balanceOf.return_value.call.return_value = 500_000_000
    pool.functions.totalAssets.return_value.call.return_value = 2_000_000_000
    pool.functions.convertToAssets.return_value.call.return_value = position
    strategy = Mock(address=STRATEGY)
    usdc = Mock(address=USDC, w3=web3)
    pool_balance = Mock()
    pool_balance.call.return_value = pool_cash
    other_balance = Mock()
    other_balance.call.return_value = 0
    usdc.functions.balanceOf.side_effect = lambda address: pool_balance if address == POOL else other_balance
    token = TokenDetails(contract=usdc, symbol="USDC", decimals=6)
    vault = SimpleNamespace(web3=web3, address=HARVEST_VAULT, vault_contract=wrapper, denomination_token=token)
    contracts = {HARVEST_VAULT: wrapper, STRATEGY: strategy, POOL: pool}
    monkeypatch.setattr(withdrawal_capacity_readers, "get_deployed_contract", lambda _connection, _abi, address: contracts[address])
    monkeypatch.setattr(withdrawal_capacity_readers, "_harvest_route_mismatch", Mock(return_value=None))

    data = withdrawal_capacity_readers.harvest_forty_acres_observation(vault, REVIEW_BLOCK)
    now = datetime.datetime(2026, 11, 26, tzinfo=datetime.UTC)
    result = assess_withdrawal_capacity({**data, "observed_at": now.isoformat()}, now=now)
    assert result.current_capacity == Decimal(capacity)
    assert result.capacity_warning is True
    assert result.observation["capacity_warning_source"] == source
