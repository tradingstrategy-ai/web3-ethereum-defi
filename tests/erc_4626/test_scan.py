"""Scan all ERC-4626 vaults onchain"""

import logging
import os

import hypersync
import pytest
from joblib import Parallel, delayed
from web3 import Web3

from eth_defi.erc_4626.discovery_base import LeadScanReport
from eth_defi.erc_4626.hypersync_discovery import HypersyncVaultDiscover
from eth_defi.erc_4626.lead_scan_core import scan_leads
from eth_defi.erc_4626.rpc_discovery import JSONRPCVaultDiscover
from eth_defi.erc_4626.scan import create_vault_scan_record_subprocess
from eth_defi.hypersync.server import get_hypersync_server
from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderWeb3Factory
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

HYPERSYNC_API_KEY = os.environ.get("HYPERSYNC_API_KEY")

pytestmark = pytest.mark.skipif(JSON_RPC_BASE is None or HYPERSYNC_API_KEY is None, reason="JSON_RPC_BASE and HYPERSYNC_API_KEY needed to run these tests")


@pytest.fixture(scope="module")
def web3() -> Web3:
    web3 = create_multi_provider_web3(JSON_RPC_BASE)
    return web3


def test_4626_scan_hypersync(web3):
    """Read vaults of early Base chain"""

    web3 = create_multi_provider_web3(JSON_RPC_BASE)
    web3factory = MultiProviderWeb3Factory(JSON_RPC_BASE)

    hypersync_url = get_hypersync_server(web3)
    client = hypersync.HypersyncClient(hypersync.ClientConfig(url=hypersync_url, bearer_token=HYPERSYNC_API_KEY))

    start_block = 1
    end_block = 4_000_000

    # Create a scanner that uses web3, HyperSync and subprocesses
    vault_discover = HypersyncVaultDiscover(
        web3,
        web3factory,
        client,
    )

    # Perform vault discovery and categorisation,
    # so we get information which address contains which kind of a vault
    report = vault_discover.scan_vaults(start_block, end_block, display_progress=False)
    assert report.start_block == 1
    assert report.end_block == 4000000
    vault_detections = list(report.detections.values())

    # Prepare data export by reading further per-vault data using multiprocessing
    worker_processor = Parallel(n_jobs=vault_discover.max_workers)

    # Quite a mouthful line to create a row of output for each vault detection using subproces pool
    rows = worker_processor(delayed(create_vault_scan_record_subprocess)(web3factory, d, end_block) for d in vault_detections)
    rows.sort(key=lambda x: x["Address"])

    assert len(rows) == 59
    assert rows[0]["Name"] == "Staked EURA"
    # assert rows[0]["Address"] == "0x127dc157aF74858b36bcca07D5A02ef27Cd442d0".lower()


def test_4626_scan_rpc(web3):
    """Read vaults of early Base chain using raw RPC calls"""

    web3 = create_multi_provider_web3(JSON_RPC_BASE)
    web3factory = MultiProviderWeb3Factory(JSON_RPC_BASE)

    start_block = 2_000_000
    end_block = 2_500_000

    # Create a scanner that uses web3, HyperSync and subprocesses
    vault_discover = JSONRPCVaultDiscover(
        web3,
        web3factory,
    )

    # Perform vault discovery and categorisation,
    # so we get information which address contains which kind of a vault
    report = vault_discover.scan_vaults(start_block, end_block, display_progress=False)
    vault_detections = list(report.detections.values())

    # Prepare data export by reading further per-vault data using multiprocessing
    worker_processor = Parallel(n_jobs=vault_discover.max_workers)

    # Quite a mouthful line to create a row of output for each vault detection using subproces pool
    rows = worker_processor(delayed(create_vault_scan_record_subprocess)(web3factory, d, end_block) for d in vault_detections)
    rows.sort(key=lambda x: x["Address"])

    # Not sure why 8, 13 or 14, Hypersync finds one more? Flaky on Github.
    assert len(rows) >= 8
    assert rows[0]["Name"] == "Based ETH"
    assert rows[0]["Address"] == "0x1f8c0065c464c2580be83f17f5f64dd194358649"
    assert rows[0]["_detection_data"].deposit_count == 1


@pytest.mark.parametrize("backend", ["auto", "hypersync"])
def test_lead_scan_core_hypersync(tmp_path, backend):
    """Test lead scan CLI core, incremental for both Hypersync and RPC scan"""

    logger = logging.getLogger(__name__)

    db_path = tmp_path / "vaults.db"

    report = scan_leads(
        json_rpc_urls=JSON_RPC_BASE,
        vault_db_file=db_path,
        printer=logger.info,
        start_block=2_000_000,
        end_block=2_500_000,
        backend=backend,
        hypersync_api_key=HYPERSYNC_API_KEY,
    )
    assert isinstance(report, LeadScanReport)
    match backend:
        case "auto":
            assert isinstance(report.backend, HypersyncVaultDiscover)
        case "rpc":
            assert isinstance(report.backend, JSONRPCVaultDiscover)

    assert report.new_leads == 14
    assert report.old_leads == 0
    assert report.deposits == 2526
    assert report.withdrawals == 1
    assert report.start_block == 2_000_000
    assert report.end_block == 2_500_000
    assert len(report.leads) == 14
    assert len(report.detections) == 14
    assert len(report.rows) == 14

    db = VaultDatabase.read(db_path)
    assert db.last_scanned_block == {8453: 2500000}

    # Pick one row
    # Drop.sol - not a real vault
    # https://basescan.org/address/0x65fca4426a3dbbafe2b28354ab03821d29b35045#code
    spec = VaultSpec(chain_id=8453, vault_address="0x65fca4426a3dbbafe2b28354ab03821d29b35045")
    row = report.rows[spec]
    assert row["_detection_data"].deposit_count == 1276

    updated_report = scan_leads(
        json_rpc_urls=JSON_RPC_BASE,
        vault_db_file=db_path,
        printer=logger.info,
        end_block=2_800_000,
        hypersync_api_key=HYPERSYNC_API_KEY,
    )
    assert updated_report.start_block == 2_500_001
    assert updated_report.end_block == 2_800_000
    assert isinstance(updated_report, LeadScanReport)
    assert isinstance(updated_report.backend, HypersyncVaultDiscover)
    assert updated_report.new_leads == 5
    assert updated_report.old_leads == 14
    assert updated_report.deposits == 1633


def test_4626_scan_moonwell(web3):
    """Test against good known Moonwell USDC vault on Base.

    Scan NAV at a specific block to know NAV reads are good.
    """

    web3 = create_multi_provider_web3(JSON_RPC_BASE)
    web3factory = MultiProviderWeb3Factory(JSON_RPC_BASE)

    hypersync_url = get_hypersync_server(web3)
    client = hypersync.HypersyncClient(hypersync.ClientConfig(url=hypersync_url, bearer_token=HYPERSYNC_API_KEY))

    start_block = 15_620_448
    end_block = 15_968_629

    # Create a scanner that uses web3, HyperSync and subprocesses
    vault_discover = HypersyncVaultDiscover(
        web3,
        web3factory,
        client,
    )

    # Perform vault discovery and categorisation,
    # so we get information which address contains which kind of a vault
    report = vault_discover.scan_vaults(start_block, end_block, display_progress=False)
    vault_detections = list(report.detections.values())

    # Prepare data export by reading further per-vault data using multiprocessing
    worker_processor = Parallel(n_jobs=vault_discover.max_workers)

    # Quite a mouthful line to create a row of output for each vault detection using subproces pool
    scan_block = 28_698_633
    rows = worker_processor(delayed(create_vault_scan_record_subprocess)(web3factory, d, scan_block) for d in vault_detections)
    rows.sort(key=lambda x: x["Address"])

    assert len(rows) == 155
    moonwell = [r for r in rows if r["Name"] == "Moonwell Flagship USDC"][0]
    assert 29_000_000 < moonwell["NAV"] < 31_000_000
