"""Morpho Blue offchain vault metadata.

- Morpho Blue exposes a public GraphQL API that reports vault-level governance
  warnings (e.g. ``short_timelock``) and market-level risk warnings
  (e.g. ``bad_debt_unrealized``)
- We reverse-engineered the warning schema from the Morpho Blue GraphQL API at
  ``blue-api.morpho.org``
- Vault-level warnings cover governance risk; market-level warnings cover
  financial risk in underlying market allocations
- Two-level caching: disk (24h TTL) + in-process dictionary keyed by
  ``"{cache_path}:{chain_id}:{address}"`` so tests using ``tmp_path`` are
  isolated from each other and from the default production cache

Warning types observed from the API:

**Vault-level:**
- ``short_timelock`` (RED) — timelock below required minimum
- ``deposit_disabled`` (RED) — vault has disabled deposits
- ``not_whitelisted`` (YELLOW) — not on Morpho's curated list
- ``unrecognized_deposit_asset`` (YELLOW) — deposit token not in known-asset registry
- ``invalid_name`` / ``invalid_symbol`` (RED) — naming rule violations
- ``custom`` (RED) — manual warning from Morpho team

**Market-level:**
- ``bad_debt_unrealized`` (RED) — collateral underwater, liquidation not yet done
- ``bad_debt_realized`` (YELLOW) — liquidation done but shortfall socialised to suppliers
- ``oracle_price_derivation`` (RED) — on-chain oracle deviates from off-chain reference
- ``not_whitelisted`` (YELLOW) — market not curated
- ``unrecognized_collateral_asset`` / ``unrecognized_loan_asset`` (YELLOW)

Morpho Blue API documentation:
- `Morpho Blue GraphQL API <https://blue-api.morpho.org/graphql>`__
- `Morpho documentation <https://docs.morpho.org/>`__
"""

import datetime
import json
import logging
from pathlib import Path
from typing import TypedDict

import requests
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.compat import native_datetime_utc_fromtimestamp, native_datetime_utc_now
from eth_defi.disk_cache import DEFAULT_CACHE_ROOT
from eth_defi.types import Percent
from eth_defi.utils import wait_other_writers

#: Where we cache fetched Morpho vault data files
DEFAULT_CACHE_PATH = DEFAULT_CACHE_ROOT / "morpho"

#: Morpho Blue public GraphQL API endpoint
MORPHO_BLUE_GRAPHQL_URL = "https://blue-api.morpho.org/graphql"

logger = logging.getLogger(__name__)

#: GraphQL query to fetch vault-level and market-level warnings for a single vault
_VAULT_WARNINGS_QUERY = """
query MorphoVaultWarnings($address: String!, $chainId: Int!) {
  vaultByAddress(address: $address, chainId: $chainId) {
    address
    warnings {
      type
      level
    }
    state {
      allocation {
        market {
          marketId
          badDebt {
            usd
          }
          realizedBadDebt {
            usd
          }
          warnings {
            type
            level
            metadata {
              __typename
              ... on BadDebtUnrealizedMarketWarningMetadata {
                badDebtUsd
                badDebtShare
              }
              ... on BadDebtRealizedMarketWarningMetadata {
                badDebtUsd
                badDebtShare
              }
            }
          }
        }
        supplyAssetsUsd
      }
    }
  }
}
"""


class MorphoMarketWarning(TypedDict):
    """A warning on a Morpho Blue market that a vault is allocated to.

    Market warnings indicate financial risk in the underlying market allocations.
    The most critical are ``bad_debt_unrealized`` (RED) and ``bad_debt_realized`` (YELLOW).

    - `Morpho Blue market warning types <https://blue-api.morpho.org/graphql>`__
    """

    #: Warning type identifier, snake_case.
    #:
    #: Common values: ``bad_debt_unrealized``, ``bad_debt_realized``,
    #: ``oracle_price_derivation``, ``not_whitelisted``,
    #: ``unrecognized_collateral_asset``, ``unrecognized_loan_asset``.
    type: str

    #: Severity level.
    #:
    #: ``"RED"`` for immediate risk (unrealized bad debt, oracle deviation),
    #: ``"YELLOW"`` for historical/governance risk (realized bad debt, not whitelisted).
    level: str

    #: Morpho Blue market identifier (bytes32 hex string).
    market_id: str

    #: USD value of bad debt for this market, if applicable.
    bad_debt_usd: float | None

    #: Bad debt as a fraction of total market supply (0.0-1.0).
    #:
    #: Example: ``0.0686`` means 6.86% of the market supply is bad debt.
    #: Only present for ``bad_debt_unrealized`` / ``bad_debt_realized`` warnings.
    bad_debt_share: Percent | None


class MorphoVaultData(TypedDict):
    """Offchain warning data for a Morpho vault from the Morpho Blue GraphQL API.

    Fetched from ``https://blue-api.morpho.org/graphql`` via the ``vaultByAddress`` query.

    - ``vault_warnings`` covers vault-level governance risk
    - ``market_warnings`` covers financial risk in underlying market allocations

    Note: for Morpho V2 vaults, the API currently returns ``NOT_FOUND`` so
    ``market_warnings`` will always be empty for those vaults.
    """

    #: Vault-level governance warnings.
    #:
    #: Each entry is a dict with ``type`` (str) and ``level`` (``"RED"`` or ``"YELLOW"``).
    #: Example: ``[{"type": "short_timelock", "level": "RED"}, {"type": "not_whitelisted", "level": "YELLOW"}]``
    vault_warnings: list[dict]

    #: Market-level risk warnings from underlying market allocations.
    #:
    #: Each entry is a :py:class:`MorphoMarketWarning`.
    #: Example: ``[{"type": "bad_debt_unrealized", "level": "RED", "market_id": "0x...", ...}]``
    market_warnings: list[MorphoMarketWarning]


#: In-process cache of fetched vault data.
#:
#: Key: ``"{cache_path}:{chain_id}:{address_lower}"`` — includes cache_path
#: so test fixtures using ``tmp_path`` get isolated entries.
_cached_vault_data: dict[str, MorphoVaultData] = {}

#: Sentinel to distinguish transient API failures from definitively-not-found
_TRANSIENT_ERROR = object()


def _parse_cached_json(file: Path) -> MorphoVaultData | object | None:
    """Read and validate a Morpho vault data JSON cache file.

    :param file:
        Path to the cache file (must exist and be non-empty).

    :return:
        - :py:class:`MorphoVaultData` on a valid cache hit
        - ``None`` if the file is an empty-dict NOT_FOUND marker
        - :py:data:`_TRANSIENT_ERROR` if the file cannot be parsed or has unexpected shape
    """
    try:
        with file.open(encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        logger.warning("Corrupted Morpho cache file %s: %s - ignoring", file, e)
        return _TRANSIENT_ERROR
    if not raw:
        # Empty dict marker written when vault is definitively NOT_FOUND
        return None
    if not isinstance(raw, dict):
        logger.warning("Unexpected Morpho cache shape in %s (%s) - ignoring", file, type(raw).__name__)
        return _TRANSIENT_ERROR
    try:
        return MorphoVaultData(**raw)
    except TypeError as e:
        logger.warning("Could not parse Morpho cache %s: %s - ignoring", file, e)
        return _TRANSIENT_ERROR


def _parse_market_warning(warning: dict, market_id: str) -> MorphoMarketWarning:
    """Parse a market warning dict from the GraphQL response.

    :param warning:
        Raw GraphQL warning dict with ``type``, ``level``, and optional ``metadata``.

    :param market_id:
        Morpho Blue market identifier.

    :return:
        Parsed :py:class:`MorphoMarketWarning`.
    """
    metadata = warning.get("metadata") or {}
    return MorphoMarketWarning(
        type=warning.get("type", ""),
        level=warning.get("level", ""),
        market_id=market_id,
        bad_debt_usd=metadata.get("badDebtUsd"),
        bad_debt_share=metadata.get("badDebtShare"),
    )


def _build_vault_data_from_api_response(raw_vault: dict) -> MorphoVaultData:
    """Build a :py:class:`MorphoVaultData` from the ``vaultByAddress`` GraphQL response.

    :param raw_vault:
        The ``vaultByAddress`` dict from the GraphQL ``data`` field.

    :return:
        Parsed vault data.
    """
    vault_warnings = [{"type": w.get("type", ""), "level": w.get("level", "")} for w in raw_vault.get("warnings") or []]

    market_warnings: list[MorphoMarketWarning] = []
    state = raw_vault.get("state") or {}
    for alloc in state.get("allocation") or []:
        market = alloc.get("market") or {}
        market_id = market.get("marketId", "")
        for w in market.get("warnings") or []:
            market_warnings.append(_parse_market_warning(w, market_id))

    return MorphoVaultData(
        vault_warnings=vault_warnings,
        market_warnings=market_warnings,
    )


def fetch_morpho_vault_data(
    web3: Web3,
    vault_address: HexAddress,
    cache_path: Path = DEFAULT_CACHE_PATH,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = datetime.timedelta(hours=24),
) -> MorphoVaultData | None:
    """Fetch and cache Morpho Blue API offchain warning data for a vault.

    Queries the Morpho Blue public GraphQL API at ``blue-api.morpho.org`` to
    retrieve vault-level governance warnings and market-level risk warnings for
    the vault's underlying market allocations.

    Two-level caching is used:

    1. In-process dict (``_cached_vault_data``) — fastest, lives for the process lifetime
    2. Disk JSON file (24h TTL) — survives process restarts, multiprocess-safe

    Caching policy:

    - **Found**: Full :py:class:`MorphoVaultData` written to disk and in-process cache.
    - **Not found** (``NOT_FOUND`` GraphQL error or null ``vaultByAddress``): empty marker
      ``{}`` written to disk so we don't re-query for 24h.
    - **Transient error** (network failure, rate limit, unexpected GraphQL error): returns
      ``None`` without writing cache so the next call retries.

    Note: Morpho V2 vaults are currently not indexed by the ``vaultByAddress`` query
    and will return ``None`` (NOT_FOUND).

    :param web3:
        Web3 instance (used to determine chain ID and checksum the address).

    :param vault_address:
        Vault contract address.

    :param cache_path:
        Directory for cache files. Default: ``~/.tradingstrategy/cache/morpho/``.

    :param now_:
        Override current UTC time (for testing).

    :param max_cache_duration:
        Cache TTL. Default 24 hours (shorter than Euler/Lagoon's 2 days because
        bad-debt warnings can appear and resolve more quickly).

    :return:
        Vault warning data, or ``None`` if the vault is not indexed by the Morpho
        Blue API or if a transient error occurred.
    """
    chain_id = web3.eth.chain_id
    address_lower = vault_address.lower()
    checksum_address = Web3.to_checksum_address(vault_address)
    cache_key = f"{cache_path}:{chain_id}:{address_lower}"

    # 1. In-process cache hit
    if cache_key in _cached_vault_data:
        return _cached_vault_data[cache_key]

    # 2. Disk cache
    if not now_:
        now_ = native_datetime_utc_now()

    cache_path.mkdir(parents=True, exist_ok=True)
    file = (cache_path / f"morpho_{chain_id}_{address_lower}.json").resolve()

    with wait_other_writers(file):
        if file.exists() and file.stat().st_size > 0:
            age = now_ - native_datetime_utc_fromtimestamp(file.stat().st_mtime)
            if age <= max_cache_duration:
                logger.info(
                    "Using cached Morpho vault data for %s on chain %d from %s (age %s)",
                    checksum_address,
                    chain_id,
                    file,
                    age,
                )
                cached = _parse_cached_json(file)
                if cached is None:
                    # Empty-dict NOT_FOUND marker
                    return None
                if cached is not _TRANSIENT_ERROR:
                    _cached_vault_data[cache_key] = cached  # type: ignore[assignment]
                    return cached  # type: ignore[return-value]

        # 3. Fetch from API
        logger.info("Fetching Morpho vault warnings for %s on chain %d", checksum_address, chain_id)
        result = _query_morpho_api(chain_id, checksum_address)

        if result is _TRANSIENT_ERROR:
            # Transient failure — do not cache, let the next call retry
            return None

        if result is None:
            # Definitively NOT_FOUND — cache empty marker so we don't re-query
            logger.info("Morpho API: vault %s on chain %d is not indexed (NOT_FOUND)", checksum_address, chain_id)
            with file.open("wt") as f:
                json.dump({}, f)
            return None

        # Successful fetch — write to disk and populate in-process cache
        serialisable = {
            "vault_warnings": result["vault_warnings"],
            "market_warnings": [dict(w) for w in result["market_warnings"]],
        }
        with file.open("wt") as f:
            json.dump(serialisable, f, indent=2)
        logger.info("Wrote Morpho vault data cache %s (%d vault warnings, %d market warnings)", file, len(result["vault_warnings"]), len(result["market_warnings"]))

        _cached_vault_data[cache_key] = result
        return result


def _query_morpho_api(chain_id: int, address: str) -> MorphoVaultData | object | None:
    """Execute the GraphQL query against the Morpho Blue API.

    :param chain_id:
        EVM chain ID.

    :param address:
        Checksummed vault address.

    :return:
        - :py:class:`MorphoVaultData` on success
        - ``None`` when the vault is definitively not found (NOT_FOUND error or null data)
        - :py:data:`_TRANSIENT_ERROR` sentinel on transient failures (network, rate limit, etc.)
    """
    payload = {
        "query": _VAULT_WARNINGS_QUERY,
        "variables": {"address": address, "chainId": chain_id},
    }
    try:
        resp = requests.post(
            MORPHO_BLUE_GRAPHQL_URL,
            json=payload,
            timeout=30,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as e:
        logger.warning("Morpho API request failed for %s on chain %d: %s", address, chain_id, e)
        return _TRANSIENT_ERROR
    except json.JSONDecodeError as e:
        logger.warning("Morpho API returned invalid JSON for %s on chain %d: %s", address, chain_id, e)
        return _TRANSIENT_ERROR

    # Check for GraphQL-level errors
    errors = body.get("errors")
    if errors:
        # Only treat NOT_FOUND as definitively absent; everything else is a transient error
        if any(str(err.get("status", "")).upper() == "NOT_FOUND" for err in errors):
            return None
        logger.warning(
            "Morpho API returned unexpected GraphQL errors for %s on chain %d: %s",
            address,
            chain_id,
            errors,
        )
        return _TRANSIENT_ERROR

    data = body.get("data") or {}
    raw_vault = data.get("vaultByAddress")

    if raw_vault is None:
        # data.vaultByAddress is null — vault not indexed
        return None

    return _build_vault_data_from_api_response(raw_vault)
