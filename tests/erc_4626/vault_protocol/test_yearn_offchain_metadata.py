"""Yearn yDaemon static metadata integration tests.

Set ``RUN_YEARN_OFFCHAIN_METADATA_TEST=1`` to run the optional live GitHub
check.  The remaining tests use a synthetic yDaemon document so they exercise
the classification and cache semantics without a network dependency.
"""

import datetime
import os
from unittest.mock import MagicMock

import pytest

import eth_defi.erc_4626.vault_protocol.yearn.compounder as yearn_compounder_module
import eth_defi.erc_4626.vault_protocol.yearn.offchain_metadata as yearn_metadata
import eth_defi.erc_4626.vault_protocol.yearn.vault as yearn_vault_module
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.cap.vault import CAPVault
from eth_defi.erc_4626.vault_protocol.yearn.compounder import YearnCompounderVault
from eth_defi.erc_4626.vault_protocol.yearn.offchain_metadata import (
    fetch_yearn_vaults_file_for_chain,
    get_yearn_frontend_membership,
)
from eth_defi.erc_4626.vault_protocol.yearn.vault import YearnV3Vault
from eth_defi.research.vault_metrics import apply_bad_flag_check
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import NOT_IN_YEARN_FRONTEND, VaultFlag
from eth_defi.vault.risk import VaultTechnicalRisk

COINFLAKES_VAULT = "0x254bd33e2f62713f893f0842c99e68f855cda315"
OFFICIAL_YEARN_VAULT = "0x1111111111111111111111111111111111111111"


def _make_ydaemon_document() -> dict[str, object]:
    """Build the relevant subset of one yDaemon chain metadata document."""

    return {
        "vaults": {
            COINFLAKES_VAULT: {
                "address": COINFLAKES_VAULT,
                "endorsed": False,
                "metadata": {
                    "isHidden": True,
                    "inclusion": {
                        "isYearn": False,
                    },
                },
            },
            OFFICIAL_YEARN_VAULT: {
                "address": OFFICIAL_YEARN_VAULT,
                "endorsed": True,
                "metadata": {
                    "isHidden": False,
                    "inclusion": {
                        "isYearn": True,
                    },
                },
            },
        },
    }


def test_yearn_static_metadata_is_cached_and_normalised(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cache one complete chain catalogue instead of querying each vault.

    1. Return a yDaemon-shaped source document from the raw GitHub request.
    2. Confirm the result is lowercase address-indexed and persisted locally.
    3. Re-read while fresh and confirm no second network request occurs.
    """

    response = MagicMock()
    response.json.return_value = _make_ydaemon_document()
    monkeypatch.setattr(yearn_metadata.requests, "get", MagicMock(return_value=response))
    now_ = native_datetime_utc_now()

    # 1. Fetch and persist the full chain source document.
    first = fetch_yearn_vaults_file_for_chain(
        1,
        cache_path=tmp_path,
        github_base_url="https://example.invalid/yearn",
        now_=now_,
    )

    # 2. Address normalisation makes checksum and scanner forms equivalent.
    assert first is not None
    assert first[COINFLAKES_VAULT].endorsed is False
    assert first[COINFLAKES_VAULT].is_yearn is False
    assert (tmp_path / "yearn-vaults-1.json").exists()

    # 3. The fresh persistent cache prevents per-vault repeated HTTP calls.
    second = fetch_yearn_vaults_file_for_chain(
        1,
        cache_path=tmp_path,
        github_base_url="https://example.invalid/yearn",
        now_=now_ + datetime.timedelta(hours=1),
    )
    assert second == first
    yearn_metadata.requests.get.assert_called_once_with("https://example.invalid/yearn/1.json", timeout=30)


def test_corrupt_yearn_cache_is_replaced_from_the_source(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refresh a corrupt local metadata cache instead of aborting vault scanning."""

    cache_file = tmp_path / "yearn-vaults-1.json"
    cache_file.write_text("not valid json")
    response = MagicMock()
    response.json.return_value = _make_ydaemon_document()
    monkeypatch.setattr(yearn_metadata.requests, "get", MagicMock(return_value=response))

    vaults = fetch_yearn_vaults_file_for_chain(
        1,
        cache_path=tmp_path,
        github_base_url="https://example.invalid/yearn",
    )

    assert vaults is not None
    assert vaults[OFFICIAL_YEARN_VAULT].endorsed is True
    assert cache_file.read_text() != "not valid json"


def test_yearn_metadata_membership_has_safe_three_way_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a successfully loaded catalogue can classify a vault as unlisted."""

    monkeypatch.setattr(yearn_metadata, "_cached_yearn_vaults", {1: yearn_metadata._parse_yearn_vault_index(_make_ydaemon_document())})

    assert get_yearn_frontend_membership(1, COINFLAKES_VAULT) is False
    assert get_yearn_frontend_membership(1, OFFICIAL_YEARN_VAULT) is True
    assert get_yearn_frontend_membership(1, "0x000000000000000000000000000000000000dead") is False

    # An unavailable source is intentionally distinct from a known missing address.
    monkeypatch.setattr(yearn_metadata, "_cached_yearn_vaults", {})
    monkeypatch.setattr(yearn_metadata, "_yearn_metadata_retry_after", {})
    monkeypatch.setattr(yearn_metadata, "fetch_yearn_vaults_file_for_chain", lambda _chain_id: None)
    assert get_yearn_frontend_membership(1, COINFLAKES_VAULT) is None


def test_unavailable_yearn_metadata_retries_after_a_short_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid repeated per-vault HTTP failures without memoising an outage forever."""

    fetch_metadata = MagicMock(return_value=None)
    monkeypatch.setattr(yearn_metadata, "_cached_yearn_vaults", {})
    monkeypatch.setattr(yearn_metadata, "_yearn_metadata_retry_after", {})
    monkeypatch.setattr(yearn_metadata, "fetch_yearn_vaults_file_for_chain", fetch_metadata)

    assert get_yearn_frontend_membership(1, COINFLAKES_VAULT) is None
    assert get_yearn_frontend_membership(1, OFFICIAL_YEARN_VAULT) is None
    fetch_metadata.assert_called_once_with(1)


def test_unlisted_yearn_vaults_are_blacklisted_in_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unendorsed Yearn vault adapters receive a bad scan flag.

    1. Give both concrete Yearn adapters an authoritative exclusion result.
    2. Confirm each adapter records the dedicated frontend-membership flag.
    3. Confirm the generic metrics bad-flag policy turns that stored flag into
       the hard ``blacklisted`` technical-risk classification.
    """

    monkeypatch.setattr(yearn_vault_module, "get_yearn_frontend_membership", lambda *_args: False)
    monkeypatch.setattr(yearn_compounder_module, "get_yearn_frontend_membership", lambda *_args: False)
    monkeypatch.setattr(ERC4626Vault, "get_flags", lambda _self: set())
    v3_vault = object.__new__(YearnV3Vault)
    v3_vault.spec = VaultSpec(chain_id=1, vault_address=COINFLAKES_VAULT)
    v3_vault.features = {ERC4626Feature.yearn_v3_like}
    compounder_vault = object.__new__(YearnCompounderVault)
    compounder_vault.spec = VaultSpec(chain_id=1, vault_address=COINFLAKES_VAULT)
    compounder_vault.features = {ERC4626Feature.yearn_compounder_like}

    # 1-2. Both concrete adapters capture the dynamic frontend decision.
    flags = v3_vault.get_flags()
    assert flags == {VaultFlag.unofficial}
    assert v3_vault.get_notes() == NOT_IN_YEARN_FRONTEND
    assert compounder_vault.get_flags() == {VaultFlag.unofficial}
    assert compounder_vault.get_notes() == NOT_IN_YEARN_FRONTEND

    # 3. BAD_FLAGS causes the exported technical risk to be hard blacklisted.
    risk, notes, checked_flags = apply_bad_flag_check(
        risk=VaultTechnicalRisk.low,
        notes=v3_vault.get_notes(),
        flags=flags,
    )
    assert risk == VaultTechnicalRisk.blacklisted
    assert notes == NOT_IN_YEARN_FRONTEND
    assert checked_flags == flags


def test_unavailable_yearn_metadata_and_cap_vaults_are_not_blacklisted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unknown metadata and non-Yearn protocol adapters out of this flag.

    A source outage must not change a Yearn vault's existing classification.
    CAP intentionally reuses the Yearn V3 adapter but has its own frontend and
    protocol classification, so it must not be classified from Yearn metadata.
    """

    monkeypatch.setattr(yearn_vault_module, "get_yearn_frontend_membership", lambda *_args: None)
    monkeypatch.setattr(ERC4626Vault, "get_flags", lambda _self: set())
    yearn_vault = object.__new__(YearnV3Vault)
    yearn_vault.spec = VaultSpec(chain_id=1, vault_address=COINFLAKES_VAULT)
    yearn_vault.features = {ERC4626Feature.yearn_v3_like}
    assert yearn_vault.get_flags() == set()

    monkeypatch.setattr(yearn_vault_module, "get_yearn_frontend_membership", lambda *_args: False)
    cap_vault = object.__new__(CAPVault)
    cap_vault.spec = VaultSpec(chain_id=1, vault_address=COINFLAKES_VAULT)
    cap_vault.features = {ERC4626Feature.cap_like}
    assert cap_vault.get_flags() == set()


@pytest.mark.skipif(os.environ.get("RUN_YEARN_OFFCHAIN_METADATA_TEST") != "1", reason="Set RUN_YEARN_OFFCHAIN_METADATA_TEST=1 to run the live yDaemon GitHub check")
def test_live_yearn_static_metadata_confirms_coinflakes_is_not_a_yearn_frontend_vault(tmp_path) -> None:
    """Check the real public yDaemon metadata end-to-end for Coinflakes.

    This deliberately uses an isolated cache so a stale operator cache cannot
    hide an upstream source change.
    """

    vaults = fetch_yearn_vaults_file_for_chain(1, cache_path=tmp_path)

    assert vaults is not None
    metadata = vaults[COINFLAKES_VAULT]
    assert metadata.endorsed is False
    assert metadata.is_yearn is False
