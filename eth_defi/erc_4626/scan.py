"""Turn vault discoveries to human-readable and machine-readable tables."""

import threading
import logging

from typing import cast

from web3 import Web3
from web3.types import BlockIdentifier

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import get_vault_protocol_name
from eth_defi.erc_4626.hypersync_discovery import ERC4262VaultDetection
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.token import TokenDiskCache

logger = logging.getLogger(__name__)


def create_vault_scan_record(
    web3: Web3,
    detection: ERC4262VaultDetection,
    block_identifier: BlockIdentifier,
    token_cache: TokenDiskCache,
) -> dict:
    """Create a row in the result table.

    - Connect to the chain to read further vault metadata via JSON-RPC calls

    :return:
        Dict for human-readable tables, with internal columns prefixed with å underscore
    """

    vault = create_vault_instance(
        web3,
        detection.address,
        detection.features,
        token_cache=token_cache,
    )

    empty_record = {
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

    if vault is None:
        # Probably not ERC-4626
        return empty_record

    try:
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

        try:
            total_assets = vault.fetch_total_assets(block_identifier)
        except ValueError:
            total_assets = None

        try:
            total_supply = vault.fetch_total_supply(block_identifier)
        except ValueError:
            total_supply = None

        if vault.denomination_token is not None:
            denomination_token = vault.denomination_token.export()
            assert type(denomination_token) == dict, f"Got {denomination_token}"
        else:
            denomination_token = None

        data = {
            "Symbol": vault.symbol,
            "Name": vault.name,
            "Address": detection.address,
            "Denomination": vault.denomination_token.symbol if vault.denomination_token else None,
            "NAV": total_assets,
            "Protocol": get_vault_protocol_name(detection.features),
            "Mgmt fee": management_fee,
            "Perf fee": performance_fee,
            "Shares": total_supply,
            "First seen": detection.first_seen_at,
            "_detection_data": detection,
            "_denomination_token": denomination_token,
            "_share_token": vault.share_token.export() if vault.share_token else None,
        }
        return data
    except Exception as e:
        # Probably caused by misdetecting a vault, then we try to call its functions and they return 0x (no data) instead of cleanly reverting
        # Not sure what is causing this
        #  When calling method: eth_call({'to': '0x463DE7D52bF7C6849ab3630Bb6F999eA0e03ED9F', 'from': '0x0000000000000000000000000000000000000000', 'data': '0x31ee80ca', 'gas': '0x1312d00'}, '0x15259fb')
        record = empty_record.copy()
        record["Name"] = f"<broken: {e.__class__.__name__}>"
        logger.warning(
            "Could not read %s %s (%s): %s",
            vault.__class__.__name__,
            detection.address,
            detection.features,
            str(e),
            exc_info=True,
        )
        return record


#: Handle per-process connections and databases
_subprocess_web3_cache = threading.local()


def create_vault_scan_record_subprocess(
    web3factory: Web3Factory,
    detection: ERC4262VaultDetection,
    block_number: int,
) -> dict:
    """Process remaining vault data reads using multiprocessing

    - Runs in a subprocess
    - See :py:func:`create_vault_scan_record`
    - Because ``Vault`` classes does reads using Python instance objects in serial manner,
      we want to speed up by doing many vaults parallel
    """

    # We need to build JSON-RPC connection separately in every thread/process
    web3 = getattr(_subprocess_web3_cache, "web3", None)
    if web3 is None:
        web3 = _subprocess_web3_cache.web3 = web3factory()

    token_cache = getattr(_subprocess_web3_cache, "token_cache", None)
    if token_cache is None:
        token_cache = _subprocess_web3_cache.token_cache = TokenDiskCache()

    record = create_vault_scan_record(
        web3,
        detection,
        block_number,
        token_cache=token_cache,
    )

    return record
