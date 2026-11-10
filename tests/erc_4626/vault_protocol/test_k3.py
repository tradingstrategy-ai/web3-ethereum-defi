"""K3 allowlist, metadata failure and real-provider scanner coverage."""

# Chain IDs and explicit fixture values are intentionally literal.
# ruff: noqa: PLR2004, PLC2701, FBT001

import json
import os
import runpy
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import _get_hardcoded_protocol_features, create_vault_instance, create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.k3.constants import K3_METAVAULTS, K3_VAULTS
from eth_defi.erc_4626.vault_protocol.k3.offchain_metadata import fetch_k3_vault_metadata
from eth_defi.erc_4626.vault_protocol.k3.tags import STRATEGY_TAGS
from eth_defi.erc_4626.vault_protocol.k3.vault import K3Vault
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.research.vault_metrics import export_lifetime_row
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata
from eth_defi.vault.strategy_tag import StrategyTag


#: Independent expectations: Ethereum cbBTC is mapped; Ethereum USDe is not.
EXPECTED_STRATEGY_TAGS = {
    "0x009c02a73706a68e0ae0209235408206e4f53709": {StrategyTag.lending, StrategyTag.carry_trade, StrategyTag.risk_premium_harvest},
    "0x56fb80282bb565de67c196924dda0138e30323be": {StrategyTag.delta_neutral, StrategyTag.lending, StrategyTag.carry_trade, StrategyTag.risk_premium_harvest},
}


@pytest.mark.parametrize("chain,address", sorted(K3_VAULTS))
def test_k3_allowlist(chain: int, address: str) -> None:
    """Only reviewed chain/address pairs select K3; generic execution is blocked."""
    features = _get_hardcoded_protocol_features(address, chain)
    assert features == {ERC4626Feature.k3_like, ERC4626Feature.erc_7540_like, ERC4626Feature.erc_7575_like}
    assert _get_hardcoded_protocol_features(address, 10) is None
    assert _get_hardcoded_protocol_features(address) is None
    vault = create_vault_instance(SimpleNamespace(eth=SimpleNamespace(chain_id=chain)), address, features=features)
    assert isinstance(vault, K3Vault)
    vault.__dict__["k3_metadata"] = None
    assert vault.short_description and "\n" not in vault.short_description
    assert vault.description and "\n\n" in vault.description
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None
    assert vault.get_deposit_manager_capability() is None
    assert not vault.supports_generic_deposit_manager()
    with pytest.raises(NotImplementedError):
        vault.get_deposit_manager()
    with pytest.raises(NotImplementedError):
        vault.is_whitelisted_deposit()
    expected_tags = EXPECTED_STRATEGY_TAGS.get(address)
    assert vault.get_strategy_tags() == expected_tags
    if expected_tags:
        tags = vault.get_strategy_tags()
        tags.clear()
        assert vault.get_strategy_tags() == expected_tags


def test_k3_metadata_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wrong identities and outages cannot populate offchain metadata."""
    chain, address = next(iter(sorted(K3_VAULTS)))
    mock = Mock(side_effect=requests.Timeout("provider timeout"))
    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.k3.offchain_metadata.requests.get", mock)
    assert fetch_k3_vault_metadata(chain, address) is None
    mock.side_effect = None
    mock.return_value = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"network": {"chainId": 8453}})
    assert fetch_k3_vault_metadata(chain, address) is None
    mock.reset_mock()
    assert fetch_k3_vault_metadata(8453, address) is None
    mock.assert_not_called()


def test_k3_metadata_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Accept a matching checksummed identity and trim display-only fields.

    Keep validation covered offline, including the provenance URL, without
    treating the record's historical status or financial fields as live data.

    :param monkeypatch: Replace the registry HTTP request with a validated record.
    :return: ``None`` after checking the descriptive identity.
    """
    address = "0x009c02a73706a68e0aE0209235408206E4F53709"
    payload = {"network": {"chainId": 1}, "contracts": {"wrapperProxy": {"address": address}}, "vault": {"name": " K3 cbBTC Vault ", "symbol": " k3cbBTC "}}
    request = Mock(return_value=SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload))
    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.k3.offchain_metadata.requests.get", request)
    metadata = fetch_k3_vault_metadata(1, address.lower())
    assert metadata is not None
    assert (metadata.name, metadata.symbol) == ("K3 cbBTC Vault", "k3cbBTC")
    assert metadata.source_url == f"https://raw.githubusercontent.com/K3-Capital/k3-vault-contracts/main/deployments/1/{address}/deployment.json"
    request.assert_called_once_with(metadata.source_url, timeout=20)
    payload["contracts"]["wrapperProxy"]["address"] = "0x333333c5DEF8368C3fCc9c378cedc0f709Fa5ECB"
    assert fetch_k3_vault_metadata(1, address) is None


@pytest.mark.parametrize("paused", [False, True])
def test_k3_request_availability(paused: bool) -> None:
    """Request pause state is independent of zero-address settled claim limits."""
    address = next(address for chain, address in K3_VAULTS if chain == 5042)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=5042, call=Mock(return_value=int(paused).to_bytes(32))))
    vault = K3Vault(web3, VaultSpec(5042, address), default_block_identifier=1)
    expected = "New requests are paused" if paused else None
    assert vault.fetch_deposit_closed_reason() == expected
    assert vault.fetch_redemption_closed_reason() == expected
    web3.eth.call.return_value = b""
    with pytest.raises(ValueError, match="paused"):
        vault.fetch_deposit_closed_reason()


@pytest.mark.skipif(not os.environ.get("RUN_K3_LIVE_TEST"), reason="Set RUN_K3_LIVE_TEST=1 for registry and real RPC checks")
@pytest.mark.parametrize("chain,address", sorted(K3_VAULTS))
def test_k3_live_scanner(chain: int, address: str) -> None:
    """Exercise registry or maintained metadata, live RPC and scanner fields."""
    web3 = create_multi_provider_web3(read_json_rpc_url(chain))
    block = web3.eth.block_number
    vault = create_vault_instance_autodetect(web3, address)
    assert isinstance(vault, K3Vault)
    metadata = vault.k3_metadata
    if (chain, address) in K3_METAVAULTS:
        assert metadata is None
    else:
        assert metadata is not None
    if metadata:
        assert vault.name == metadata.name
        assert vault.symbol == metadata.symbol
    now = native_datetime_utc_now()
    detection = ERC4262VaultDetection(chain=chain, address=address, first_seen_at_block=block, first_seen_at=now, features=vault.features, updated_at=now, deposit_count=1, redeem_count=1)
    row = create_vault_scan_record(web3, detection, block, token_cache={})
    assert row["Protocol"] == "K3"
    # Direct reads must succeed: best-effort scanner reads can hide RPC errors.
    assert row["_deposit_closed_reason"] == vault.fetch_deposit_closed_reason()
    assert row["_redemption_closed_reason"] == vault.fetch_redemption_closed_reason()
    assert row["_strategy_tags"] == vault.get_strategy_tags()
    assert row["_short_description"] == vault.short_description
    assert "\n\n" in row["_description"]
    exported = export_lifetime_row(pd.Series({"description": row["_description"], "short_description": row["_short_description"]}))
    decoded = json.loads(json.dumps(exported))
    assert decoded["description"] == row["_description"]
    assert decoded["short_description"] == row["_short_description"]
    assert row["_deposit_manager"] is None
    assert row["_deposit_permission"] == "unknown"


@pytest.mark.parametrize("chain,address", sorted(K3_VAULTS))
def test_k3_metadata_migration(chain: int, address: str) -> None:
    """A repeated refresh preserves notes, prices and unrelated rows."""
    spec = VaultSpec(chain, address)
    original = {"Protocol": "<unknown ERC-7540>", "features": {ERC4626Feature.erc_7540_like}, "_notes": "Retain this note", "NAV": 12, "_deposit_closed_reason": "Max deposit cap reached (maxDeposit=0)"}
    unrelated = VaultSpec(10, address)
    rows = {spec: deepcopy(original), unrelated: {"Name": "Unrelated"}}
    context = SimpleNamespace(metadata=lambda: SimpleNamespace(rows=rows), report=Mock(), commit_metadata=Mock())
    migrate = runpy.run_path(str(Path(__file__).parents[3] / "migration/scripts/057-k3-vault-metadata.py"))["migrate"]
    migrate(context)
    once = deepcopy(rows)
    migrate(context)
    assert rows == once
    assert rows[spec]["Protocol"] == "K3"
    assert rows[spec]["_notes"] == original["_notes"]
    assert rows[spec]["NAV"] == 12
    assert rows[spec]["_deposit_closed_reason"] is None
    metadata = get_handwritten_vault_metadata(chain, address)
    assert rows[spec]["_description"] == metadata.description
    assert rows[spec]["_short_description"] == metadata.short_description
    assert rows[spec]["_strategy_tags"] == EXPECTED_STRATEGY_TAGS.get(address)
    if address in EXPECTED_STRATEGY_TAGS:
        assert rows[spec]["_strategy_tags"] is not STRATEGY_TAGS[address]
    assert rows[unrelated] == {"Name": "Unrelated"}


@pytest.mark.parametrize("chain,address", sorted(K3_METAVAULTS))
def test_k3_metavault_uses_maintained_metadata(chain: int, address: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Absence from the JSON registry is deliberate, without failed network calls."""
    request = Mock()
    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.k3.offchain_metadata.requests.get", request)
    assert fetch_k3_vault_metadata(chain, address) is None
    request.assert_not_called()
