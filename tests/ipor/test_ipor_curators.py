"""Test IPOR Fusion atomist metadata."""

import datetime
import json
from decimal import Decimal
from pathlib import Path

import pytest

import eth_defi.erc_4626.scan as scan_module
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.ipor.curators import get_ipor_vault_atomist, load_ipor_vault_atomists
from eth_defi.erc_4626.vault_protocol.ipor.vault import IPORVault
from eth_defi.feed.sources import load_feeder_metadata
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.curator import CURATORS_DATA_DIR, identify_curator
from eth_defi.vault.fee import FeeData, VaultFeeMode

TAU_PRIME_HELOC = "0xdf8a0d3c90462c4c9b5a8697c119fa67cb84a874"


def test_load_ipor_vault_atomists_lowercases_addresses(tmp_path: Path) -> None:
    """IPOR atomist overlay lookups are case-insensitive for vault addresses."""

    path = tmp_path / "vault_atomists.json"
    path.write_text(
        json.dumps(
            {
                "1:0xDF8A0d3c90462c4c9B5A8697C119fA67cb84a874": "TAU Labs",
            }
        )
    )

    atomists = load_ipor_vault_atomists(path)

    assert atomists[1, TAU_PRIME_HELOC] == "TAU Labs"
    assert get_ipor_vault_atomist(1, "0xDF8A0d3c90462c4c9B5A8697C119fA67cb84a874", path=path) == "TAU Labs"


def test_get_ipor_vault_atomist_returns_none_for_unknown_vault(tmp_path: Path) -> None:
    """Unknown IPOR vaults do not produce a manager name."""

    path = tmp_path / "vault_atomists.json"
    path.write_text("{}")

    assert get_ipor_vault_atomist(1, "0x0000000000000000000000000000000000000000", path=path) is None


def test_ipor_vault_atomist_accessor_uses_overlay() -> None:
    """IPOR vault instances expose the overlay atomist as manager metadata."""

    vault = IPORVault(
        web3=None,
        spec=VaultSpec(
            chain_id=1,
            vault_address="0xDF8A0d3c90462c4c9B5A8697C119fA67cb84a874",
        ),
    )

    assert vault.atomist == "TAU Labs"
    assert vault.manager_name == "TAU Labs"


def test_ipor_atomists_are_declared_on_curator_yaml() -> None:
    """Each committed IPOR atomist has a matching curator YAML metadata field."""

    atomists = sorted(set(load_ipor_vault_atomists().values()))

    for atomist in atomists:
        curator_slug = identify_curator(
            chain_id=1,
            vault_token_symbol="",
            vault_name="Prime HELOC Loop",
            vault_address="0x0000000000000000000000000000000000000000",
            protocol_slug="ipor-fusion",
            manager_name=atomist,
        )
        assert curator_slug is not None, f"IPOR atomist {atomist!r} must resolve to a curator"

        curator_metadata = load_feeder_metadata(CURATORS_DATA_DIR / f"{curator_slug}.yaml")
        assert curator_metadata.get("ipor-atomist") == atomist, f"{curator_slug} must declare ipor-atomist: {atomist}"


class _FakeToken:
    """Minimal token object for scan row tests."""

    symbol = "USDC"

    def export(self) -> dict:
        """Return token metadata."""
        return {"symbol": self.symbol}


class _FakeIPORVault:
    """Minimal vault for testing scan record manager metadata flow."""

    symbol = "primeHELOC"
    name = "Prime HELOC Loop"
    denomination_token = _FakeToken()
    share_token = _FakeToken()
    manager_name = "TAU Labs"
    description = None
    short_description = None

    @staticmethod
    def get_fee_data() -> FeeData:
        """Return deterministic fee data."""
        return FeeData(
            fee_mode=VaultFeeMode.internalised_minting,
            management=0.0,
            performance=0.0,
            deposit=0.0,
            withdraw=0.0,
        )

    @staticmethod
    def fetch_total_assets(_block_identifier: int) -> Decimal:
        """Return deterministic NAV below expensive status-read threshold."""
        return Decimal("1000")

    @staticmethod
    def fetch_total_supply(_block_identifier: int) -> Decimal:
        """Return deterministic share supply."""
        return Decimal("1000")

    @staticmethod
    def get_estimated_lock_up() -> None:
        """No lockup."""
        return None

    @staticmethod
    def get_flags() -> set:
        """No flags."""
        return set()

    @staticmethod
    def get_link() -> str:
        """Return a deterministic vault link."""
        return "https://app.ipor.io/fusion/ethereum/0xdf8a0d3c90462c4c9b5a8697c119fa67cb84a874"


def test_vault_scan_record_sets_manager_name_for_ipor_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    """IPOR atomist flows through scan rows as the vault manager name.

    `vault_metrics.py` later passes ``_manager_name`` to
    :py:func:`eth_defi.vault.curator.identify_curator`, so this checks the
    scanner side of the chain without live RPC calls.
    """

    fake_vault = _FakeIPORVault()

    def create_fake_vault_instance(*_args, **_kwargs) -> _FakeIPORVault:
        """Return fake IPOR vault for scan record creation."""
        return fake_vault

    monkeypatch.setattr(scan_module, "create_vault_instance", create_fake_vault_instance)

    timestamp = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC).replace(tzinfo=None)

    detection = ERC4262VaultDetection(
        chain=1,
        address=TAU_PRIME_HELOC,
        first_seen_at_block=0,
        first_seen_at=timestamp,
        features={ERC4626Feature.ipor_like},
        updated_at=timestamp,
        deposit_count=0,
        redeem_count=0,
    )

    record = scan_module.create_vault_scan_record(
        web3=None,
        detection=detection,
        block_identifier=0,
        token_cache={},
    )

    assert record["Protocol"] == "IPOR Fusion"
    assert record["_manager_name"] == "TAU Labs"
