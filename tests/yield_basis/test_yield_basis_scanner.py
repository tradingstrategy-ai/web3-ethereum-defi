"""Focused scanner wiring tests for YieldBasis products."""

import datetime
import pickle  # noqa: S403 - trusted local reader-state fixture
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_typing import HexAddress
from eth_utils import to_checksum_address
from web3.exceptions import Web3Exception

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import LeadScanReport
from eth_defi.erc_4626.lead_discovery_state import LeadDiscoveryState, create_lead_discovery_signature, get_lead_discovery_state_path, save_lead_discovery_state
from eth_defi.vault import scan_all_chains
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.scan_all_chains import ChainConfig
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.yield_basis.addresses import YIELD_BASIS_ACTIVE_MARKETS, YIELD_BASIS_STABLECOIN
from eth_defi.yield_basis.historical_context import YieldBasisContextPrefillResult
from eth_defi.yield_basis.vault_catalog import YieldBasisMarket, YieldBasisScanPreparation
from eth_defi.yield_basis.vault_sync import YieldBasisCatalogueSyncResult

#: Fixed end block for the isolated price cycle.
SCANNER_END_BLOCK: int = 1_000

#: Shared chain continuation point expected to drive the next context prefill.
CHAIN_READER_BLOCK: int = 900

#: Active non-YieldBasis vault retained when another product is withheld.
UNRELATED_VAULT: HexAddress = to_checksum_address("0x3000000000000000000000000000000000000001")


@dataclass(slots=True)
class _VaultWithoutFeatures:
    """Minimal scanner vault that intentionally has no ``features`` member."""

    #: Primary product address.
    address: HexAddress
    #: Deployment hint assigned by the scanner.
    first_seen_at_block: int | None = None
    #: Optional contextual-history path assigned by the scanner.
    historical_context_path: Path | None = None

    def get_spec(self) -> VaultSpec:
        """Return the common vault identity.

        :return:
            Chain/address key for reader-state selection.
        """

        return VaultSpec(1, self.address)


class _TokenCache:
    """No-op token cache for a network-free scanner test."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def commit(self) -> None:
        """Accept the metadata scanner's explicit cache flush."""

    def close(self) -> None:
        """Accept the metadata scanner's explicit cache close."""


def _detection(address: HexAddress, features: set[ERC4626Feature]) -> ERC4262VaultDetection:
    """Create one active Ethereum metadata detection.

    :param address:
        Vault or LT address.
    :param features:
        Persisted routing features.
    :return:
        Detection accepted by the common price activity filter.
    """

    return ERC4262VaultDetection(
        chain=1,
        address=address.lower(),
        first_seen_at_block=100,
        first_seen_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC).replace(tzinfo=None),
        features=features,
        updated_at=datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC).replace(tzinfo=None),
        deposit_count=100,
        redeem_count=0,
    )


def _preparation() -> YieldBasisScanPreparation:
    """Create one valid reviewed product for scanner wiring tests.

    :return:
        Fixed-block preparation accepted by the metadata and price phases.
    """

    review = YIELD_BASIS_ACTIVE_MARKETS[7]
    product = YieldBasisMarket(
        review=review,
        cryptopool=to_checksum_address("0x1000000000000000000000000000000000000001"),
        amm=to_checksum_address("0x2000000000000000000000000000000000000001"),
        killed=False,
    )
    return YieldBasisScanPreparation(1, SCANNER_END_BLOCK, True, YIELD_BASIS_STABLECOIN, (product,), ())


@pytest.mark.parametrize("metadata_path", ("lead_cache_hit", "post_discovery"))
def test_metadata_scan_reconciles_yield_basis_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    metadata_path: str,
) -> None:
    """Reconcile the validated catalogue on either metadata control path.

    A lead-cache hit refreshes YieldBasis immediately. A cache miss waits for
    generic discovery to write its database, then repairs the reviewed rows.

    :param tmp_path:
        Isolated metadata and lead-state paths.
    :param monkeypatch:
        Pytest patch fixture used to isolate network and token-cache reads.
    :param metadata_path:
        Select the cached or post-discovery metadata path.
    :return:
        None.
    """

    cache_hit = metadata_path == "lead_cache_hit"
    vault_database_path = tmp_path / "vault-metadata-db.pickle"
    if cache_hit:
        VaultDatabase(last_scanned_block={1: SCANNER_END_BLOCK}).write(vault_database_path)

    monkeypatch.setattr(scan_all_chains, "create_multi_provider_web3", lambda *_args, **_kwargs: SimpleNamespace(eth=SimpleNamespace(chain_id=1, block_number=SCANNER_END_BLOCK)))
    monkeypatch.setattr(scan_all_chains, "get_almost_latest_block_number", lambda _web3: SCANNER_END_BLOCK)
    monkeypatch.setattr(scan_all_chains, "TokenDiskCache", _TokenCache)
    monkeypatch.setattr(scan_all_chains, "build_chain_configs", lambda: [ChainConfig(name="Test", env_var="JSON_RPC_TEST", scan_vaults=True)])
    preparation = _preparation()
    monkeypatch.setattr(scan_all_chains, "fetch_yield_basis_scan_preparation", lambda _web3, _block_number: preparation)

    synchronised_cursors = []

    def fake_sync(*, vault_db: VaultDatabase, preparation: YieldBasisScanPreparation, **_kwargs: object) -> YieldBasisCatalogueSyncResult:
        """Capture the database state present at reconciliation time."""

        assert preparation.factory_valid
        synchronised_cursors.append(vault_db.last_scanned_block[1])
        return YieldBasisCatalogueSyncResult(1, 1, 0, 0, ())

    monkeypatch.setattr(scan_all_chains, "fetch_and_sync_yield_basis_vault_catalogue", fake_sync)

    if cache_hit:
        signature, configuration = create_lead_discovery_signature([("Test", "JSON_RPC_TEST")])
        save_lead_discovery_state(
            LeadDiscoveryState(1, signature, configuration, native_datetime_utc_now(), SCANNER_END_BLOCK),
            get_lead_discovery_state_path(tmp_path, 1),
        )
        monkeypatch.setattr(scan_all_chains, "scan_leads", lambda **_kwargs: pytest.fail("lead-cache hit must not run discovery"))
    else:

        def fake_scan_leads(**_kwargs: object) -> LeadScanReport:
            """Write the metadata cursor before post-discovery reconciliation."""

            VaultDatabase(last_scanned_block={1: SCANNER_END_BLOCK}).write(vault_database_path)
            return LeadScanReport(start_block=0, end_block=SCANNER_END_BLOCK)

        monkeypatch.setattr(scan_all_chains, "scan_leads", fake_scan_leads)

    success, metrics = scan_all_chains.scan_vaults_for_chain("https://rpc.example", 1, vault_db_path=vault_database_path)

    assert success is True
    assert synchronised_cursors == [SCANNER_END_BLOCK]
    assert metrics["yield_basis_products"] == 1
    assert metrics["lead_discovery_cache_hit"] is cache_hit


def test_metadata_pre_scan_failure_preserves_existing_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave prior metadata untouched when Factory validation is unavailable.

    :param tmp_path:
        Isolated metadata and lead-state paths.
    :param monkeypatch:
        Pytest patch fixture used to simulate the failed pre-scan.
    :return:
        None.
    """

    review = YIELD_BASIS_ACTIVE_MARKETS[7]
    detection = _detection(review.lt_address, {ERC4626Feature.yield_basis_lt})
    spec = VaultSpec(1, review.lt_address)
    original_row = {"_detection_data": detection, "Name": "Previously healthy YieldBasis row"}
    vault_database_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows={spec: original_row}, last_scanned_block={1: SCANNER_END_BLOCK}).write(vault_database_path)

    monkeypatch.setattr(scan_all_chains, "create_multi_provider_web3", lambda *_args, **_kwargs: SimpleNamespace(eth=SimpleNamespace(chain_id=1, block_number=SCANNER_END_BLOCK)))
    monkeypatch.setattr(scan_all_chains, "get_almost_latest_block_number", lambda _web3: SCANNER_END_BLOCK)
    monkeypatch.setattr(scan_all_chains, "build_chain_configs", lambda: [ChainConfig(name="Test", env_var="JSON_RPC_TEST", scan_vaults=True)])

    def fail_pre_scan(_web3: object, _block_number: int) -> YieldBasisScanPreparation:
        """Simulate a retryable Factory RPC failure."""

        message = "temporary Factory failure"
        raise RuntimeError(message)

    monkeypatch.setattr(scan_all_chains, "fetch_yield_basis_scan_preparation", fail_pre_scan)
    monkeypatch.setattr(scan_all_chains, "fetch_and_sync_yield_basis_vault_catalogue", lambda **_kwargs: pytest.fail("invalid preparation must not be reconciled"))
    monkeypatch.setattr(scan_all_chains, "scan_leads", lambda **_kwargs: pytest.fail("fresh lead cache must not run discovery"))
    signature, configuration = create_lead_discovery_signature([("Test", "JSON_RPC_TEST")])
    save_lead_discovery_state(
        LeadDiscoveryState(1, signature, configuration, native_datetime_utc_now(), SCANNER_END_BLOCK),
        get_lead_discovery_state_path(tmp_path, 1),
    )

    success, metrics = scan_all_chains.scan_vaults_for_chain("https://rpc.example", 1, vault_db_path=vault_database_path)

    persisted = VaultDatabase.read(vault_database_path).rows[spec]
    assert success is True
    assert metrics["yield_basis_products"] == 0
    assert persisted["Name"] == original_row["Name"]


@pytest.mark.parametrize("prefill_fails", (False, True))
def test_price_scan_withholds_by_address_and_resumes_from_chain_reader_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, prefill_fails: bool) -> None:
    """Keep unrelated vaults and resume context from common chain state.

    The unrelated adapter deliberately lacks ``features``. This catches a
    regression where the YieldBasis filter inspected optional adapter state
    instead of removing the known invalid LT addresses directly. Its reader
    state also proves contextual YieldBasis readers do not need private state.

    :param tmp_path:
        Isolated metadata, reader-state and output paths.
    :param monkeypatch:
        Pytest patch fixture used to isolate network and Parquet operations.
    :param prefill_fails:
        Simulate a transient context-prefill error while unrelated vaults
        continue through the common writer.
    :return:
        None.
    """

    valid_review = YIELD_BASIS_ACTIVE_MARKETS[7]
    withheld_review = YIELD_BASIS_ACTIVE_MARKETS[8]
    rows = {}
    for address, features in (
        (valid_review.lt_address, {ERC4626Feature.yield_basis_lt}),
        (withheld_review.lt_address, {ERC4626Feature.yield_basis_lt}),
        (UNRELATED_VAULT, set()),
    ):
        detection = _detection(address, features)
        rows[VaultSpec(1, address)] = {"_detection_data": detection}
    vault_database_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows=rows).write(vault_database_path)

    reader_state_path = tmp_path / "reader-state.pickle"
    with reader_state_path.open("wb") as stream:
        pickle.dump({VaultSpec(1, UNRELATED_VAULT): {"last_block": CHAIN_READER_BLOCK}}, stream)

    product = YieldBasisMarket(
        review=valid_review,
        cryptopool=to_checksum_address("0x1000000000000000000000000000000000000001"),
        amm=to_checksum_address("0x2000000000000000000000000000000000000001"),
        killed=False,
    )
    preparation = YieldBasisScanPreparation(
        chain_id=1,
        block_number=SCANNER_END_BLOCK,
        factory_valid=True,
        stablecoin=YIELD_BASIS_STABLECOIN,
        products=(product,),
        review_required=("market 8 withheld by test",),
    )
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, block_number=SCANNER_END_BLOCK))
    vaults = {address.lower(): _VaultWithoutFeatures(address) for address in (valid_review.lt_address, withheld_review.lt_address, UNRELATED_VAULT)}
    captured: dict[str, object] = {}

    monkeypatch.setattr(scan_all_chains, "create_multi_provider_web3", lambda *_args, **_kwargs: web3)
    monkeypatch.setattr(scan_all_chains, "MultiProviderWeb3Factory", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(scan_all_chains, "TokenDiskCache", _TokenCache)
    monkeypatch.setattr(scan_all_chains, "create_vault_instance", lambda _web3, address, _features, **_kwargs: vaults[address.lower()])
    monkeypatch.setattr(scan_all_chains, "configure_hypersync_from_env", lambda *_args, **_kwargs: SimpleNamespace(hypersync_client=object()))
    monkeypatch.setattr(scan_all_chains, "fetch_yield_basis_scan_preparation", lambda _web3, _block_number: preparation)

    def fake_context_prefill(**kwargs: object) -> YieldBasisContextPrefillResult:
        """Capture the selected scope and optionally fail the prefill.

        :param kwargs:
            Production prefill arguments captured for assertions.
        :return:
            Empty successful result when failure simulation is disabled.
        """

        captured["context_start"] = kwargs["start_block"]
        captured["context_vaults"] = tuple(vault.address.lower() for vault in kwargs["vaults"])
        if prefill_fails:
            message = "temporary archive provider failure"
            raise Web3Exception(message)
        return YieldBasisContextPrefillResult(1, int(kwargs["start_block"]), int(kwargs["end_block"]), 0, 0)

    def fake_price_writer(**kwargs: object) -> dict[str, object]:
        """Return a minimal successful common-writer result."""

        captured["writer_addresses"] = kwargs["vault_addresses"]
        return {"rows_written": 0, "start_block": CHAIN_READER_BLOCK, "end_block": SCANNER_END_BLOCK, "reader_states": {}}

    monkeypatch.setattr(scan_all_chains, "fetch_and_store_yield_basis_historical_context", fake_context_prefill)
    monkeypatch.setattr(scan_all_chains, "scan_historical_prices_to_parquet", fake_price_writer)

    success, metrics = scan_all_chains.scan_prices_for_chain(
        "https://rpc.example",
        max_workers=2,
        frequency="1h",
        vault_db_path=vault_database_path,
        uncleaned_price_path=tmp_path / "prices.parquet",
        reader_state_path=reader_state_path,
        historical_context_path=tmp_path / "context.duckdb",
        end_block=SCANNER_END_BLOCK,
    )

    expected_addresses = {UNRELATED_VAULT.lower()} if prefill_fails else {valid_review.lt_address.lower(), UNRELATED_VAULT.lower()}
    assert success is True
    assert metrics["items_scanned"] == len(expected_addresses)
    assert captured["context_start"] == CHAIN_READER_BLOCK
    assert captured["context_vaults"] == (valid_review.lt_address.lower(),)
    assert captured["writer_addresses"] == expected_addresses
