"""Protocol-independent perpetual DEX vault observations and calculations.

Adapters preserve one account observation and one signed notional for every
non-zero open position.  Long/short, gross/net and concentration are derived
from those fundamental values and are never written to the source tables.
"""

import datetime
import uuid
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from packaging.version import InvalidVersion, Version


class SourcePositionDataStatus(StrEnum):
    """Availability states emitted directly by a protocol adapter."""

    available = "available"
    not_public = "not_public"
    authentication_required = "authentication_required"
    source_error = "source_error"
    not_implemented = "not_implemented"


class PerpParquetDataStatus(StrEnum):
    """Availability states materialised in the raw and cleaned price Parquet."""

    available = "available"
    not_public = "not_public"
    authentication_required = "authentication_required"
    source_error = "source_error"
    not_implemented = "not_implemented"
    not_collected = "not_collected"
    not_applicable = "not_applicable"
    stale = "stale"


class PositionValuationBasis(StrEnum):
    """The source used to calculate a position's current quote notional."""

    source_position_value = "source_position_value"
    mark_price = "mark_price"
    oracle_price = "oracle_price"


@dataclass(slots=True, frozen=True)
class PerpVaultIdentity:
    """Stable identity shared by a native vault scanner and price exporter.

    ``dataset_chain_id`` and ``dataset_address`` are the exact keys used in the
    vault price Parquet, including synthetic non-EVM chain and address values.
    """

    protocol_slug: str
    deployment_slug: str
    vault_id: str
    dataset_chain_id: int
    dataset_address: str


@dataclass(slots=True, frozen=True)
class PerpVaultAccountObservation:
    """One immutable account observation and its position-set availability.

    A unique ``snapshot_id`` identifies the write, while corrections are
    selected by identity and ``position_effective_at``.  ``total_equity`` is
    optional audit information and never gates valid position collection.
    """

    identity: PerpVaultIdentity
    snapshot_id: str
    observed_at: datetime.datetime
    written_at: datetime.datetime
    position_effective_at: datetime.datetime
    equity_effective_at: datetime.datetime | None
    total_equity: Decimal | None
    quote_asset: str | None
    position_data_status: SourcePositionDataStatus
    position_data_reason: str
    position_set_complete: bool
    source_endpoint: str
    collector_version: str
    raw_payload_reference: str | None = None


@dataclass(slots=True, frozen=True)
class PerpVaultPositionObservation:
    """One non-zero open position valued in the account quote asset."""

    snapshot_id: str
    source_market_id: str
    signed_notional: Decimal
    quote_asset: str
    valuation_basis: PositionValuationBasis
    valuation_observed_at: datetime.datetime
    source_endpoint: str


@dataclass(slots=True, frozen=True)
class PerpVaultObservationBundle:
    """One account observation and its complete current position set."""

    account: PerpVaultAccountObservation
    positions: tuple[PerpVaultPositionObservation, ...]


@dataclass(slots=True, frozen=True)
class DerivedPerpVaultExposure:
    """Materialised exposure values calculated from one validated bundle."""

    long_notional: Decimal | None
    short_notional: Decimal | None
    open_position_count: int | None
    largest_position_notional: Decimal | None


def create_unavailable_perp_vault_observation_bundle(  # noqa: PLR0917
    identity: PerpVaultIdentity,
    observed_at: datetime.datetime,
    total_equity: Decimal | None,
    quote_asset: str,
    status: SourcePositionDataStatus,
    reason: str,
    source_endpoint: str,
    collector_version: str = "1",
) -> PerpVaultObservationBundle:
    """Create an explicit account-only source observation with no positions.

    Account-only APIs still need a source availability state so downstream
    users see null exposure rather than an invented empty portfolio.

    :param identity:
        Protocol-native vault identity mapped to the price dataset key.
    :param observed_at:
        Naive UTC account/API observation time.
    :param total_equity:
        Optional current account equity in ``quote_asset``.
    :param quote_asset:
        Exact source denomination.
    :param status:
        Non-available source position status.
    :param reason:
        Concise protocol-specific availability explanation.
    :param source_endpoint:
        Public endpoint used for the account value.
    :param collector_version:
        Parser version stored with the immutable bundle.
    :return:
        Bundle with no position rows.
    """
    if status is SourcePositionDataStatus.available:
        msg = "Use a complete position bundle for available position data"
        raise ValueError(msg)
    account = PerpVaultAccountObservation(
        identity=identity,
        snapshot_id=uuid.uuid4().hex,
        observed_at=observed_at,
        written_at=observed_at,
        position_effective_at=observed_at,
        equity_effective_at=observed_at,
        total_equity=total_equity,
        quote_asset=quote_asset,
        position_data_status=status,
        position_data_reason=reason,
        position_set_complete=False,
        source_endpoint=source_endpoint,
        collector_version=collector_version,
    )
    return PerpVaultObservationBundle(account=account, positions=())


def validate_perp_vault_observation_bundle(bundle: PerpVaultObservationBundle) -> None:
    """Validate source facts before an observation bundle reaches DuckDB.

    Available observations must contain a complete response and a unique,
    non-zero, quote-consistent position set.  Unavailable source states cannot
    carry positions, which prevents privacy or authentication gaps becoming a
    false empty portfolio.

    :param bundle:
        Account observation and its protocol-normalised positions.
    :return:
        ``None``.  Raises :class:`ValueError` for invalid source facts.
    """
    account = bundle.account
    if account.observed_at.tzinfo is not None or account.written_at.tzinfo is not None or account.position_effective_at.tzinfo is not None:
        msg = "Perp observation timestamps must be naive UTC datetimes"
        raise ValueError(msg)
    if account.equity_effective_at is not None and account.equity_effective_at.tzinfo is not None:
        msg = "Equity timestamp must be a naive UTC datetime"
        raise ValueError(msg)
    try:
        Version(account.collector_version)
    except InvalidVersion as exc:
        raise ValueError(f"Collector version must be PEP 440 compliant: {account.collector_version}") from exc

    if account.position_data_status is SourcePositionDataStatus.available:
        if not account.position_set_complete:
            msg = "Available position data requires a complete position set"
            raise ValueError(msg)
    elif account.position_set_complete:
        msg = "Unavailable position data cannot claim a complete position set"
        raise ValueError(msg)
    elif bundle.positions:
        msg = "Unavailable position data cannot contain position rows"
        raise ValueError(msg)

    markets: set[str] = set()
    for position in bundle.positions:
        if position.snapshot_id != account.snapshot_id:
            msg = "Position snapshot ID does not match its account observation"
            raise ValueError(msg)
        if position.source_market_id in markets:
            raise ValueError(f"Duplicate source market ID: {position.source_market_id}")
        markets.add(position.source_market_id)
        if position.signed_notional == 0:
            msg = "Stored perp positions must have non-zero signed notional"
            raise ValueError(msg)
        if not position.signed_notional.is_finite():
            msg = "Position signed notional must be finite"
            raise ValueError(msg)
        if position.valuation_observed_at.tzinfo is not None:
            msg = "Position valuation timestamp must be naive UTC"
            raise ValueError(msg)
        if account.quote_asset is not None and position.quote_asset != account.quote_asset:
            msg = "Position quote asset does not match account quote asset"
            raise ValueError(msg)


def derive_perp_vault_exposure(bundle: PerpVaultObservationBundle) -> DerivedPerpVaultExposure:
    """Calculate the materialised exposure basis values for one bundle.

    The returned values are null for unavailable/incomplete source data and
    zero for a validated available account with no open positions.  Gross,
    net and concentration remain derived consumer values.

    :param bundle:
        Previously validated account and position observations.
    :return:
        Long, short, count and largest absolute current notional.
    """
    validate_perp_vault_observation_bundle(bundle)
    if bundle.account.position_data_status is not SourcePositionDataStatus.available:
        return DerivedPerpVaultExposure(None, None, None, None)

    signed_notionals = [position.signed_notional for position in bundle.positions]
    long_notional = sum((value for value in signed_notionals if value > 0), Decimal(0))
    short_notional = sum((-value for value in signed_notionals if value < 0), Decimal(0))
    largest = max((abs(value) for value in signed_notionals), default=Decimal(0))
    return DerivedPerpVaultExposure(long_notional, short_notional, len(signed_notionals), largest)
