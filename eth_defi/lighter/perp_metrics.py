"""Public Lighter pool account and open-position metric collection."""

import datetime
import logging
import uuid
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

import duckdb
from joblib import Parallel, delayed

from eth_defi.compat import native_datetime_utc_now
from eth_defi.lighter.constants import LighterAPIConfig
from eth_defi.lighter.session import LighterSession
from eth_defi.lighter.valuation import fetch_lighter_account_by_index
from eth_defi.lighter.vault import LighterPoolSummary
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


def build_lighter_pool_observation_bundle(
    account: dict[str, Any],
    deployment: LighterAPIConfig,
    observed_at: datetime.datetime,
) -> tuple[PerpVaultObservationBundle, dict[str, Any]]:
    """Normalise a public Lighter account response to fundamental source facts.

    Lighter reports an absolute position value and a separate integer sign.
    The public account parser uses ``sign >= 0`` for a long, which this adapter
    preserves after filtering zero source quantities.

    :param account:
        Raw first object from ``GET /api/v1/account``.
    :param deployment:
        Lighter deployment identity and denomination configuration.
    :param observed_at:
        Naive UTC response receipt time, used because the endpoint has no
        position-state timestamp.
    :return:
        Validated common bundle and its whitelisted audit payload.
    """
    account_index = int(account.get("account_index", account.get("index")))
    identity = PerpVaultIdentity(
        protocol_slug="lighter",
        deployment_slug=deployment.slug,
        vault_id=str(account_index),
        dataset_chain_id=deployment.chain_id,
        dataset_address=deployment.format_pool_address(account_index),
    )
    positions: list[PerpVaultPositionObservation] = []
    payload_positions: list[dict[str, str]] = []
    snapshot_id = uuid.uuid4().hex
    for raw_position in account.get("positions") or []:
        quantity = Decimal(str(raw_position.get("position", "0")))
        if quantity == 0:
            continue
        raw_value = raw_position.get("position_value")
        if raw_value is None:
            msg = "Lighter non-zero position lacks current position_value"
            raise ValueError(msg)
        absolute_notional = abs(Decimal(str(raw_value)))
        if absolute_notional == 0:
            msg = "Lighter non-zero position has zero current position_value"
            raise ValueError(msg)
        sign = Decimal(1) if int(raw_position.get("sign", 0)) >= 0 else Decimal(-1)
        market_id = str(raw_position["market_id"])
        signed_notional = sign * absolute_notional
        positions.append(
            PerpVaultPositionObservation(
                snapshot_id=snapshot_id,
                source_market_id=market_id,
                signed_notional=signed_notional,
                quote_asset=deployment.denomination,
                valuation_basis=PositionValuationBasis.source_position_value,
                valuation_observed_at=observed_at,
                source_endpoint="GET /api/v1/account",
            )
        )
        payload_positions.append(
            {
                "market_id": market_id,
                "position": str(quantity),
                "sign": str(raw_position.get("sign", 0)),
                "position_value": str(raw_value),
            }
        )
    total_equity = Decimal(str(account["total_asset_value"])) if account.get("total_asset_value") is not None else None
    bundle = PerpVaultObservationBundle(
        account=PerpVaultAccountObservation(
            identity=identity,
            snapshot_id=snapshot_id,
            observed_at=observed_at,
            written_at=observed_at,
            position_effective_at=observed_at,
            equity_effective_at=observed_at,
            total_equity=total_equity,
            quote_asset=deployment.denomination,
            position_data_status=SourcePositionDataStatus.available,
            position_data_reason="Public pool account positions",
            position_set_complete=True,
            source_endpoint="GET /api/v1/account",
            collector_version="1",
        ),
        positions=tuple(positions),
    )
    return bundle, {"account_index": str(account_index), "total_asset_value": str(account.get("total_asset_value")), "positions": payload_positions}


def _fetch_lighter_pool_bundle(
    session: LighterSession,
    summary: LighterPoolSummary,
    timeout: float,
) -> tuple[PerpVaultObservationBundle, dict[str, Any]]:
    """Fetch and normalise one pool without writing to the shared DuckDB handle."""
    observed_at = native_datetime_utc_now()
    try:
        account = fetch_lighter_account_by_index(session, summary.account_index, timeout=timeout)
        return build_lighter_pool_observation_bundle(account, session.deployment, observed_at)
    except Exception as exc:
        identity = PerpVaultIdentity(
            protocol_slug="lighter",
            deployment_slug=session.deployment.slug,
            vault_id=str(summary.account_index),
            dataset_chain_id=session.deployment.chain_id,
            dataset_address=session.deployment.format_pool_address(summary.account_index),
        )
        bundle = create_unavailable_perp_vault_observation_bundle(
            identity=identity,
            observed_at=observed_at,
            total_equity=Decimal(str(summary.total_asset_value)),
            quote_asset=session.deployment.denomination,
            status=SourcePositionDataStatus.source_error,
            reason=f"Public account position read failed: {exc}",
            source_endpoint="GET /api/v1/account",
        )
        return bundle, {"account_index": str(summary.account_index), "error": str(exc)}


def collect_lighter_pool_observations(
    session: LighterSession,
    connection: duckdb.DuckDBPyConnection,
    summaries: Iterable[LighterPoolSummary],
    max_workers: int,
    timeout: float,
) -> int:
    """Collect public Lighter positions in parallel and persist bundles serially.

    :param session:
        Configured public Lighter HTTP session.
    :param connection:
        Owner-thread protocol DuckDB connection.
    :param summaries:
        Pools already selected by the daily scanner.
    :param max_workers:
        Threaded HTTP worker count.
    :param timeout:
        Per-request timeout in seconds.
    :return:
        Number of attempted pool observations.
    """
    selected = tuple(summaries)
    results = Parallel(n_jobs=max_workers, backend="threading")(delayed(_fetch_lighter_pool_bundle)(session, summary, timeout) for summary in selected)
    for bundle, payload in results:
        write_perp_vault_observation_bundle(connection, bundle, payload)
    return len(results)
