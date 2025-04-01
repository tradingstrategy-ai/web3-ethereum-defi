"""Turn vault discoveries to human readable rows."""
import threading

from typing import cast

from web3 import Web3
from web3.types import BlockIdentifier

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import get_vault_protocol_name
from eth_defi.erc_4626.hypersync_discovery import ERC4262VaultDetection
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.event_reader.web3factory import Web3Factory


def create_vault_scan_record(
    web3: Web3,
    detection: ERC4262VaultDetection,
    block_identifier: BlockIdentifier,
) -> dict:
    """Create a row in the result table.

    - Connect to the chain to read further vault metadata via JSON-RPC calls

    :return:
        Dict for human-readable tables
    """

    vault = create_vault_instance(web3, detection.address, detection.features)

    if vault is None:
        # Probably not ERC-4626
        data = {
            "Symbol": "",
            "Name": "",
            "Address": detection.address,
            "Protocol": "<unknown>",
            "Denomination": "",
            "NAV": 0,
            "Mgmt fee": "-",
            "Perf fee": "-",
            "Shares": 0,
            "First seen": detection.first_seen_at,
            "_detection_data": detection,
        }
    else:
        # Try to figure out the correct vault subclass
        # to pull out the data like fees
        vault = cast(ERC4626Vault, vault)
        try:
            management_fee = vault.get_management_fee(block_identifier)
            assert type(management_fee) == float, f"Vault {vault} gave {management_fee}"
        except NotImplementedError:
            management_fee = None

        try:
            performance_fee = vault.get_performance_fee(block_identifier)
            assert type(performance_fee) == float
        except NotImplementedError:
            performance_fee = None

        data = {
            "Symbol": vault.symbol,
            "Name": vault.name,
            "Address": detection.address,
            "Denomination": vault.denomination_token.symbol,
            "NAV": vault.fetch_total_assets(block_identifier),
            "Protocol": get_vault_protocol_name(detection.features),
            "Mgmt fee": management_fee,
            "Perf fee": performance_fee,
            "Shares": vault.fetch_total_supply(block_identifier),
            "First seen": detection.first_seen_at,
            "_detection_data": detection,
        }

    return data



_subprocess_web3_cache = threading.local()


def create_vault_scan_record_subprocess(
    web3factory: Web3Factory,
    detection: ERC4262VaultDetection,
    block_number: int,
) -> dict:
    """Process remaining vault data reads using multiprocessing

    - See :py:func:`create_vault_scan_record`
    - Because ``Vault`` classes does reads using Python instance objects in serial manner,
      we want to speed up by doing many vaults parallel
    """

    # We need to build JSON-RPC connection separately in every thread/process
    web3 = getattr(_subprocess_web3_cache, "web3", None)
    if web3 is None:
        web3 = _subprocess_web3_cache.web3 = web3factory()

    return create_vault_scan_record(
        web3,
        detection,
        block_number,
    )