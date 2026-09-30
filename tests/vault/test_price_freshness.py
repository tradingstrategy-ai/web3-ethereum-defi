"""Offline coverage of real vault price-row freshness decisions."""

import datetime
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pyarrow.parquet as pq
import pytest

from eth_defi.erc_4626.vault import VaultReaderState
from eth_defi.event_reader.multicall_batcher import EncodedCall
from eth_defi.vault.base import VaultHistoricalRead, VaultSpec
from eth_defi.vault.historical import VaultHistoricalReadMulticaller, scan_historical_prices_to_parquet
from eth_defi.vault.scan_all_chains import fetch_current_vault_tvl_usd


@dataclass(slots=True)
class DummyToken:
    """Supply one denomination symbol to the existing TVL converter."""

    symbol: str = "USDC"


@dataclass(slots=True)
class DummyVault:
    """Supply the identity and denomination used by a historical reader."""

    spec: VaultSpec
    denomination_token: DummyToken
    first_seen_at_block: int = 1

    @property
    def address(self) -> str:
        """Return the scanner address."""
        return self.spec.vault_address

    @property
    def vault_address(self) -> str:
        """Return the scanner address."""
        return self.spec.vault_address

    @property
    def chain_id(self) -> int:
        """Return the scanner chain ID."""
        return self.spec.chain_id

    def get_spec(self) -> VaultSpec:
        """Return the identity used by persisted reader states."""
        return self.spec


class DummyReader:
    """Produce fixed genuine source observations without a network provider."""

    def __init__(self, vault: DummyVault, timestamps: list[datetime.datetime], *, contextual: bool = False) -> None:
        """Create a static or contextual reader with constant values.

        :param vault:
            Vault used for identity and USD conversion.
        :param timestamps:
            Real source observation times to offer the sparse filter.
        :param contextual:
            Select the contextual branch when true.
        """
        self.vault = vault
        self.address = vault.address
        self.timestamps = timestamps
        self.uses_contextual_history = contextual
        self.uses_share_price_equivalence = False
        self.first_block = None
        self.reader_state = None if contextual else VaultReaderState(vault)
        if self.reader_state is not None and timestamps:
            self.reader_state.peaked_at = timestamps[0]

    def make_read(self, block_number: int, timestamp: datetime.datetime) -> VaultHistoricalRead:
        """Create a valid constant-value row at the supplied source block.

        :param block_number:
            Actual sampled block number.
        :param timestamp:
            Actual block timestamp.
        :return:
            A genuine historical observation.
        """
        if self.reader_state is not None:
            result = SimpleNamespace(block_identifier=block_number, timestamp=timestamp)
            self.reader_state.on_called(result, total_assets=Decimal(2_000), share_price=Decimal(1))
        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=Decimal(1),
            total_assets=Decimal(2_000),
            total_supply=Decimal(2_000),
            performance_fee=None,
            management_fee=None,
            errors=None,
        )

    def process_result(self, block_number: int, timestamp: datetime.datetime, results: list[object]) -> VaultHistoricalRead:
        """Convert one fake static multicall to a genuine observation.

        :param block_number:
            Sampled block.
        :param timestamp:
            Sampled source timestamp.
        :param results:
            The fake multicall result list.
        :return:
            Constant-value historical row.
        """
        assert len(results) == 1
        return self.make_read(block_number, timestamp)

    def fetch_contextual_historical_reads(self, start_block: int, end_block: int, step: int):
        """Yield event-context observations over the requested range.

        :param start_block:
            Inclusive block boundary.
        :param end_block:
            Exclusive block boundary.
        :param step:
            Unused grid spacing.
        :return:
            Iterator of genuine contextual rows.
        """
        _ = step
        return (self.make_read(block, timestamp) for block, timestamp in enumerate(self.timestamps, start=1) if start_block <= block < end_block)


def make_offline_scan(monkeypatch: pytest.MonkeyPatch, reader: DummyReader) -> VaultHistoricalReadMulticaller:
    """Install a fixed reader and source results on the common scanner.

    :param monkeypatch:
        Pytest patcher for the external reader preparation.
    :param reader:
        Static or contextual dummy reader.
    :return:
        A scanner using no RPC or token-cache network reads.
    """
    scanner = VaultHistoricalReadMulticaller(web3factory=None, supported_quote_tokens=None, enforce_live_freshness=True)
    call = EncodedCall(func_name="price", address=reader.address, data=b"", extra_data={"vault": reader.address})
    monkeypatch.setattr(scanner, "prepare_readers", lambda *_, **__: {reader.address: reader})
    monkeypatch.setattr(scanner, "generate_vault_historical_calls", lambda *_: () if reader.uses_contextual_history else ((call, reader.reader_state),))
    return scanner


def test_low_activity_tvl_probe_uses_reader_conversion() -> None:
    """A low-deposit-count ETH vault can qualify at $1,500 converted TVL."""
    vault = SimpleNamespace(address="0x0000000000000000000000000000000000000005", denomination_token=DummyToken("ETH"), first_seen_at_block=1)
    vault.spec = VaultSpec(1, vault.address)
    vault.vault_address = vault.address

    def make_reader(*, stateful: bool) -> SimpleNamespace:
        """Supply the same conversion state as the live reader."""
        assert stateful
        return SimpleNamespace(reader_state=VaultReaderState(vault))

    vault.get_historical_reader = make_reader
    vault.fetch_nav = lambda: Decimal("0.5")

    assert fetch_current_vault_tvl_usd(vault) == (Decimal(1_500), False)

    vault.denomination_token = DummyToken("UNRECOGNISED")
    assert fetch_current_vault_tvl_usd(vault) == (None, True)


def test_weekly_static_reader_retains_unchanged_real_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a row at each seven-day early deadline with source timestamps."""
    start = datetime.datetime.fromisoformat("2026-01-01T00:00:00")
    days = (0, 6, 7, 14)
    timestamps = [start + datetime.timedelta(days=day) for day in days]
    vault = DummyVault(VaultSpec(1, "0x0000000000000000000000000000000000000001"), DummyToken())
    reader = DummyReader(vault, timestamps)
    scanner = make_offline_scan(monkeypatch, reader)
    calls = [EncodedCall(func_name="price", address=vault.address, data=b"", extra_data={"vault": vault.address})]

    def fake_read_multicall(**_: object):
        """Yield one actual sampled block for each supplied timestamp."""
        for block, timestamp in enumerate(timestamps, start=1):
            result = SimpleNamespace(call=calls[0], block_identifier=block, timestamp=timestamp)
            yield SimpleNamespace(block_number=block, timestamp=timestamp, results=[result])

    rows = list(scanner.read_historical([vault], 1, 5, 1, reader_func=fake_read_multicall))

    assert [row.timestamp for row in rows] == [timestamps[0], timestamps[2], timestamps[3]]
    assert scanner.freshness_rows_written == len(rows) - 1


def test_contextual_reader_retains_unchanged_real_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apply the seven-day fallback to contextual prices with meaningful TVL."""
    start = datetime.datetime.fromisoformat("2026-01-01T00:00:00")
    timestamps = [start + datetime.timedelta(days=day) for day in (0, 6, 7)]
    vault = DummyVault(VaultSpec(1, "0x0000000000000000000000000000000000000002"), DummyToken())
    reader = DummyReader(vault, timestamps, contextual=True)
    scanner = make_offline_scan(monkeypatch, reader)

    rows = list(scanner.read_historical([vault], 1, 4, 1))

    assert [row.timestamp for row in rows] == [timestamps[0], timestamps[2]]
    assert scanner.freshness_rows_written == 1


def test_live_parquet_scan_seeds_prior_timestamp_and_preserves_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An overlapping live rescan keeps older rows and seeds only prior time."""
    vault = DummyVault(VaultSpec(1, "0x0000000000000000000000000000000000000003"), DummyToken())
    start = datetime.datetime.fromisoformat("2026-01-01T00:00:00")
    seen_baselines: list[dict[str, datetime.datetime]] = []

    def fake_read_historical(self: VaultHistoricalReadMulticaller, vaults: list[DummyVault], start_block: int, end_block: int, **_: object):
        """Yield real-looking constant values while recording the prior row."""
        assert vaults == [vault]
        seen_baselines.append(dict(self.last_retained_at))
        reader = DummyReader(vault, [])
        self.readers = {vault.address: reader}
        for block in range(start_block, end_block):
            timestamp = start + datetime.timedelta(days=block - 1)
            row = reader.make_read(block, timestamp)
            self.last_retained_at[vault.address] = timestamp
            yield row

    monkeypatch.setattr(VaultHistoricalReadMulticaller, "read_historical", fake_read_historical)
    web3 = SimpleNamespace(
        eth=SimpleNamespace(chain_id=1, get_block=lambda block: {"timestamp": int((start + datetime.timedelta(days=block - 1)).replace(tzinfo=datetime.UTC).timestamp())}),
    )
    path = tmp_path / "prices.parquet"
    token_cache = SimpleNamespace(filename=tmp_path / "tokens.sqlite")
    kwargs = {
        "output_fname": path,
        "web3": web3,
        "web3factory": None,
        "vaults": [vault],
        "token_cache": token_cache,
        "step": 1,
        "reader_states": {},
        "enforce_live_freshness": True,
    }
    scan_historical_prices_to_parquet(**kwargs, start_block=1, end_block=3)
    scan_historical_prices_to_parquet(**kwargs, start_block=2, end_block=4)

    assert seen_baselines == [{}, {vault.address: start}]
    table = pq.read_table(path, columns=["block_number", "timestamp"])
    assert table["block_number"].to_pylist() == [1, 2, 3]
    assert table["timestamp"].to_pylist() == [start + datetime.timedelta(days=day) for day in range(3)]


def test_stateful_quiet_scan_preserves_last_retained_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Advancing from saved state never deletes an unread continuation block."""
    vault = DummyVault(VaultSpec(1, "0x0000000000000000000000000000000000000004"), DummyToken())
    start = datetime.datetime.fromisoformat("2026-01-01T00:00:00")
    reader_available = True

    def fake_read_historical(self: VaultHistoricalReadMulticaller, vaults: list[DummyVault], start_block: int, end_block: int, **_: object):
        """Yield initial source observations, then an empty live interval."""
        assert vaults == [vault]
        reader = DummyReader(vault, [])
        self.readers = {vault.address: reader} if reader_available else {}
        for block in range(start_block, min(end_block, 3)):
            timestamp = start + datetime.timedelta(days=block - 1)
            row = reader.make_read(block, timestamp)
            self.last_retained_at[vault.address] = timestamp
            yield row

    monkeypatch.setattr(VaultHistoricalReadMulticaller, "read_historical", fake_read_historical)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, get_block=lambda block: {"timestamp": int((start + datetime.timedelta(days=block - 1)).replace(tzinfo=datetime.UTC).timestamp())}))
    path = tmp_path / "prices.parquet"
    kwargs = {
        "output_fname": path,
        "web3": web3,
        "web3factory": None,
        "vaults": [vault],
        "token_cache": SimpleNamespace(filename=tmp_path / "tokens.sqlite"),
        "step": 1,
        "enforce_live_freshness": True,
    }
    first = scan_historical_prices_to_parquet(**kwargs, start_block=1, end_block=3, reader_states={})
    second = scan_historical_prices_to_parquet(**kwargs, end_block=3, reader_states=first["reader_states"])
    stale = scan_historical_prices_to_parquet(**kwargs, end_block=20, reader_states=first["reader_states"], expected_live_vaults={vault.address})
    reader_available = False
    unavailable = scan_historical_prices_to_parquet(**kwargs, end_block=20, reader_states=first["reader_states"], expected_live_vaults={vault.address})

    assert second["start_block"] == first["end_block"]
    assert second["rows_deleted"] == 0
    assert stale["overdue_vaults"] == {vault.address: "no_valid_source_observation"}
    assert unavailable["overdue_vaults"] == {vault.address: "reader_unavailable"}
    assert pq.read_table(path, columns=["block_number"])["block_number"].to_pylist() == [1, 2]


def test_audit_failure_keeps_published_prices_and_continuation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A deterministic final audit failure cannot replay committed samples.

    Save source rows and progress despite an audit-only exception, then resume
    from the returned state and retain every previously published row.
    """
    vault = DummyVault(VaultSpec(1, "0x0000000000000000000000000000000000000005"), DummyToken())
    start = datetime.datetime(2026, 1, 1)

    def fake_read(self: VaultHistoricalReadMulticaller, vaults: list[DummyVault], start_block: int, end_block: int, **_: object):
        reader = DummyReader(vault, [])
        self.readers = {vault.address: reader}
        for block in range(start_block, end_block):
            timestamp = start + datetime.timedelta(days=block - 1)
            self.last_retained_at[vault.address] = timestamp
            yield reader.make_read(block, timestamp)

    def failed_audit(_state: VaultReaderState) -> bool:
        raise AttributeError("injected final audit failure")

    monkeypatch.setattr(VaultHistoricalReadMulticaller, "read_historical", fake_read)
    monkeypatch.setattr(VaultReaderState, "freshness_qualified", property(failed_audit))
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, get_block=lambda block: {"timestamp": int((start + datetime.timedelta(days=block - 1)).replace(tzinfo=datetime.UTC).timestamp())}))
    path = tmp_path / "prices.parquet"
    kwargs = dict(output_fname=path, web3=web3, web3factory=None, vaults=[vault], token_cache=SimpleNamespace(filename=tmp_path / "tokens.sqlite"), step=1, enforce_live_freshness=True)
    first = scan_historical_prices_to_parquet(**kwargs, start_block=1, end_block=3, reader_states={})
    assert first["audit_error"] == "AttributeError: injected final audit failure"
    assert first["reader_states"][vault.spec]["last_block"] == 2
    second = scan_historical_prices_to_parquet(**kwargs, end_block=4, reader_states=first["reader_states"])
    assert second["start_block"] == 3
    assert pq.read_table(path, columns=["block_number"])["block_number"].to_pylist() == [1, 2, 3]
