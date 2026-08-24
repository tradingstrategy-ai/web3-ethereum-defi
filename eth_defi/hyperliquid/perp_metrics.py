"""Public Hyperliquid vault open-position metric collection."""

import datetime
import logging
import threading
import uuid
from collections.abc import Iterable
from decimal import InvalidOperation
from typing import Any

import duckdb
import requests
from joblib import Parallel, delayed

from eth_defi.compat import native_datetime_utc_now
from eth_defi.hyperliquid.api import PerpClearinghouseState, fetch_perp_clearinghouse_state
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID
from eth_defi.hyperliquid.session import HyperliquidSession
from eth_defi.hyperliquid.vault import VaultSummary
from eth_defi.perp_dex.metrics import (
    PerpVaultAccountObservation,
    PerpVaultIdentity,
    PerpVaultObservationBundle,
    PerpVaultPositionObservation,
    PositionValuationBasis,
    SourcePositionDataStatus,
    create_unavailable_perp_vault_observation_bundle,
)
from eth_defi.perp_dex.storage import write_perp_vault_observation_bundle

logger = logging.getLogger(__name__)


#: ``clearinghouseState`` has an API weight of two, compared with the usual
#: twenty for Hyperliquid ``/info`` endpoints.  Nine requests per second uses
#: 1,080 of the documented 1,200 weight-per-minute, per-IP allowance and
#: leaves 10% capacity for unrelated clients.
#:
#: See https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits
CLEARINGHOUSE_STATE_REQUESTS_PER_SECOND = 9.0


def build_hyperliquid_vault_observation_bundle(
    summary: VaultSummary,
    state: PerpClearinghouseState,
    observed_at: datetime.datetime,
) -> tuple[PerpVaultObservationBundle, dict[str, Any]]:
    """Normalise public ``clearinghouseState`` positions for a vault address.

    Hyperliquid's ``positionValue`` is absolute, while signed ``szi`` carries
    direction.  The adapter signs the value from non-zero size and excludes
    all margin, leverage and liquidation fields from its payload.

    See Hyperliquid's `clearinghouse state documentation
    <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals>`__.

    :param summary:
        Public vault-listing identity and optional account equity.
    :param state:
        Public ``clearinghouseState`` response parsed by the existing API
        client.
    :param observed_at:
        Naive UTC response receipt time.
    :return:
        Common account/position bundle and whitelisted raw payload.
    """
    address = str(summary.vault_address).lower()
    snapshot_id = uuid.uuid4().hex
    positions: list[PerpVaultPositionObservation] = []
    payload_positions: list[dict[str, str]] = []
    for position in state.asset_positions:
        if position.size == 0:
            continue
        absolute_notional = abs(position.position_value)
        if absolute_notional == 0:
            msg = "Hyperliquid non-zero position has zero positionValue"
            raise ValueError(msg)
        signed_notional = absolute_notional if position.size > 0 else -absolute_notional
        positions.append(
            PerpVaultPositionObservation(
                snapshot_id=snapshot_id,
                source_market_id=position.coin,
                signed_notional=signed_notional,
                quote_asset="USDC",
                valuation_basis=PositionValuationBasis.source_position_value,
                valuation_observed_at=observed_at,
                source_endpoint="POST /info clearinghouseState",
            )
        )
        payload_positions.append({"coin": position.coin, "szi": str(position.size), "positionValue": str(position.position_value)})
    identity = PerpVaultIdentity("hyperliquid", "hypercore", address, HYPERCORE_CHAIN_ID, address)
    bundle = PerpVaultObservationBundle(
        account=PerpVaultAccountObservation(
            identity=identity,
            snapshot_id=snapshot_id,
            observed_at=observed_at,
            written_at=observed_at,
            position_effective_at=observed_at,
            equity_effective_at=observed_at,
            total_equity=summary.tvl,
            quote_asset="USDC",
            position_data_status=SourcePositionDataStatus.available,
            position_data_reason="Public clearinghouse state",
            position_set_complete=True,
            source_endpoint="POST /info clearinghouseState",
            collector_version="1",
        ),
        positions=tuple(positions),
    )
    return bundle, {"vault_address": address, "tvl": str(summary.tvl), "asset_positions": payload_positions}


def _fetch_hyperliquid_vault_bundle(
    session: HyperliquidSession,
    summary: VaultSummary,
    timeout: float,
) -> tuple[PerpVaultObservationBundle, dict[str, Any]]:
    """Fetch and normalise one public vault without sharing a DuckDB writer.

    Expected source and parsing failures become an explicit unavailable bundle;
    programming defects continue to propagate.

    :param session:
        Configured public Hyperliquid session.
    :param summary:
        Vault identity and account equity discovered by the native vault scanner.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        Normalised common observation and whitelisted audit payload.
    """
    observed_at = native_datetime_utc_now()
    address = str(summary.vault_address).lower()
    try:
        state = fetch_perp_clearinghouse_state(session, address, timeout=timeout)
        return build_hyperliquid_vault_observation_bundle(summary, state, observed_at)
    except (requests.RequestException, InvalidOperation, KeyError, TypeError, ValueError) as exc:
        bundle = create_unavailable_perp_vault_observation_bundle(
            identity=PerpVaultIdentity("hyperliquid", "hypercore", address, HYPERCORE_CHAIN_ID, address),
            observed_at=observed_at,
            total_equity=summary.tvl,
            quote_asset="USDC",
            status=SourcePositionDataStatus.source_error,
            reason=f"Public clearinghouseState read failed: {exc}",
            source_endpoint="POST /info clearinghouseState",
        )
        return bundle, {"vault_address": address, "error": str(exc)}


def collect_hyperliquid_vault_observations(
    session: HyperliquidSession,
    connection: duckdb.DuckDBPyConnection,
    summaries: Iterable[VaultSummary],
    max_workers: int,
    timeout: float,
) -> int:
    """Collect public vault positions with threaded reads and serial writes.

    Hyperliquid's public clearinghouse endpoint is read according to its
    `canonical API documentation
    <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals>`__.
    This endpoint has a lower request weight than the mixed ``/info`` traffic
    used by the rest of the scanner, so it uses its own bounded 9 RPS limiter.
    When Webshare proxies are available, each distinct proxy receives one
    worker and its own limiter; direct-IP scans retain one shared limiter.

    :param session:
        Configured public Hyperliquid HTTP session.
    :param connection:
        Owner-thread native metrics DuckDB connection.
    :param summaries:
        Vaults already selected by the native vault scanner.
    :param max_workers:
        Threaded HTTP worker count.
    :param timeout:
        Per-request timeout in seconds.
    :return:
        Number of attempted vault observations.
    """
    selected = tuple(summaries)
    if not selected:
        return 0

    worker_count = min(max_workers, len(selected))
    if session.proxy_enabled:
        # Each clone has an independent limiter.  Do not create multiple
        # clones for one proxy IP, as that would exceed its documented budget.
        worker_count = min(worker_count, session.proxy_count)
    else:
        # The endpoint-specific limiter is independent from the scanner's
        # mixed-endpoint limiter.  Keep exactly one direct-IP worker so this
        # special budget cannot be multiplied by MAX_WORKERS.
        worker_count = 1

    worker_sessions = [
        session.clone_for_worker(
            proxy_start_index=worker_index,
            requests_per_second=CLEARINGHOUSE_STATE_REQUESTS_PER_SECOND,
        )
        for worker_index in range(worker_count)
    ]
    session_pool = list(worker_sessions)
    session_lock = threading.Lock()

    def _worker(summary: VaultSummary) -> tuple[PerpVaultObservationBundle, dict[str, Any]]:
        with session_lock:
            worker_session = session_pool.pop()
        try:
            return _fetch_hyperliquid_vault_bundle(worker_session, summary, timeout)
        finally:
            with session_lock:
                session_pool.append(worker_session)

    results = Parallel(n_jobs=worker_count, backend="threading")(delayed(_worker)(summary) for summary in selected)
    for bundle, payload in results:
        write_perp_vault_observation_bundle(connection, bundle, payload)
    return len(results)
