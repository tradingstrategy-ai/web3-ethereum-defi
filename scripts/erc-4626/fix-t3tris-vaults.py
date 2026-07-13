#!/usr/bin/env python3
"""Repair T3tris vault metadata and prices from the T3tris API.

This script is a targeted production repair tool for T3tris vaults.

It avoids deprecated ``RESET_LEADS`` and any whole-chain rediscovery. See the
recommended targeted backfill approach in
``scripts/erc-4626/README-vault-scripts.md#recommended-targeted-backfill-for-new-vault-protocols``.
Instead it uses the T3tris public vault API and a baked API snapshot to:

1. Upsert lead entries only for known T3tris vault addresses.
2. Upsert missing or broken vault metadata rows only for those addresses.
3. Populate historical price data only for those addresses.

The historical price scan is non-destructive for unrelated vaults. Caught-up
vaults are skipped. For the remaining target vaults, the chain scan starts from
the earliest block any selected T3tris vault needs, while parquet deletion
remains scoped to those selected T3tris addresses.

API documentation:

- https://api.t3tris.finance/api/v1/vaults
- https://api.t3tris.finance/api/v1/vaults/{chainId}/{vaultAddress}

Usage:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/erc-4626/fix-t3tris-vaults.py

Useful environment variables:

.. list-table::
   :header-rows: 1

   * - Variable
     - Description
   * - ``DRY_RUN``
     - If ``true``, only print planned work. Default: ``false``.
   * - ``T3TRIS_FETCH_API``
     - Fetch the live T3tris API and prefer it over the baked snapshot. Default: ``true``.
   * - ``T3TRIS_VERIFIED_ONLY``
     - If ``true``, process only API-verified vaults. Default: ``false``.
   * - ``T3TRIS_SCAN_PRICES``
     - If ``false``, update only leads and metadata. Default: ``true``.
   * - ``T3TRIS_REWRITE_TARGETED``
     - If ``true``, rescan each target vault from its first known block and rewrite only
       that vault's rows. Default: ``false``.
   * - ``T3TRIS_REFRESH_EXISTING_METADATA``
     - If ``true``, refresh existing good metadata rows as well as missing or broken rows.
       Default: ``false``.
   * - ``MAX_WORKERS``
     - Historical multicall worker count. Default: ``8``.
   * - ``FREQUENCY``
     - Historical price frequency, ``1h`` or ``1d``. Default: ``1h``.
   * - ``START_BLOCK``
     - Optional global minimum start block override.
   * - ``END_BLOCK``
     - Optional global end block override.
   * - ``VAULT_DB_PATH``
     - Optional metadata DB path. Default: production vault metadata DB.
   * - ``UNCLEANED_PRICE_DATABASE``
     - Optional uncleaned price parquet path. Default: production uncleaned price DB.
   * - ``READER_STATE_DATABASE``
     - Optional reader-state pickle path. Default: production reader state DB.

JSON-RPC URLs are read per chain using the normal ``JSON_RPC_<CHAIN_NAME>``
convention where available.
"""

import datetime
import json
import logging
import os
import pickle  # noqa: S403 - trusted local production reader-state pickle.
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from atomicwrites import atomic_write
from eth_typing import HexAddress
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError, Web3Exception

from eth_defi.chain import CHAIN_NAMES, EVM_BLOCK_TIMES, get_chain_name
from eth_defi.compat import native_datetime_utc_fromtimestamp, native_datetime_utc_now
from eth_defi.erc_4626.classification import create_vault_instance, detect_vault_features
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.env import get_json_rpc_env
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.provider.named import get_provider_name
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultBase, VaultSpec
from eth_defi.vault.historical import ParquetScanResult, pformat_scan_result, scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import DEFAULT_READER_STATE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)

T3TRIS_API_URL = "https://api.t3tris.finance/api/v1/vaults"
USER_AGENT = "web3-ethereum-defi-t3tris-maintenance/1.0"
SUPPORTED_CHAIN_IDS = {42161}

#: Baked snapshot from ``GET https://api.t3tris.finance/api/v1/vaults``.
#:
#: This is a fallback if the API is unavailable and a review aid for operators:
#: the script contains the full EVM vault list known at implementation time.
T3TRIS_VAULT_SNAPSHOT_JSON = """
{
  "vaults": [
    {
      "chainId": 42161,
      "address": "0x271cbb50e0af266bf4a3657e8c5b4b895258d306",
      "name": "Arbitrum - BoLD",
      "createdAtBlock": "477950763",
      "createdAtTs": 1782579606,
      "curatorName": null,
      "verified": false
    },
    {
      "chainId": 42161,
      "address": "0x9984ad74c5fb6bec3888e14b4e453707d3be7f8f",
      "name": "Gami USDC",
      "createdAtBlock": "479257711",
      "createdAtTs": 1782905947,
      "curatorName": "Gami Labs",
      "verified": true
    },
    {
      "chainId": 42161,
      "address": "0x98e43a491a464f0886bc5e57207c340bbed0d01f",
      "name": "First - USDC",
      "createdAtBlock": "473516860",
      "createdAtTs": 1781468480,
      "curatorName": "First Capital",
      "verified": true
    },
    {
      "chainId": 42161,
      "address": "0xc84cc66300e70acd19500f639bcad7d7a8d34ba9",
      "name": "Ellen Capital BTC",
      "createdAtBlock": "481469145",
      "createdAtTs": 1783458649,
      "curatorName": null,
      "verified": true
    }
  ]
}
"""


@dataclass(slots=True)
class T3trisVaultReference:
    """T3tris API vault reference.

    :param chain_id:
        EVM chain id.

    :param address:
        Vault contract address.

    :param name:
        T3tris API display name.

    :param first_seen_at_block:
        Creation block from the T3tris API.

    :param first_seen_at:
        Creation timestamp from the T3tris API, stored as naive UTC.

    :param curator_name:
        T3tris API curator display name.

    :param verified:
        Whether the T3tris frontend marks the vault as verified.
    """

    chain_id: int
    address: HexAddress
    name: str
    first_seen_at_block: int
    first_seen_at: datetime.datetime
    curator_name: str | None
    verified: bool | None

    def get_spec(self) -> VaultSpec:
        """Return the canonical vault spec."""
        return VaultSpec(self.chain_id, self.address.lower())


@dataclass(slots=True)
class ChainRepairResult:
    """Repair counters for one chain."""

    chain_id: int
    lead_upserts: int = 0
    metadata_upserts: int = 0
    metadata_preserved: int = 0
    metadata_failures: int = 0
    price_scans: int = 0
    price_failures: int = 0
    skipped_unsupported: int = 0


def parse_bool_env(name: str, *, default: bool = False) -> bool:
    """Parse a boolean environment variable."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_optional_int_env(name: str) -> int | None:
    """Parse an optional integer environment variable."""
    value = os.environ.get(name)
    if not value:
        return None
    return int(value)


def parse_first_seen_at(value: object) -> datetime.datetime:
    """Parse a T3tris API timestamp.

    :param value:
        ``createdAtTs`` value from the API.

    :return:
        Naive UTC datetime. Uses current UTC time if the API value is missing.
    """
    if isinstance(value, int | float):
        return native_datetime_utc_fromtimestamp(value)
    return native_datetime_utc_now()


def parse_t3tris_payload(payload: dict) -> list[T3trisVaultReference]:
    """Parse a T3tris vault API response.

    :param payload:
        JSON object returned by the T3tris API.

    :return:
        Supported EVM vault references.
    """
    vaults = payload.get("vaults")
    if not isinstance(vaults, list):
        raise ValueError(f"Unexpected T3tris API response shape: {type(payload)}")

    refs = []
    for item in vaults:
        if not isinstance(item, dict):
            continue

        chain_id = item.get("chainId")
        address = item.get("address")
        if not isinstance(chain_id, int) or chain_id not in SUPPORTED_CHAIN_IDS:
            continue
        if not isinstance(address, str) or not Web3.is_address(address):
            continue

        first_seen_at_block = max(1, int(item.get("createdAtBlock") or "1"))
        refs.append(
            T3trisVaultReference(
                chain_id=chain_id,
                address=HexAddress(Web3.to_checksum_address(address)),
                name=item.get("name") or "",
                first_seen_at_block=first_seen_at_block,
                first_seen_at=parse_first_seen_at(item.get("createdAtTs")),
                curator_name=item.get("curatorName"),
                verified=item.get("verified"),
            )
        )

    refs.sort(key=lambda ref: (ref.chain_id, ref.address.lower()))
    return refs


def fetch_t3tris_vaults(timeout: float = 30.0) -> list[T3trisVaultReference]:
    """Fetch the T3tris vault list from the official API.

    :param timeout:
        HTTP timeout in seconds.

    :return:
        API records that look like supported EVM vault addresses.
    """
    request = Request(  # noqa: S310 - constant HTTPS T3tris API endpoint.
        T3TRIS_API_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - constant HTTPS T3tris API endpoint.
        payload = json.load(response)

    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected T3tris API response type: {type(payload)}")

    return parse_t3tris_payload(payload)


def load_t3tris_vault_references() -> list[T3trisVaultReference]:
    """Load live T3tris API vaults, falling back to the baked snapshot."""
    snapshot_refs = parse_t3tris_payload(json.loads(T3TRIS_VAULT_SNAPSHOT_JSON))

    if not parse_bool_env("T3TRIS_FETCH_API", default=True):
        logger.info("Using baked T3tris API snapshot with %d vaults", len(snapshot_refs))
        return snapshot_refs

    try:
        live_refs = fetch_t3tris_vaults()
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError, OSError) as e:
        logger.warning("Could not fetch T3tris API, using baked snapshot: %s", e)
        return snapshot_refs

    snapshot_specs = {ref.get_spec() for ref in snapshot_refs}
    live_specs = {ref.get_spec() for ref in live_refs}
    new_specs = live_specs - snapshot_specs
    removed_specs = snapshot_specs - live_specs
    if new_specs:
        logger.warning("T3tris API has %d vaults not in the baked snapshot: %s", len(new_specs), sorted(map(str, new_specs))[:20])
    if removed_specs:
        logger.warning("Baked T3tris snapshot has %d vaults no longer returned by the API", len(removed_specs))

    logger.info("Fetched %d T3tris vaults from the live API", len(live_refs))
    return live_refs


def filter_references(refs: list[T3trisVaultReference]) -> list[T3trisVaultReference]:
    """Apply operator filters."""
    if not parse_bool_env("T3TRIS_VERIFIED_ONLY", default=False):
        return refs
    return [ref for ref in refs if ref.verified is True]


def get_rpc_env_candidates(chain_id: int) -> list[str]:
    """Get possible JSON-RPC environment variable names for a chain."""
    names = []
    if chain_id in CHAIN_NAMES:
        names.append(get_json_rpc_env(chain_id))
    names.append(f"JSON_RPC_CHAIN_{chain_id}")
    names.append(f"JSON_RPC_{chain_id}")
    return list(dict.fromkeys(names))


def read_rpc_url_for_chain(chain_id: int) -> tuple[str | None, str | None]:
    """Read the JSON-RPC URL for a chain.

    :return:
        Tuple ``(url, env_var_name)``. Both are ``None`` if no env var is set.
    """
    for env_name in get_rpc_env_candidates(chain_id):
        value = os.environ.get(env_name)
        if value:
            return value, env_name
    return None, None


def get_step_for_frequency(chain_id: int, frequency: str) -> int:
    """Convert frequency to block step for a chain."""
    seconds = {
        "1h": 60 * 60,
        "1d": 24 * 60 * 60,
    }[frequency]

    override = os.environ.get(f"BLOCK_TIME_SECONDS_{chain_id}")
    if override:
        block_time = float(override)
    else:
        block_time = float(EVM_BLOCK_TIMES.get(chain_id, 1.0))

    return max(1, int(seconds / block_time))


def load_vault_database(path: Path) -> VaultDatabase:
    """Load or initialise the vault metadata database."""
    if path.exists():
        return VaultDatabase.read(path)
    logger.warning("Vault metadata DB %s does not exist, creating a new DB", path)
    return VaultDatabase()


def load_reader_states(path: Path) -> dict[VaultSpec, dict]:
    """Load existing historical reader states."""
    if not path.exists():
        return {}
    with path.open("rb") as inp:
        return pickle.load(inp)  # noqa: S301 - trusted local production reader-state pickle.


def write_reader_states(path: Path, states: dict[VaultSpec, dict]) -> None:
    """Write historical reader states atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="wb", overwrite=True) as out:
        pickle.dump(states, out)


def fetch_latest_existing_price_blocks(price_path: Path, chain_id: int, addresses: set[str]) -> dict[str, int]:
    """Fetch latest existing price blocks for several vaults in one parquet read.

    :param price_path:
        Existing uncleaned price parquet path.

    :param chain_id:
        Chain id to inspect.

    :param addresses:
        Lowercase vault addresses.

    :return:
        Mapping ``vault address -> latest block number``.
    """
    if not price_path.exists() or not addresses:
        return {}

    table = pq.read_table(price_path, columns=["chain", "address", "block_number"])
    mask = pc.and_(
        pc.equal(table["chain"], chain_id),
        pc.is_in(table["address"], pa.array(sorted(addresses))),
    )
    filtered = table.filter(mask)
    if filtered.num_rows == 0:
        return {}

    latest_blocks: dict[str, int] = {}
    data = filtered.to_pydict()
    for address, block_number in zip(data["address"], data["block_number"], strict=True):
        latest_blocks[address] = max(latest_blocks.get(address, 0), block_number)
    return latest_blocks


def upsert_lead(vault_db: VaultDatabase, ref: T3trisVaultReference) -> bool:
    """Upsert one API-listed T3tris vault into the lead map."""
    spec = ref.get_spec()
    existing = vault_db.leads.get(spec)
    lead = PotentialVaultMatch(
        chain=ref.chain_id,
        address=HexAddress(ref.address.lower()),
        first_seen_at_block=max(1, ref.first_seen_at_block),
        first_seen_at=getattr(existing, "first_seen_at", ref.first_seen_at) if existing else ref.first_seen_at,
        # API-seeded leads did not come from a historical flow-event count.
        # Keep non-zero deposit count so legacy candidate filters treat the
        # operator-curated API list as intentional input.
        deposit_count=max(getattr(existing, "deposit_count", 0) if existing else 0, 1),
        withdrawal_count=getattr(existing, "withdrawal_count", 0) if existing else 0,
    )
    vault_db.leads[spec] = lead
    return existing is None


def create_detection(ref: T3trisVaultReference, features: set[ERC4626Feature], updated_at: datetime.datetime) -> ERC4262VaultDetection:
    """Create an ERC-4626 detection envelope for an API-listed T3tris vault."""
    return ERC4262VaultDetection(
        chain=ref.chain_id,
        address=HexAddress(ref.address.lower()),
        first_seen_at_block=max(1, ref.first_seen_at_block),
        first_seen_at=ref.first_seen_at,
        features=features,
        updated_at=updated_at,
        # API-seeded metadata does not have historical event counts. Use the
        # minimum production threshold so targeted and future price scans do not
        # discard known API vaults before feature-based protocol routing runs.
        deposit_count=5,
        redeem_count=0,
    )


def is_broken_metadata_row(row: dict) -> bool:
    """Check whether a vault metadata row is an RPC failure placeholder."""
    name = row.get("Name") or ""
    return name.startswith("<broken") or (not name and not row.get("Denomination"))


def should_refresh_metadata(vault_db: VaultDatabase, ref: T3trisVaultReference) -> bool:
    """Decide whether to refresh a metadata row."""
    if parse_bool_env("T3TRIS_REFRESH_EXISTING_METADATA", default=False):
        return True

    row = vault_db.rows.get(ref.get_spec())
    if row is None:
        return True

    if is_broken_metadata_row(row):
        return True

    expected_curator_name = (ref.curator_name or "").strip()
    if expected_curator_name:
        current_manager_name = (row.get("_manager_name") or "").strip()
        if current_manager_name.casefold() != expected_curator_name.casefold():
            return True

    return False


def upsert_metadata_row(web3: Web3, vault_db: VaultDatabase, token_cache: TokenDiskCache, ref: T3trisVaultReference, updated_at: datetime.datetime) -> bool:
    """Create or repair one vault metadata row from live chain data."""
    if not should_refresh_metadata(vault_db, ref):
        return False

    features = detect_vault_features(web3, ref.address, verbose=False)
    if ERC4626Feature.t3tris_like not in features:
        raise ValueError(f"Vault {ref.get_spec()} did not classify as T3tris, features were {features}")

    detection = create_detection(ref, features, updated_at)
    row = create_vault_scan_record(web3, detection, web3.eth.block_number, token_cache)
    vault_db.rows[ref.get_spec()] = row
    return True


def create_price_vault(web3: Web3, vault_db: VaultDatabase, token_cache: TokenDiskCache, ref: T3trisVaultReference) -> VaultBase | None:
    """Create a vault reader instance from the metadata DB row."""
    row = vault_db.rows.get(ref.get_spec())
    if row is None:
        return None

    detection = row.get("_detection_data")
    if not isinstance(detection, ERC4262VaultDetection):
        return None
    if ERC4626Feature.t3tris_like not in detection.features:
        return None

    vault = create_vault_instance(web3, ref.address, detection.features, token_cache=token_cache)
    if vault is not None:
        vault.first_seen_at_block = detection.first_seen_at_block
    return vault


def fetch_vault_price_start_block(ref: T3trisVaultReference, latest_existing_blocks: dict[str, int], *, rewrite_targeted: bool) -> int:
    """Get the historical price repair start block for one vault.

    :param ref:
        Target T3tris vault.

    :param latest_existing_blocks:
        Mapping ``vault address -> latest block number`` from the existing
        price parquet.

    :param rewrite_targeted:
        If ``True``, start from the first known API block even when the vault
        already has price rows.

    :return:
        First block that needs scanning for this vault.
    """
    explicit_start_block = parse_optional_int_env("START_BLOCK")
    if explicit_start_block is not None:
        return explicit_start_block

    latest_existing_block = latest_existing_blocks.get(ref.address.lower())
    if rewrite_targeted or latest_existing_block is None:
        return max(1, ref.first_seen_at_block)

    return latest_existing_block + 1


def scan_chain_price_history(  # noqa: PLR0917 - explicit operational script arguments keep the call site auditable.
    web3: Web3,
    json_rpc_url: str,
    token_cache: TokenDiskCache,
    reader_states: dict[VaultSpec, dict],
    refs: list[T3trisVaultReference],
    vaults: list[VaultBase],
    price_path: Path,
    end_block: int | None,
    frequency: str,
    max_workers: int,
    *,
    rewrite_targeted: bool,
) -> ParquetScanResult | None:
    """Scan one chain's T3tris historical prices once.

    The underlying parquet writer deletes and rewrites only rows whose address
    is in ``vault_addresses``. We therefore first drop caught-up vaults, then
    use the earliest required start block across the remaining target vaults
    and scan all of those vaults in one pass for the chain.

    :param web3:
        Web3 connection for the chain.

    :param json_rpc_url:
        RPC URL used to create worker connections.

    :param token_cache:
        Shared token metadata cache.

    :param reader_states:
        Existing reader states. Target vault states are removed for this scan
        to force a targeted backfill from ``start_block``.

    :param refs:
        T3tris vault references that match ``vaults``.

    :param vaults:
        Supported vault reader instances.

    :param price_path:
        Raw historical price parquet path.

    :param end_block:
        End block for this chain scan.

    :param frequency:
        Historical price frequency, ``1h`` or ``1d``.

    :param max_workers:
        Historical multicall worker count.

    :param rewrite_targeted:
        If ``True``, rewrite target vault rows from their first known API
        block.

    :return:
        Parquet scan result, or ``None`` if all target vaults are already
        caught up or unsupported.
    """
    if not vaults:
        return None

    latest_existing_blocks = fetch_latest_existing_price_blocks(price_path, web3.eth.chain_id, {ref.address.lower() for ref in refs})
    selected_refs: list[T3trisVaultReference] = []
    selected_vaults: list[VaultBase] = []
    selected_start_blocks: list[int] = []
    caught_up_count = 0

    for ref, vault in zip(refs, vaults, strict=True):
        vault_start_block = fetch_vault_price_start_block(ref, latest_existing_blocks, rewrite_targeted=rewrite_targeted)
        if end_block is not None and vault_start_block >= end_block:
            caught_up_count += 1
            continue
        selected_refs.append(ref)
        selected_vaults.append(vault)
        selected_start_blocks.append(vault_start_block)

    if not selected_vaults:
        logger.info("Skipping chain %s price scan: %d T3tris vaults already caught up at block %s", web3.eth.chain_id, len(vaults), end_block)
        return None

    if caught_up_count:
        logger.info("Skipping %d caught-up T3tris vaults on chain %s", caught_up_count, web3.eth.chain_id)

    start_block = min(selected_start_blocks)
    latest_start_block = max(selected_start_blocks)
    vault_addresses = {ref.address.lower() for ref in selected_refs}

    if end_block is not None and start_block >= end_block:
        logger.info("Skipping chain %s price scan: %d T3tris vaults already caught up at block %s", web3.eth.chain_id, len(selected_vaults), end_block)
        return None

    logger.info(
        "Scanning %d T3tris vaults on chain %s once from block %d; latest per-vault start=%d, rewrite_targeted=%s",
        len(selected_vaults),
        web3.eth.chain_id,
        start_block,
        latest_start_block,
        rewrite_targeted,
    )

    # Remove only targeted T3tris vault reader states. This prevents stale
    # state from skipping a missing backfill, while preserving every other
    # vault state on this and other chains.
    target_specs = {ref.get_spec() for ref in selected_refs}
    scan_reader_states = {state_spec: state for state_spec, state in reader_states.items() if state_spec not in target_specs}
    hypersync_config = configure_hypersync_from_env(web3)
    result = scan_historical_prices_to_parquet(
        output_fname=price_path,
        web3=web3,
        web3factory=MultiProviderWeb3Factory(json_rpc_url, retries=5),
        vaults=selected_vaults,
        token_cache=token_cache,
        start_block=start_block,
        end_block=end_block,
        step=get_step_for_frequency(refs[0].chain_id, frequency),
        chunk_size=32,
        max_workers=max_workers,
        frequency=frequency,
        reader_states=scan_reader_states,
        hypersync_client=hypersync_config.hypersync_client,
        vault_addresses=vault_addresses,
    )

    reader_states.clear()
    reader_states.update(result["reader_states"])
    logger.info("Price scan result:\n%s", pformat_scan_result(result))
    return result


def repair_chain(  # noqa: PLR0917 - explicit operational script arguments keep the call site auditable.
    chain_id: int,
    refs: list[T3trisVaultReference],
    vault_db: VaultDatabase,
    vault_db_path: Path,
    price_path: Path,
    reader_state_path: Path,
    *,
    dry_run: bool,
) -> ChainRepairResult:
    """Repair all configured T3tris vaults on one chain."""
    result = ChainRepairResult(chain_id=chain_id)
    updated_at = native_datetime_utc_now()

    if chain_id not in CHAIN_NAMES:
        result.skipped_unsupported = len(refs)
        logger.warning(
            "Skipping unsupported T3tris chain %s with %d API vaults: chain is not configured in eth_defi.chain",
            chain_id,
            len(refs),
        )
        return result

    for ref in refs:
        if upsert_lead(vault_db, ref):
            result.lead_upserts += 1

    if dry_run:
        logger.info("Dry run: not writing vault DB for chain %s", chain_id)

    json_rpc_url, env_name = read_rpc_url_for_chain(chain_id)
    if not json_rpc_url:
        logger.warning(
            "Skipping metadata and prices for chain %s (%s): no RPC env set. Tried: %s",
            chain_id,
            get_chain_name(chain_id),
            ", ".join(get_rpc_env_candidates(chain_id)),
        )
        if not dry_run:
            vault_db_path.parent.mkdir(parents=True, exist_ok=True)
            vault_db.write(vault_db_path)
        return result

    web3 = create_multi_provider_web3(json_rpc_url)
    if web3.eth.chain_id != chain_id:
        raise ValueError(f"RPC env {env_name} points to chain {web3.eth.chain_id}, expected {chain_id}")

    logger.info("Repairing %d T3tris vaults on chain %s using %s (%s)", len(refs), chain_id, env_name, get_provider_name(web3.provider))

    token_cache = TokenDiskCache()
    for ref in refs:
        if dry_run:
            continue

        try:
            if upsert_metadata_row(web3, vault_db, token_cache, ref, updated_at):
                result.metadata_upserts += 1
            else:
                result.metadata_preserved += 1
        except (BadFunctionCallOutput, ContractLogicError, ValueError, Web3Exception, TimeoutError, OSError) as e:
            result.metadata_failures += 1
            logger.warning("Could not upsert metadata for %s %s (%s): %s", ref.get_spec(), ref.name, ref.curator_name, e)

    if dry_run:
        return result

    vault_db_path.parent.mkdir(parents=True, exist_ok=True)
    vault_db.write(vault_db_path)
    token_cache.commit()

    if not parse_bool_env("T3TRIS_SCAN_PRICES", default=True):
        return result

    frequency = os.environ.get("FREQUENCY", "1h")
    if frequency not in {"1h", "1d"}:
        raise ValueError(f"Unsupported FREQUENCY: {frequency}")

    max_workers = int(os.environ.get("MAX_WORKERS", "8"))
    end_block = parse_optional_int_env("END_BLOCK")
    if end_block is None:
        end_block = web3.eth.block_number
    rewrite_targeted = parse_bool_env("T3TRIS_REWRITE_TARGETED", default=False)
    reader_states = load_reader_states(reader_state_path)

    price_refs: list[T3trisVaultReference] = []
    price_vaults: list[VaultBase] = []
    for ref in refs:
        vault = create_price_vault(web3, vault_db, token_cache, ref)
        if vault is None:
            result.skipped_unsupported += 1
            logger.info("Skipping price scan for unsupported vault %s %s (%s)", ref.get_spec(), ref.name, ref.curator_name)
            continue
        price_refs.append(ref)
        price_vaults.append(vault)

    if not price_vaults:
        logger.info("Skipping chain %s price scan: no supported T3tris vault readers", chain_id)
        token_cache.commit()
        return result

    try:
        scan_result = scan_chain_price_history(
            web3=web3,
            json_rpc_url=json_rpc_url,
            token_cache=token_cache,
            reader_states=reader_states,
            refs=price_refs,
            vaults=price_vaults,
            price_path=price_path,
            end_block=end_block,
            frequency=frequency,
            max_workers=max_workers,
            rewrite_targeted=rewrite_targeted,
        )
        if scan_result is not None:
            result.price_scans += 1
            write_reader_states(reader_state_path, reader_states)
    except (AssertionError, BadFunctionCallOutput, ContractLogicError, ValueError, Web3Exception, TimeoutError, OSError) as e:
        result.price_failures += 1
        logger.warning("Could not scan prices for chain %s T3tris vault batch: %s", chain_id, e)

    token_cache.commit()
    return result


def main() -> None:
    """Run the targeted T3tris repair."""
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))

    dry_run = parse_bool_env("DRY_RUN", default=False)
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    price_path = Path(os.environ.get("UNCLEANED_PRICE_DATABASE", str(DEFAULT_UNCLEANED_PRICE_DATABASE))).expanduser()
    reader_state_path = Path(os.environ.get("READER_STATE_DATABASE", str(DEFAULT_READER_STATE_DATABASE))).expanduser()

    refs = filter_references(load_t3tris_vault_references())
    if not refs:
        message = "No T3tris vault references selected"
        raise RuntimeError(message)

    logger.info("Selected %d T3tris vaults across %d chains", len(refs), len({ref.chain_id for ref in refs}))
    vault_db = load_vault_database(vault_db_path)

    refs_by_chain: dict[int, list[T3trisVaultReference]] = defaultdict(list)
    for ref in refs:
        refs_by_chain[ref.chain_id].append(ref)

    results = []
    for chain_id, chain_refs in sorted(refs_by_chain.items()):
        results.append(
            repair_chain(
                chain_id=chain_id,
                refs=chain_refs,
                vault_db=vault_db,
                vault_db_path=vault_db_path,
                price_path=price_path,
                reader_state_path=reader_state_path,
                dry_run=dry_run,
            )
        )

    logger.info("T3tris repair summary")
    for result in results:
        logger.info(
            "chain=%s lead_upserts=%d metadata_upserts=%d metadata_preserved=%d metadata_failures=%d price_scans=%d price_failures=%d skipped_unsupported=%d",
            result.chain_id,
            result.lead_upserts,
            result.metadata_upserts,
            result.metadata_preserved,
            result.metadata_failures,
            result.price_scans,
            result.price_failures,
            result.skipped_unsupported,
        )

    failures = sum(result.metadata_failures + result.price_failures for result in results)
    if failures:
        logger.warning("Completed with %d per-vault failures; see logs above", failures)
    else:
        logger.info("All selected T3tris repairs completed")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
