"""Tests for vault metadata worker JSON-RPC accounting attachment."""

import datetime
import threading
from typing import Any

import pytest

from eth_defi.erc_4626 import scan
from eth_defi.erc_4626.core import ERC4262VaultDetection
from eth_defi.provider.rpcdb import RPCRequestStats


class FakeWeb3:
    """Record accumulator swaps made by the cached worker helper."""

    def __init__(self) -> None:
        self.rpc_request_stats: RPCRequestStats | None = None
        self.attachments: list[RPCRequestStats | None] = []

    def set_rpc_request_stats(self, stats: RPCRequestStats | None) -> None:
        """Attach or detach the current task accumulator."""

        self.rpc_request_stats = stats
        self.attachments.append(stats)


class FakeFactory:
    """Return one cached fake Web3 and expose the phase accumulator."""

    def __init__(self, web3: FakeWeb3, stats: RPCRequestStats) -> None:
        self.web3 = web3
        self.rpc_request_stats = stats

    def __call__(self, _context: Any | None = None) -> FakeWeb3:
        """Return the worker connection."""

        return self.web3


def test_vault_metadata_worker_attaches_and_detaches_phase_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cached metadata worker cannot retain a completed phase accumulator."""

    timestamp = datetime.datetime(2026, 7, 20, tzinfo=datetime.timezone.utc).replace(tzinfo=None)
    detection = ERC4262VaultDetection(
        chain=1,
        address="0x0000000000000000000000000000000000000001",
        first_seen_at_block=1,
        first_seen_at=timestamp,
        features=set(),
        updated_at=timestamp,
        deposit_count=0,
        redeem_count=0,
    )
    stats = RPCRequestStats()
    web3 = FakeWeb3()
    factory = FakeFactory(web3, stats)
    monkeypatch.setattr(scan, "_subprocess_web3_cache", threading.local())
    monkeypatch.setattr(scan, "TokenDiskCache", object)

    def create_record(worker_web3: FakeWeb3, worker_detection: ERC4262VaultDetection, block_number: int, token_cache: object) -> dict:
        """Verify accounting remains attached for the actual metadata read."""

        assert worker_web3.rpc_request_stats is stats
        assert worker_detection is detection
        assert token_cache is not None
        return {"block_number": block_number}

    monkeypatch.setattr(scan, "create_vault_scan_record", create_record)

    result = scan.create_vault_scan_record_subprocess(factory, detection, 100)

    assert result == {"block_number": 100}
    assert web3.attachments == [stats, None]
