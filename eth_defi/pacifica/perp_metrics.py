"""Unsupported Pacifica lake account and open-position parser groundwork."""

import datetime
import uuid
from decimal import Decimal
from typing import Any

import requests

from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.pacifica.constants import PACIFICA_API_URL, PACIFICA_CHAIN_ID
from eth_defi.perp_dex.metrics import (
    PerpVaultAccountObservation,
    PerpVaultIdentity,
    PerpVaultObservationBundle,
    PerpVaultPositionObservation,
    PositionValuationBasis,
    SourcePositionDataStatus,
)

#: Pacifica is intentionally excluded from the production capability registry
#: and native price pipeline.
PACIFICA_PERP_VAULT_METRICS_SUPPORTED = False

# TODO: Enable Pacifica only after its native price reader is implemented and
# mark/position timestamp skew is validated against the shared metric contract.


def _unwrap_pacifica_response(response: requests.Response) -> Any:
    """Validate Pacifica's standard response envelope.

    :param response:
        HTTP response returned by the public Pacifica API.
    :return:
        The response envelope's ``data`` value.
    """
    response.raise_for_status()
    payload = response.json()
    if payload.get("success") is not True:
        raise ValueError(f"Pacifica API request failed: {payload.get('error') or payload}")
    return payload["data"]


def fetch_pacifica_lakes(session: requests.Session, timeout: float) -> tuple[dict[str, Any], ...]:
    """Fetch the publicly listed Pacifica lakes/vaults.

    This unsupported parser groundwork follows Pacifica's `public API
    documentation <https://docs.pacifica.fi/>`__ and does not write to the
    production observation store.

    :param session:
        Configured HTTP session.
    :param timeout:
        Request timeout in seconds.
    :return:
        Immutable sequence of raw lake records.
    """
    data = _unwrap_pacifica_response(session.get(f"{PACIFICA_API_URL}/lake/list", timeout=timeout))
    return tuple(data.get("lakes") or ())


def fetch_pacifica_account(session: requests.Session, lake_address: str, timeout: float) -> dict[str, Any]:
    """Fetch a current public account equity snapshot for one lake.

    :param session:
        Configured HTTP session.
    :param lake_address:
        Pacifica lake account identifier.
    :param timeout:
        Request timeout in seconds.
    :return:
        Raw public account response.
    """
    return _unwrap_pacifica_response(session.get(f"{PACIFICA_API_URL}/account", params={"account": lake_address}, timeout=timeout))


def fetch_pacifica_positions(session: requests.Session, lake_address: str, timeout: float) -> tuple[dict[str, Any], ...]:
    """Fetch the complete public current position set for one lake.

    :param session:
        Configured HTTP session.
    :param lake_address:
        Pacifica lake account identifier.
    :param timeout:
        Request timeout in seconds.
    :return:
        Immutable sequence of raw open-position records.
    """
    return tuple(_unwrap_pacifica_response(session.get(f"{PACIFICA_API_URL}/positions", params={"account": lake_address}, timeout=timeout)))


def fetch_pacifica_marks(session: requests.Session, timeout: float) -> dict[str, tuple[Decimal, datetime.datetime]]:
    """Fetch current mark prices once and index them by Pacifica symbol.

    :param session:
        Configured HTTP session.
    :param timeout:
        Request timeout in seconds.
    :return:
        Symbol mapping to a decimal mark and naive UTC source timestamp.
    """
    prices = _unwrap_pacifica_response(session.get(f"{PACIFICA_API_URL}/info/prices", timeout=timeout))
    result: dict[str, tuple[Decimal, datetime.datetime]] = {}
    for price in prices:
        mark = price.get("mark")
        timestamp = price.get("timestamp")
        if mark is None or timestamp is None:
            continue
        result[str(price["symbol"])] = (Decimal(str(mark)), native_datetime_utc_fromtimestamp(int(timestamp) / 1000))
    return result


def build_pacifica_lake_observation_bundle(
    lake: dict[str, Any],
    account: dict[str, Any],
    positions: tuple[dict[str, Any], ...],
    marks: dict[str, tuple[Decimal, datetime.datetime]],
    observed_at: datetime.datetime,
) -> tuple[PerpVaultObservationBundle, dict[str, Any]]:
    """Normalise public Pacifica facts to one account and signed notionals.

    Pacifica positions supply direction as ``bid``/``ask`` and size in base
    units. The only valuation retained is base amount multiplied by the same
    collection cycle's public mark; margin and liquidation fields are not
    collected.

    This function remains parser groundwork only; Pacifica is not registered
    in the production collection or export pipeline.

    :param lake:
        Raw lake identity record.
    :param account:
        Raw account equity response.
    :param positions:
        Complete raw open-position response.
    :param marks:
        Symbol mapping to decimal marks and naive UTC source timestamps.
    :param observed_at:
        Naive UTC collection time.
    :return:
        Normalised common observation and whitelisted audit payload.
    """
    address = str(lake["address"])
    snapshot_id = uuid.uuid4().hex
    normalised_positions: list[PerpVaultPositionObservation] = []
    payload_positions: list[dict[str, str]] = []
    for position in positions:
        amount = Decimal(str(position.get("amount", "0")))
        if amount == 0:
            continue
        symbol = str(position["symbol"])
        try:
            mark, mark_observed_at = marks[symbol]
        except KeyError as exc:
            raise ValueError(f"Pacifica position market has no contemporaneous mark: {symbol}") from exc
        side = str(position.get("side", "")).lower()
        if side not in {"bid", "ask"}:
            raise ValueError(f"Unknown Pacifica position side: {side}")
        absolute_notional = abs(amount) * mark
        if absolute_notional == 0:
            msg = "Pacifica non-zero position has zero mark notional"
            raise ValueError(msg)
        signed_notional = absolute_notional if side == "bid" else -absolute_notional
        normalised_positions.append(
            PerpVaultPositionObservation(
                snapshot_id=snapshot_id,
                source_market_id=symbol,
                signed_notional=signed_notional,
                quote_asset="USDC",
                valuation_basis=PositionValuationBasis.mark_price,
                valuation_observed_at=mark_observed_at,
                source_endpoint="GET /positions + GET /info/prices",
            )
        )
        payload_positions.append({"symbol": symbol, "side": side, "amount": str(amount), "mark": str(mark)})

    account_updated_at = account.get("updated_at")
    equity_effective_at = native_datetime_utc_fromtimestamp(int(account_updated_at) / 1000) if account_updated_at else observed_at
    bundle = PerpVaultObservationBundle(
        account=PerpVaultAccountObservation(
            identity=PerpVaultIdentity("pacifica", "mainnet", address, PACIFICA_CHAIN_ID, address.lower()),
            snapshot_id=snapshot_id,
            observed_at=observed_at,
            written_at=observed_at,
            position_effective_at=observed_at,
            equity_effective_at=equity_effective_at,
            total_equity=Decimal(str(account["account_equity"])) if account.get("account_equity") is not None else None,
            quote_asset="USDC",
            position_data_status=SourcePositionDataStatus.available,
            position_data_reason="Public Pacifica lake account and positions",
            position_set_complete=True,
            source_endpoint="GET /account + GET /positions + GET /info/prices",
            collector_version="1",
        ),
        positions=tuple(normalised_positions),
    )
    return bundle, {
        "lake_address": address,
        "account_equity": str(account.get("account_equity")),
        "account_updated_at": account_updated_at,
        "positions": payload_positions,
    }
