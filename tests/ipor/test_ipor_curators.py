"""Test IPOR Fusion atomist metadata."""

import datetime
from decimal import Decimal
from pathlib import Path

import pytest

import eth_defi.erc_4626.scan as scan_module
import eth_defi.erc_4626.vault_protocol.ipor.offchain_metadata as ipor_metadata
import eth_defi.erc_4626.vault_protocol.ipor.vault as ipor_vault_module
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.ipor.offchain_metadata import (
    fetch_ipor_atomist_names,
    fetch_ipor_vault_atomist,
)
from eth_defi.erc_4626.vault_protocol.ipor.vault import IPORVault
from eth_defi.feed.sources import load_feeder_metadata
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.curator import CURATORS_DATA_DIR, identify_curator
from eth_defi.vault.fee import FeeData, VaultFeeMode

TAU_PRIME_HELOC = "0xdf8a0d3c90462c4c9b5a8697c119fa67cb84a874"


class _FakeResponse:
    """Small ``requests.Response`` substitute for IPOR metadata tests."""

    def __init__(self, text: str = "", payload: dict | None = None) -> None:
        """Create fake response data."""
        self.text = text
        self.payload = payload or {}

    def raise_for_status(self) -> None:
        """No-op for successful fake responses."""

    def json(self) -> dict:
        """Return fake JSON response data."""
        return self.payload


class _FakeEth:
    """Minimal ``web3.eth`` replacement for IPOR metadata tests."""

    def __init__(self, chain_id: int) -> None:
        """Create fake chain metadata."""
        self.chain_id = chain_id


class _FakeWeb3:
    """Minimal Web3 replacement for IPOR metadata tests."""

    def __init__(self, chain_id: int = 1) -> None:
        """Create fake Web3 chain metadata."""
        self.eth = _FakeEth(chain_id)


def test_extract_ipor_frontend_atomists() -> None:
    """IPOR frontend bundle parser extracts address-keyed atomist metadata."""

    bundle = 'prime={...Jt,name:"Prime HELOC Loop",chainId:We.id,address:"0xDF8A0d3c90462c4c9B5A8697C119fA67cb84a874",xatomist:"Wrong Labs",atomist:"TAU Labs",chartColors:{strategy:"byMarket"}};other={...Jt,name:"No manager",address:"0x0000000000000000000000000000000000000000"};'

    atomists = ipor_metadata._extract_ipor_frontend_atomists(bundle)

    assert atomists[TAU_PRIME_HELOC] == "TAU Labs"
    assert "0x0000000000000000000000000000000000000000" not in atomists


def test_fetch_ipor_vault_atomist_fetches_dynamic_manager_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """IPOR atomist lookups come from fetched frontend metadata and local cache."""

    ipor_metadata._cached_frontend_atomists.clear()

    def fake_get(url: str, **_kwargs) -> _FakeResponse:
        """Return deterministic fake IPOR endpoint responses."""
        if url == "https://app.example/fusion":
            return _FakeResponse(text='<script type="module" src="https://cdn.example/assets/vendor-test.js"></script><script type="module" src="/assets/index-test.js"></script>')
        if url == "https://cdn.example/assets/vendor-test.js":
            return _FakeResponse(text="export default {};")
        if url == "https://app.example/assets/index-test.js":
            return _FakeResponse(text=('prime={...Jt,name:"Prime HELOC Loop",chainId:We.id,address:"0xDF8A0d3c90462c4c9B5A8697C119fA67cb84a874",atomist:"TAU Labs"};'))
        raise AssertionError(f"Unexpected URL {url}")

    monkeypatch.setattr(ipor_metadata.requests, "get", fake_get)
    monkeypatch.setattr(ipor_metadata, "fetch_ipor_customisation_list", lambda **_kwargs: {})

    assert (
        fetch_ipor_vault_atomist(
            _FakeWeb3(),
            "0xDF8A0d3c90462c4c9B5A8697C119fA67cb84a874",
            cache_path=tmp_path,
            api_base_url="https://api.example",
            app_base_url="https://app.example",
        )
        == "TAU Labs"
    )
    assert (tmp_path / "ipor_frontend_atomists.json").exists()

    def fail_get(url: str, **_kwargs) -> _FakeResponse:
        """Fail if the in-process frontend cache is not used."""
        raise AssertionError(f"Unexpected second request {url}")

    monkeypatch.setattr(ipor_metadata.requests, "get", fail_get)

    assert (
        fetch_ipor_vault_atomist(
            _FakeWeb3(),
            "0xDF8A0d3c90462c4c9B5A8697C119fA67cb84a874",
            cache_path=tmp_path,
            api_base_url="https://api.example",
            app_base_url="https://app.example",
        )
        == "TAU Labs"
    )


def test_fetch_ipor_vault_atomist_prefers_customisation_curator_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """IPOR API ``curatorName`` overrides the frontend atomist fallback."""

    def fake_get(url: str, **_kwargs) -> _FakeResponse:
        """Return deterministic fake IPOR endpoint responses."""
        raise AssertionError(f"Unexpected frontend fallback fetch {url}")

    monkeypatch.setattr(ipor_metadata.requests, "get", fake_get)
    monkeypatch.setattr(
        ipor_metadata,
        "fetch_ipor_customisation_list",
        lambda **_kwargs: {
            (1, "0xDF8A0d3c90462c4c9B5A8697C119fA67cb84a874"): {
                "chain_id": 1,
                "vault_address": "0xDF8A0d3c90462c4c9B5A8697C119fA67cb84a874",
                "description": None,
                "vault_logo_url": None,
                "curator_name": "TAU Labs",
                "disclaimer_link": None,
                "prospectus_link": None,
            }
        },
    )

    assert fetch_ipor_vault_atomist(_FakeWeb3(), TAU_PRIME_HELOC, cache_path=tmp_path, api_base_url="https://api.example", app_base_url="https://app.example") == "TAU Labs"


def test_fetch_ipor_vault_atomist_caches_empty_customisations(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Empty IPOR API customisation results are cached for the current process."""

    ipor_metadata._cached_customisations.clear()
    ipor_metadata._cached_frontend_atomists.clear()

    call_count = 0

    def fake_fetch_customisations(**_kwargs) -> dict:
        """Return an empty customisation response once."""
        nonlocal call_count
        call_count += 1
        return {}

    monkeypatch.setattr(ipor_metadata, "fetch_ipor_customisation_list", fake_fetch_customisations)
    monkeypatch.setattr(ipor_metadata, "fetch_ipor_frontend_atomists", lambda **_kwargs: {TAU_PRIME_HELOC: "TAU Labs"})

    assert fetch_ipor_vault_atomist(_FakeWeb3(), TAU_PRIME_HELOC, cache_path=tmp_path, api_base_url="https://api.example") == "TAU Labs"
    assert fetch_ipor_vault_atomist(_FakeWeb3(), TAU_PRIME_HELOC, cache_path=tmp_path, api_base_url="https://api.example") == "TAU Labs"
    assert call_count == 1


def test_fetch_ipor_atomist_names_combines_api_and_frontend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """IPOR atomist name audit helper combines both manager metadata sources."""

    monkeypatch.setattr(ipor_metadata, "fetch_ipor_frontend_atomists", lambda **_kwargs: {TAU_PRIME_HELOC: "TAU Labs"})
    monkeypatch.setattr(
        ipor_metadata,
        "fetch_ipor_customisation_list",
        lambda **_kwargs: {
            (1, "0x0000000000000000000000000000000000000000"): {
                "curator_name": "IPOR DAO",
            }
        },
    )

    assert fetch_ipor_atomist_names(cache_path=tmp_path) == {"TAU Labs", "IPOR DAO"}


def test_fetch_ipor_vault_atomist_returns_none_for_unknown_vault(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Unknown IPOR vaults do not produce a manager name."""

    monkeypatch.setattr(ipor_metadata, "fetch_ipor_customisation_list", lambda **_kwargs: {})
    monkeypatch.setattr(ipor_metadata, "fetch_ipor_frontend_atomists", lambda **_kwargs: {})

    assert fetch_ipor_vault_atomist(_FakeWeb3(), "0x0000000000000000000000000000000000000000", cache_path=tmp_path) is None


def test_fetch_ipor_vault_atomist_falls_back_to_frontend_address_map(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Frontend atomist metadata works when IPOR API curator names are missing."""

    monkeypatch.setattr(ipor_metadata, "fetch_ipor_customisation_list", lambda **_kwargs: {})
    monkeypatch.setattr(ipor_metadata, "fetch_ipor_frontend_atomists", lambda **_kwargs: {TAU_PRIME_HELOC: "TAU Labs"})

    assert fetch_ipor_vault_atomist(_FakeWeb3(), TAU_PRIME_HELOC, cache_path=tmp_path) == "TAU Labs"


def test_ipor_vault_atomist_accessor_uses_dynamic_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """IPOR vault instances expose fetched atomist metadata as manager metadata."""

    monkeypatch.setattr(ipor_vault_module, "fetch_ipor_vault_atomist", lambda *_args, **_kwargs: "TAU Labs")

    vault = IPORVault(
        web3=None,
        spec=VaultSpec(
            chain_id=1,
            vault_address="0xDF8A0d3c90462c4c9B5A8697C119fA67cb84a874",
        ),
    )

    assert vault.atomist == "TAU Labs"
    assert vault.manager_name == "TAU Labs"


def test_ipor_atomist_curator_yaml_values_resolve() -> None:
    """Declared IPOR atomist YAML metadata resolves to curator slugs."""

    atomists: list[str] = []
    for path in sorted(CURATORS_DATA_DIR.glob("*.yaml")):
        curator_metadata = load_feeder_metadata(path)
        atomist = curator_metadata.get("ipor-atomist")
        if not atomist:
            continue
        atomists.append(atomist)
        curator_slug = identify_curator(
            chain_id=1,
            vault_token_symbol="",
            vault_name="Prime HELOC Loop",
            vault_address="0x0000000000000000000000000000000000000000",
            protocol_slug="ipor-fusion",
            manager_name=atomist,
        )
        assert curator_slug is not None, f"IPOR atomist {atomist!r} must resolve to a curator"

        resolved_metadata = load_feeder_metadata(CURATORS_DATA_DIR / f"{curator_slug}.yaml")
        assert resolved_metadata.get("ipor-atomist") == atomist, f"{curator_slug} must declare ipor-atomist: {atomist}"

    assert "TAU Labs" in atomists


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

    @staticmethod
    def fetch_scan_record_extra_data() -> dict[str, object]:
        """Return no protocol-specific scan columns."""
        return {}


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
