"""Turn vault discoveries to human-readable and machine-readable tables."""

import datetime
import logging
import threading
from collections.abc import Callable
from decimal import Decimal
from typing import TypeVar

from eth_abi.exceptions import DecodingError
from requests.exceptions import HTTPError, RequestException
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError, MismatchedABI, Web3Exception, Web3RPCError, Web3ValueError
from web3.types import BlockIdentifier

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import get_vault_protocol_name, is_lending_protocol
from eth_defi.erc_4626.discovery_base import ERC4262VaultDetection
from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import MorphoV1Vault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultBase
from eth_defi.vault.fee import BROKEN_FEE_DATA, FeeData

logger = logging.getLogger(__name__)

OptionalVaultRead = TypeVar("OptionalVaultRead")
ACTIVITY_STATUS_MIN_NAV = Decimal("5000")
OPTIONAL_READ_EXCEPTIONS = (AttributeError, NotImplementedError, ValueError)
BEST_EFFORT_READ_EXCEPTIONS = (
    *OPTIONAL_READ_EXCEPTIONS,
    ConnectionError,
    KeyError,
    RuntimeError,
    TimeoutError,
    TypeError,
    DecodingError,
    BadFunctionCallOutput,
    ContractLogicError,
    ExtraValueError,
    MismatchedABI,
    RequestException,
    Web3Exception,
    Web3RPCError,
    Web3ValueError,
)
ROW_READ_EXCEPTIONS = (
    *BEST_EFFORT_READ_EXCEPTIONS,
    AssertionError,
    ArithmeticError,
)


def _optional_vault_read(reader: Callable[[], OptionalVaultRead]) -> OptionalVaultRead | None:
    """Run a best-effort vault metadata read.

    Scan rows include optional fields that not every :py:class:`VaultBase`
    adapter supports. Treat only missing or explicitly unsupported adapter
    methods as empty values; unexpected exceptions still flow to
    :py:func:`create_vault_scan_record` and mark the vault as broken.

    :param reader:
        Zero-argument callable that reads one optional vault field.

    :return:
        Reader value, or ``None`` when the field is unavailable.
    """

    try:
        return reader()
    except OPTIONAL_READ_EXCEPTIONS:
        return None


def _best_effort_vault_read(reader: Callable[[], OptionalVaultRead]) -> OptionalVaultRead | None:
    """Run a non-critical vault metadata read.

    Activity status and lending-liquidity fields are scan-row adornments. Before
    the Mellow unification, these reads were fully best-effort and a failed RPC
    call only left the individual field empty. Preserve that behaviour so a
    transient or protocol-specific failure does not mark the whole vault row
    broken.

    :param reader:
        Zero-argument callable that reads one non-critical vault field.

    :return:
        Reader value, or ``None`` when the field cannot be read.
    """

    try:
        return reader()
    except BEST_EFFORT_READ_EXCEPTIONS as e:
        # Preserve the legacy field-level isolation for optional activity and
        # lending reads. These calls are not required to identify or price a
        # vault, and protocol adapters have historically raised heterogeneous
        # RPC/decode/runtime errors here.
        logger.debug(
            "Ignored failed non-critical vault read %s: %s",
            getattr(reader, "__qualname__", repr(reader)),
            e,
            exc_info=True,
        )
        return None


def _fetch_total_assets(vault: VaultBase, block_identifier: BlockIdentifier) -> Decimal | None:
    """Fetch vault assets through the shared scan contract.

    ERC-4626 vaults expose ``totalAssets()`` through
    :py:meth:`eth_defi.erc_4626.vault.ERC4626Vault.fetch_total_assets`.
    Other :py:class:`eth_defi.vault.base.VaultBase` adapters, like Mellow, may
    not have that method. In those cases we fall back to ``fetch_nav()`` so all
    smart-contract vault protocols can use the same scan-record path.

    :param vault:
        Vault adapter instance.

    :param block_identifier:
        Block number or tag for the read.

    :return:
        Human-readable denomination-token TVL, or ``None`` if unavailable.
    """

    total_assets = _optional_vault_read(lambda: vault.fetch_total_assets(block_identifier))

    # For vaults without an ERC-4626 totalAssets() method, fall back to
    # fetch_nav(). Protocol adapters can still expose comparable TVL through
    # fetch_total_assets(); Mellow does this as share_price * total_supply, while
    # ForgeYields uses fetch_nav() because its canonical TVL is off-chain.
    if total_assets is None:
        total_assets = _optional_vault_read(lambda: vault.fetch_nav(block_identifier))

    return total_assets


def _fetch_total_supply(vault: VaultBase, block_identifier: BlockIdentifier) -> Decimal | None:
    """Fetch vault share supply if the adapter supports it.

    :param vault:
        Vault adapter instance.

    :param block_identifier:
        Block number or tag for the read.

    :return:
        Human-readable share supply, or ``None`` if unavailable.
    """

    return _optional_vault_read(lambda: vault.fetch_total_supply(block_identifier))


def _export_denomination_token(vault: VaultBase) -> dict | None:
    """Export denomination token metadata for scan rows.

    :param vault:
        Vault adapter instance.

    :return:
        Token metadata dictionary, or ``None`` if the vault does not expose a
        denomination token.
    """

    token = vault.denomination_token
    if token is None:
        return None

    exported_token = token.export()
    assert isinstance(exported_token, dict), f"Got {exported_token}"
    return exported_token


def _fetch_activity_status(vault: VaultBase, total_assets: Decimal | None) -> dict[str, object]:
    """Fetch deposit and redemption status fields for sizeable vaults.

    :param vault:
        Vault adapter instance.

    :param total_assets:
        Human-readable denomination-token TVL.

    :return:
        Private scan-row status fields.
    """

    status = {
        "_deposit_closed_reason": None,
        "_redemption_closed_reason": None,
        "_deposit_next_open": None,
        "_redemption_next_open": None,
    }

    if total_assets is None or total_assets <= ACTIVITY_STATUS_MIN_NAV:
        return status

    status["_deposit_closed_reason"] = _best_effort_vault_read(vault.fetch_deposit_closed_reason)
    status["_redemption_closed_reason"] = _best_effort_vault_read(vault.fetch_redemption_closed_reason)
    status["_deposit_next_open"] = _best_effort_vault_read(vault.fetch_deposit_next_open)
    status["_redemption_next_open"] = _best_effort_vault_read(vault.fetch_redemption_next_open)

    return status


def _fetch_lending_stats(
    vault: VaultBase,
    detection: ERC4262VaultDetection,
    total_assets: Decimal | None,
    block_identifier: BlockIdentifier,
) -> dict[str, object]:
    """Fetch lending-specific row fields for lending protocols.

    :param vault:
        Vault adapter instance.

    :param detection:
        Vault detection metadata with protocol feature flags.

    :param total_assets:
        Human-readable denomination-token TVL.

    :param block_identifier:
        Block number or tag for the read.

    :return:
        Private scan-row lending fields.
    """

    stats = {
        "_available_liquidity": None,
        "_utilisation": None,
    }

    if not is_lending_protocol(detection.features) or total_assets is None or total_assets <= ACTIVITY_STATUS_MIN_NAV:
        return stats

    stats["_available_liquidity"] = _best_effort_vault_read(lambda: vault.fetch_available_liquidity(block_identifier))
    stats["_utilisation"] = _best_effort_vault_read(lambda: vault.fetch_utilisation_percent(block_identifier))

    return stats


def _normalise_scan_note(notes: str | None, description: str | None, short_description: str | None) -> str | None:
    """Drop scan notes that only duplicate description fields.

    Some protocol adapters use :py:meth:`VaultBase.get_notes` as a generic
    "extra text" hook and fall back to the same off-chain description already
    exported as ``_description``. Keep distinct notes, warnings and source
    links, but avoid populating ``_notes`` with duplicate description text.

    :param notes:
        Raw note from the vault adapter.
    :param description:
        Full vault description already exported separately.
    :param short_description:
        Short vault description already exported separately.
    :return:
        Distinct note, or ``None`` if it duplicates description text.
    """

    if not notes:
        return None

    if notes in {description, short_description}:
        return None

    return notes


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

    empty_record = {
        "Symbol": "",
        "Name": "",
        "Address": detection.address,
        "Protocol": "<unknown>",
        "Denomination": "",
        "Share token": "",
        "NAV": 0,
        "Peak NAV": 0,
        "Mgmt fee": None,
        "Perf fee": None,
        "Shares": 0,
        "Features": "",
        "features": set(detection.features),
        "First seen": detection.first_seen_at,
        "Link": None,
        "_detection_data": detection,
        "_fees": None,
        "_flags": {},
        "_notes": None,
        "_deposit_manager": None,
    }

    vault = create_vault_instance(
        web3,
        detection.address,
        detection.features,
        token_cache=token_cache,
        default_block_identifier=block_identifier,
    )

    if vault is None:
        # Probably not ERC-4626
        return empty_record

    try:
        try:
            fees = vault.get_fee_data()
        except (NotImplementedError, ValueError):
            fees = BROKEN_FEE_DATA

        assert isinstance(fees, FeeData), f"Got {type(fees)}: {fees}"

        total_assets = _fetch_total_assets(vault, block_identifier)
        total_supply = _fetch_total_supply(vault, block_identifier)
        denomination_token = _export_denomination_token(vault)

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

        link = vault.get_link()
        protocol_name = get_vault_protocol_name(detection.features)
        activity_status = _fetch_activity_status(vault, total_assets)
        lending_stats = _fetch_lending_stats(vault, detection, total_assets, block_identifier)
        description = vault.description
        short_description = vault.short_description
        notes = _normalise_scan_note(vault.get_notes(), description, short_description)
        capability_resolver = getattr(vault, "get_deposit_manager_capability", None)
        deposit_manager_capability = capability_resolver() if capability_resolver is not None else None

        data = {
            "Symbol": vault.symbol,
            "Name": vault.name,
            "Address": detection.address,
            "Denomination": vault.denomination_token.symbol if vault.denomination_token else None,
            "Share token": vault.share_token.symbol if vault.share_token else None,
            "NAV": total_assets,
            "Protocol": protocol_name,
            "Mgmt fee": fees.management,
            "Perf fee": fees.performance,
            "Deposit fee": fees.deposit,
            "Withdraw fee": fees.withdraw,
            "Shares": total_supply,
            "First seen": detection.first_seen_at,
            "Features": ", ".join(sorted([f.name for f in detection.features])),
            "features": set(detection.features),
            "Link": link,
            "_detection_data": detection,
            "_denomination_token": denomination_token,
            "_share_token": vault.share_token.export() if vault.share_token else None,
            "_fees": fees,
            "_flags": flags,
            "_lockup": lockup,
            "_description": description,
            "_short_description": short_description,
            "_notes": notes,
            "_manager_name": vault.manager_name,
            "_morpho_offchain_data": vault.morpho_offchain_data if isinstance(vault, (MorphoV1Vault, MorphoV2Vault)) else None,
            "_deposit_manager": deposit_manager_capability.as_initial_public_schema() if deposit_manager_capability else None,
        }
        data.update(activity_status)
        data.update(lending_stats)
        data.update(vault.fetch_scan_record_extra_data())
        return data
    except ExtraValueError:
        # No idea yet
        raise
    except ROW_READ_EXCEPTIONS as e:
        # Final row-level guard: record one broken vault row and continue the
        # wider scan when an adapter or RPC provider fails in an unexpected way.
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
            exc_info=False,
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
