"""Scan all ERC-4626 vaults onchain"""

import os

import hypersync
import pytest
from joblib import Parallel, delayed
from web3 import Web3

from eth_defi.erc_4626.hypersync_discovery import HypersyncVaultDiscover
from eth_defi.erc_4626.scan import create_vault_scan_record_subprocess
from eth_defi.hypersync.server import get_hypersync_server
from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderWeb3Factory

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

pytestmark = pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run these tests")


@pytest.fixture(scope='module')
def web3() -> Web3:
    web3 = create_multi_provider_web3(JSON_RPC_BASE)
    return web3


def test_4626_scan(web3):
    """Read vaults of early Base chain"""

    web3 = create_multi_provider_web3(JSON_RPC_BASE)
    web3factory = MultiProviderWeb3Factory(JSON_RPC_BASE)

    hypersync_url = get_hypersync_server(web3)
    client = hypersync.HypersyncClient(hypersync.ClientConfig(url=hypersync_url))

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
    vault_detections = list(vault_discover.scan_vaults(start_block, end_block, display_progress=False))

    # Prepare data export by reading further per-vault data using multiprocessing
    worker_processor = Parallel(n_jobs=vault_discover.max_workers)

    # Quite a mouthful line to create a row of output for each vault detection using subproces pool
    rows = worker_processor(delayed(create_vault_scan_record_subprocess)(web3factory, d, end_block) for d in vault_detections)
    rows.sort(key=lambda x: x["Address"])

    assert len(rows) == 24
    assert rows[0]["Name"] == "FARM_BSWAP-LP"
    assert rows[0]["Address"] == "0x127dc157aF74858b36bcca07D5A02ef27Cd442d0"
