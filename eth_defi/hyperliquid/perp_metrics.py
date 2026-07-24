"""Public Hyperliquid vault open-position metric collection."""

import datetime
import logging
import uuid
from collections.abc import Iterable
from typing import Any

import duckdb
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


def build_hyperliquid_vault_observation_bundle(
    summary: VaultSummary,
    state: PerpClearinghouseState,
    observed_at: datetime.datetime,
) -> tuple[PerpVaultObservationBundle, dict[str, Any]]:
    """Normalise public ``clearinghouseState`` positions for a vault address.

    Hyperliquid's ``positionValue`` is absolute, while signed ``szi`` carries
    direction.  The adapter signs the value from non-zero size and excludes
    all margin, leverage and liquidation fields from its payload.

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
    """Fetch and normalise one public vault without sharing a DuckDB writer."""
    observed_at = native_datetime_utc_now()
    address = str(summary.vault_address).lower()
    try:
        state = fetch_perp_clearinghouse_state(session, address, timeout=timeout)
        return build_hyperliquid_vault_observation_bundle(summary, state, observed_at)
    except Exception as exc:
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

    :param session:
        Configured public Hyperliquid HTTP session.
    :param connection:
        Owner-thread daily metrics DuckDB connection.
    :param summaries:
        Vaults already selected by the daily scanner.
    :param max_workers:
        Threaded HTTP worker count.
    :param timeout:
        Per-request timeout in seconds.
    :return:
        Number of attempted vault observations.
    """
    selected = tuple(summaries)
    results = Parallel(n_jobs=max_workers, backend="threading")(delayed(_fetch_hyperliquid_vault_bundle)(session, summary, timeout) for summary in selected)
    for bundle, payload in results:
        write_perp_vault_observation_bundle(connection, bundle, payload)
    return len(results)
