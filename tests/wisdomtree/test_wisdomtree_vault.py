"""Test WisdomTree WTGXX routing, read-only flows and issuer NAV parsing."""

# ruff: noqa: ARG001, ARG002, DTZ001, PLC2701, PLR6301

import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from eth_defi.erc_4626.classification import _get_hardcoded_protocol_features, create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.tokenised_fund.wisdomtree.constants import ETHEREUM_CHAIN_ID, WTGXX_ETHEREUM
from eth_defi.tokenised_fund.wisdomtree.nav import WisdomTreeAPIError, WisdomTreeNAVPoint, fetch_wisdomtree_nav_history
from eth_defi.tokenised_fund.wisdomtree.vault import WISDOMTREE_RESTRICTED_FLOW_REASON, WisdomTreeVault
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.risk import VaultTechnicalRisk


def test_wisdomtree_hardcoded_classification_is_chain_aware() -> None:
    """Route the reviewed WTGXX deployment only on Ethereum."""

    assert _get_hardcoded_protocol_features(WTGXX_ETHEREUM.token, chain_id=ETHEREUM_CHAIN_ID) == {ERC4626Feature.wisdomtree_like}
    assert _get_hardcoded_protocol_features(WTGXX_ETHEREUM.token, chain_id=8453) is None


def test_wisdomtree_vault_is_read_only() -> None:
    """Do not advertise an incomplete permissioned lifecycle as public flows."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1))
    vault = create_vault_instance(web3, WTGXX_ETHEREUM.token, features={ERC4626Feature.wisdomtree_like})
    assert isinstance(vault, WisdomTreeVault)
    assert vault.fetch_deposit_closed_reason() == WISDOMTREE_RESTRICTED_FLOW_REASON
    assert vault.fetch_redemption_closed_reason() == WISDOMTREE_RESTRICTED_FLOW_REASON
    with pytest.raises(NotImplementedError):
        vault.get_deposit_manager()
    assert vault.get_fee_data().fee_mode == VaultFeeMode.internalised_skimming
    assert vault.get_management_fee("latest") == pytest.approx(0.0025)
    assert get_vault_protocol_name({ERC4626Feature.wisdomtree_like}) == "WisdomTree"
    assert vault.get_risk() == VaultTechnicalRisk.low


def test_wisdomtree_nav_history_uses_documented_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse current/history wrapper responses and retain chronological order."""

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"history": [{"date": "2026-07-02", "nav": "1.00"}, {"asOfDate": "2026-07-01T00:00:00Z", "netAssetValue": 1}]}

    class Session:
        def get(self, *args, **kwargs):
            assert kwargs["params"] == {"ticker": "WTGXX", "history": "true"}
            assert kwargs["headers"]["x-wt-dataspan-key"] == "test-key"
            return Response()

    points = list(fetch_wisdomtree_nav_history("WTGXX", api_key="test-key", session=Session()))
    assert points == [WisdomTreeNAVPoint(datetime.datetime(2026, 7, 1), Decimal("1")), WisdomTreeNAVPoint(datetime.datetime(2026, 7, 2), Decimal("1.00"))]


def test_wisdomtree_nav_requires_explicit_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never silently substitute a one-dollar estimate for official NAV."""

    monkeypatch.delenv("WISDOMTREE_DATASPAN_API_KEY", raising=False)
    with pytest.raises(WisdomTreeAPIError, match="WISDOMTREE_DATASPAN_API_KEY"):
        list(fetch_wisdomtree_nav_history("WTGXX"))
