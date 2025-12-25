"""Turn vault discoveries to human-readable and machine-readable tables."""

import datetime
import threading
import logging

from typing import cast

import pandas as pd
from web3 import Web3
from web3.types import BlockIdentifier

from requests.exceptions import HTTPError

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import get_vault_protocol_name
from eth_defi.erc_4626.discovery_base import ERC4262VaultDetection
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.token import TokenDiskCache
from eth_defi.vault.fee import FeeData, BROKEN_FEE_DATA
from eth_defi.vault.flag import get_vault_special_flags, get_notes

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
        "Share token": "",
        "NAV": 0,
        "Mgmt fee": None,
        "Perf fee": None,
        "Shares": 0,
        "Features": "",
        "First seen": detection.first_seen_at,
        "Link": None,
        "_detection_data": detection,
        "_fees": None,
        "_flags": {},
    }

    if vault is None:
        # Probably not ERC-4626
        return empty_record

    try:
        # Try to figure out the correct vault subclass
        # to pull out the data like fees
        vault = cast(ERC4626Vault, vault)

        try:
            fees = vault.get_fee_data()
        except (NotImplementedError, ValueError) as e:
            fees = BROKEN_FEE_DATA

        assert isinstance(fees, FeeData), f"Got {type(fees)}: {fees}"

        management_fee = fees.management
        performance_fee = fees.performance
        deposit_fee = fees.deposit
        withdraw_fee = fees.withdraw

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

        try:
            lockup = vault.get_estimated_lock_up()
        except ValueError as e:
            logger.error(f"Failed to read lockup for vault {vault} at {detection.address}: {e}", exc_info=e)
            lockup = None

        if lockup is not None:
            assert isinstance(lockup, datetime.timedelta), f"Expected timedelta, got {type(lockup)}: {lockup}"

        # Resolve vault flags from the smart contract state
        try:
            flags = vault.get_flags()
        except ValueError as e:
            logger.error(f"Failed to read flags for vault {vault} at {detection.address}: {e}", exc_info=e)
            flags = {}

        # Resolve vault flags from the smart contract state
        link = vault.get_link()

        protocol_name = get_vault_protocol_name(detection.features)

        data = {
            "Symbol": vault.symbol,
            "Name": vault.name,
            "Address": detection.address,
            "Denomination": vault.denomination_token.symbol if vault.denomination_token else None,
            "Share token": vault.share_token.symbol if vault.share_token else None,
            "NAV": total_assets,
            "Protocol": protocol_name,
            "Mgmt fee": management_fee,
            "Perf fee": performance_fee,
            "Deposit fee": deposit_fee,
            "Withdraw fee": withdraw_fee,
            "Shares": total_supply,
            "First seen": detection.first_seen_at,
            "Features": ", ".join(sorted([f.name for f in detection.features])),
            "Link": link,
            "_detection_data": detection,
            "_denomination_token": denomination_token,
            "_share_token": vault.share_token.export() if vault.share_token else None,
            "_fees": fees,
            "_flags": flags,
            "_lockup": lockup,
        }
        return data
    except ExtraValueError as e:
        # No idea yet
        raise
    except Exception as e:
        extra_message = ""
        if isinstance(e, HTTPError):
            # dRPC brokeness trap.
            # We should not try to process HTTP 400 entries
            if e.response is not None:
                extra_message = e.response.text

        # import ipdb ; ipdb.set_trace()

        # Probably caused by misdetecting a vault, then we try to call its functions and they return 0x (no data) instead of cleanly reverting
        # Not sure what is causing this
        #  When calling method: eth_call({'to': '0x463DE7D52bF7C6849ab3630Bb6F999eA0e03ED9F', 'from': '0x0000000000000000000000000000000000000000', 'data': '0x31ee80ca', 'gas': '0x1312d00'}, '0x15259fb')
        record = empty_record.copy()
        record["Name"] = f"<broken: {e.__class__.__name__}>"
        logger.warning(
            "Could not read %s %s (%s): %s - %s",
            vault.__class__.__name__,
            detection.address,
            detection.features,
            str(e),
            extra_message,
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

    assert isinstance(detection, ERC4262VaultDetection), f"Expected ERC4262VaultDetection, got {type(detection)}"

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
