"""Derive.xyz public API functions.

Typed wrappers for public (unauthenticated) Derive API endpoints.

Uses :py:func:`~eth_defi.derive.session.create_derive_session` for
HTTP connections with rate limiting and retry logic.

Example::

    from eth_defi.derive.api import fetch_perpetual_instruments, fetch_funding_rate_history, fetch_open_interest_onchain
    from eth_defi.derive.session import create_derive_session
    from web3 import Web3

    session = create_derive_session()

    # Discover all active perpetual instruments
    instruments = fetch_perpetual_instruments(session)
    print(instruments)  # ['ETH-PERP', 'BTC-PERP', ...]

    # Fetch funding rate history for one instrument
    rates = fetch_funding_rate_history(session, "ETH-PERP")
    for r in rates:
        print(f"{r.timestamp}: rate={r.funding_rate}")

    # Fetch historical open interest on-chain
    w3 = Web3(Web3.HTTPProvider("https://rpc.derive.xyz"))
    oi = fetch_open_interest_onchain(w3, "0xAf65752C4643E25C02F693f9D4FE19cF23a095E3", block_number=36000000)
    print(f"OI: {oi}")  # e.g. Decimal('3355.06')
"""

import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal

from requests import Session
from web3 import Web3

from eth_defi.derive.constants import DERIVE_MAINNET_API_URL, DERIVE_MAINNET_RPC_URL
from eth_defi.event_reader.multicall_batcher import get_multicall_contract

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OpenInterestEntry:
    """A daily on-chain snapshot for a Derive perpetual instrument.

    Contains open interest, mark price (perp price), and index price
    read from on-chain view functions via Multicall3 at daily intervals.
    """

    #: Instrument name (e.g. ``"ETH-PERP"``)
    instrument: str

    #: Snapshot timestamp (naive UTC, aligned to midnight)
    timestamp: datetime.datetime

    #: Timestamp in milliseconds since epoch
    timestamp_ms: int

    #: Open interest in the instrument's base currency
    #: (e.g. ETH for ETH-PERP). From ``openInterest(uint256)``.
    open_interest: Decimal

    #: Mark/perp price in USD (18 decimals on-chain).
    #: From ``getPerpPrice()`` — the first return value (price).
    #: ``None`` if the call reverted (contract not yet deployed).
    perp_price: Decimal | None = None

    #: Spot/index price in USD (18 decimals on-chain).
    #: From ``getIndexPrice()`` — the first return value (price).
    #: ``None`` if the call reverted (contract not yet deployed).
    index_price: Decimal | None = None


@dataclass(slots=True)
class FundingRateEntry:
    """A single funding rate snapshot from Derive.

    Represents one hourly funding rate observation for a perpetual
    instrument.
    """

    #: Instrument name (e.g. ``"ETH-PERP"``)
    instrument: str

    #: Snapshot timestamp (naive UTC)
    timestamp: datetime.datetime

    #: Timestamp in milliseconds since epoch
    timestamp_ms: int

    #: Hourly funding rate as a decimal fraction
    #: (e.g. ``Decimal("0.00001234")``)
    funding_rate: Decimal


def _unwrap_result(data: dict, method: str) -> dict:
    """Unwrap JSON-RPC envelope and raise on error.

    :param data:
        Parsed JSON response.
    :param method:
        API method name for error messages.
    :return:
        The ``result`` field from the response.
    :raises ValueError:
        If the response contains an error.
    """
    if "error" in data:
        error = data["error"]
        error_data = error.get("data", "")
        error_msg = f"Derive API error for {method}: {error.get('code', 'unknown')}: {error.get('message', 'no message')}"
        if error_data:
            error_msg += f" (data: {error_data})"
        raise ValueError(error_msg)
    return data.get("result", {})


def fetch_perpetual_instruments(
    session: Session,
    currency: str | None = None,
    base_url: str = DERIVE_MAINNET_API_URL,
    timeout: float = 30.0,
) -> list[str]:
    """Fetch all active perpetual instrument names from Derive.

    Calls the public ``get_all_instruments`` endpoint with
    ``instrument_type="perp"`` to discover available perpetual
    contracts.

    Example::

        from eth_defi.derive.api import fetch_perpetual_instruments
        from eth_defi.derive.session import create_derive_session

        session = create_derive_session()
        instruments = fetch_perpetual_instruments(session)
        # ['ETH-PERP', 'BTC-PERP', 'SOL-PERP', ...]

    :param session:
        HTTP session from :py:func:`~eth_defi.derive.session.create_derive_session`.
    :param currency:
        Optional currency filter (e.g. ``"ETH"``, ``"BTC"``).
        If ``None``, returns all active perps.
    :param base_url:
        Derive API base URL.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        Sorted list of instrument names.
    :raises ValueError:
        If the API returns an error response.
    """
    url = f"{base_url}/public/get_all_instruments"
    instruments = []
    page = 1

    while True:
        params = {
            "instrument_type": "perp",
            "expired": False,
            "page": page,
            "page_size": 1000,
        }
        if currency is not None:
            params["currency"] = currency

        response = session.post(
            url,
            json=params,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()

        result = _unwrap_result(response.json(), "get_all_instruments")
        page_instruments = result.get("instruments", [])

        for inst in page_instruments:
            name = inst.get("instrument_name")
            if name and inst.get("is_active", True):
                instruments.append(name)

        pagination = result.get("pagination", {})
        num_pages = pagination.get("num_pages", 1)
        if page >= num_pages:
            break
        page += 1

    instruments.sort()
    logger.info("Found %d active perpetual instruments", len(instruments))
    return instruments


def fetch_funding_rate_history(
    session: Session,
    instrument_name: str,
    start_time: datetime.datetime | None = None,
    end_time: datetime.datetime | None = None,
    base_url: str = DERIVE_MAINNET_API_URL,
    timeout: float = 30.0,
) -> list[FundingRateEntry]:
    """Fetch funding rate history for a Derive perpetual instrument.

    Calls the public ``get_funding_rate_history`` endpoint.
    No authentication required.

    Data is returned at hourly resolution — the native funding rate
    interval on Derive.  The full history is available back to
    instrument inception (ETH-PERP since 2024-01-05).

    For best results, keep the query window to one day at a time
    and use :py:class:`~eth_defi.derive.historical.DeriveFundingRateDatabase`
    for bulk historical fetches.

    .. note::

       The Derive API requires the parameter names ``start_timestamp``
       and ``end_timestamp`` (not ``start_time`` / ``end_time``).
       Using the wrong names silently falls back to the most recent
       30 days.

    Example::

        from eth_defi.derive.api import fetch_funding_rate_history
        from eth_defi.derive.session import create_derive_session

        session = create_derive_session()
        rates = fetch_funding_rate_history(session, "ETH-PERP")
        for r in rates:
            print(f"{r.timestamp}: {r.funding_rate}")

    :param session:
        HTTP session from :py:func:`~eth_defi.derive.session.create_derive_session`.
    :param instrument_name:
        Perpetual instrument name (e.g. ``"ETH-PERP"``).
    :param start_time:
        Start of the query window (naive UTC). Defaults to 30 days ago.
    :param end_time:
        End of the query window (naive UTC). Defaults to now.
    :param base_url:
        Derive API base URL.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        List of funding rate entries sorted by timestamp ascending.
    :raises ValueError:
        If the API returns an error response.
    """
    url = f"{base_url}/public/get_funding_rate_history"

    params: dict = {"instrument_name": instrument_name}

    if start_time is not None:
        params["start_timestamp"] = int(start_time.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)

    if end_time is not None:
        params["end_timestamp"] = int(end_time.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)

    response = session.post(
        url,
        json=params,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()

    result = _unwrap_result(response.json(), "get_funding_rate_history")
    history = result.get("funding_rate_history", [])

    entries = []
    for item in history:
        ts_ms = int(item["timestamp"])
        ts_dt = datetime.datetime.fromtimestamp(ts_ms / 1000, tz=datetime.timezone.utc).replace(tzinfo=None)
        entries.append(
            FundingRateEntry(
                instrument=instrument_name,
                timestamp=ts_dt,
                timestamp_ms=ts_ms,
                funding_rate=Decimal(str(item["funding_rate"])),
            )
        )

    entries.sort(key=lambda e: e.timestamp_ms)
    logger.debug("Fetched %d funding rate entries for %s", len(entries), instrument_name)
    return entries


def fetch_open_interest(
    session: Session,
    instrument_name: str,
    base_url: str = DERIVE_MAINNET_API_URL,
    timeout: float = 30.0,
) -> OpenInterestEntry | None:
    """Fetch the **current** open interest for a Derive perpetual instrument.

    Calls the public ``/public/statistics`` endpoint.  No authentication
    required.

    .. warning::

       This endpoint **always returns the current live OI** regardless of
       any timestamp parameter.  It does **not** support historical queries.
       For historical open interest data, use
       :py:func:`fetch_open_interest_onchain` which reads on-chain state
       from the Derive Chain archive node at any historical block.

    Example::

        from eth_defi.derive.api import fetch_open_interest
        from eth_defi.derive.session import create_derive_session

        session = create_derive_session()
        entry = fetch_open_interest(session, "ETH-PERP")
        if entry:
            print(f"{entry.timestamp}: {entry.open_interest}")

    :param session:
        HTTP session from :py:func:`~eth_defi.derive.session.create_derive_session`.
    :param instrument_name:
        Perpetual instrument name (e.g. ``"ETH-PERP"``) or aggregate
        type (``"PERP"``, ``"ALL"``, ``"OPTION"``, ``"SPOT"``).
    :param base_url:
        Derive API base URL.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        :py:class:`OpenInterestEntry` timestamped at the current moment,
        or ``None`` if open interest is zero (instrument not yet listed).
    :raises ValueError:
        If the API returns an error response.
    """
    url = f"{base_url}/public/statistics"

    params: dict = {"instrument_name": instrument_name}

    response = session.post(
        url,
        json=params,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()

    result = _unwrap_result(response.json(), "statistics")
    oi_raw = result.get("open_interest")

    if not oi_raw:
        return None

    oi = Decimal(str(oi_raw))
    if oi == 0:
        return None

    ts_dt = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    ts_ms = int(ts_dt.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)

    logger.debug("Fetched open interest for %s at %s: %s", instrument_name, ts_dt, oi)
    return OpenInterestEntry(
        instrument=instrument_name,
        timestamp=ts_dt,
        timestamp_ms=ts_ms,
        open_interest=oi,
    )


#: ABI selector for ``openInterest(uint256 subId)`` on Derive perp contracts.
#:
#: Verified on Derive Mainnet Blockscout for ETH-PERP contract
#: ``0xAf65752C4643E25C02F693f9D4FE19cF23a095E3``.
PERP_OPEN_INTEREST_SELECTOR = "88e53ec8"

#: ABI-encoded ``uint256(0)`` for sub-ID 0 (all perps use sub-ID 0).
_PERP_SUB_ID_ZERO = "0000000000000000000000000000000000000000000000000000000000000000"

#: ABI selector for ``getPerpPrice()`` on Derive perp contracts.
#:
#: Returns ``(uint256 price, uint256 confidence)`` — both 18-decimal.
PERP_GET_PERP_PRICE_SELECTOR = "90f76b18"

#: ABI selector for ``getIndexPrice()`` on Derive perp contracts.
#:
#: Returns ``(uint256 price, uint256 confidence)`` — both 18-decimal.
PERP_GET_INDEX_PRICE_SELECTOR = "58c0994a"

#: Derive Chain block time in seconds (OP Stack L2, 2s blocks).
DERIVE_BLOCK_TIME_SECONDS = 2


def _decode_uint256(raw: bytes) -> Decimal | None:
    """Decode a uint256 return value (18-decimal fixed-point) to Decimal.

    Returns ``None`` for zero or insufficient data.
    """
    if len(raw) < 32:
        return None
    val = int.from_bytes(raw[:32], "big")
    if val == 0:
        return None
    return Decimal(val) / Decimal(10**18)


def fetch_open_interest_onchain(
    w3: Web3,
    contract_address: str,
    block_number: int,
    sub_id: int = 0,
) -> Decimal | None:
    """Fetch open interest for a Derive perp from on-chain state.

    Calls ``openInterest(uint256 subId)`` on the perp asset contract at the
    specified historical block.  The Derive Chain RPC endpoint
    (``https://rpc.derive.xyz``) is an archive node that supports historical
    ``eth_call`` going back to chain genesis.

    This is the only way to retrieve historical open interest data — the
    public ``/public/statistics`` REST endpoint always returns the current
    live value regardless of any ``end_time`` parameter.

    The returned value is in the instrument's base currency with 18 decimal
    places (e.g. ETH for ETH-PERP, BTC for BTC-PERP).

    Example::

        from web3 import Web3
        from eth_defi.derive.api import fetch_open_interest_onchain
        from eth_defi.derive.constants import DERIVE_MAINNET_RPC_URL

        w3 = Web3(Web3.HTTPProvider(DERIVE_MAINNET_RPC_URL))
        # ETH-PERP contract on Derive Mainnet
        oi = fetch_open_interest_onchain(
            w3,
            "0xAf65752C4643E25C02F693f9D4FE19cF23a095E3",
            block_number=36000000,
        )
        print(oi)  # e.g. Decimal('3186.42')

    :param w3:
        Web3 instance connected to Derive Chain
        (``https://rpc.derive.xyz``, chain ID 957).
    :param contract_address:
        The ``base_asset_address`` for the instrument, as returned by
        the ``/public/get_all_instruments`` endpoint.
    :param block_number:
        Block number at which to read state. Use
        :py:func:`~eth_defi.derive.historical.estimate_block_at_timestamp`
        to convert a UTC timestamp to a block number.
    :param sub_id:
        Sub-asset identifier. Always ``0`` for perpetuals.
    :return:
        Open interest in the base currency as a :py:class:`Decimal`,
        or ``None`` if zero (instrument not yet active at that block).
    :raises Exception:
        Transient RPC errors (connection failures, timeouts, rate
        limits) propagate to the caller so the sync loop can abort
        before advancing the watermark past a hole.  Only
        ``ContractLogicError`` (contract revert — meaning the block
        predates contract deployment) is caught and treated as
        zero OI.
    """
    from web3.exceptions import ContractLogicError

    calldata = "0x" + PERP_OPEN_INTEREST_SELECTOR + f"{sub_id:064x}"
    try:
        raw = w3.eth.call({"to": contract_address, "data": calldata}, block_number)
    except ContractLogicError:
        # Contract not yet deployed or reverts at this block — treat as zero
        logger.debug("openInterest reverted at block %d (pre-deployment?)", block_number)
        return None

    return _decode_uint256(raw)


@dataclass(slots=True)
class PerpSnapshotMulticallResult:
    """Result of a single instrument from :py:func:`fetch_perp_snapshots_multicall`.

    Groups the three on-chain reads (OI, perp price, index price) for
    one instrument at one block.
    """

    #: Open interest in base currency. ``None`` if reverted or zero.
    open_interest: Decimal | None

    #: Mark/perp price in USD. ``None`` if reverted.
    perp_price: Decimal | None

    #: Spot/index price in USD. ``None`` if reverted.
    index_price: Decimal | None


def fetch_perp_snapshots_multicall(
    w3: Web3,
    contract_addresses: list[str],
    block_number: int,
    sub_id: int = 0,
) -> list[PerpSnapshotMulticallResult]:
    """Fetch open interest, perp price, and index price for multiple instruments in one RPC call.

    Uses Multicall3 ``aggregate3`` to batch three view function calls per
    instrument:

    - ``openInterest(uint256 subId)`` — OI in base currency
    - ``getPerpPrice()`` — mark/perp price in USD
    - ``getIndexPrice()`` — spot/index price in USD

    For N instruments this sends 3×N subcalls in a single RPC round-trip.
    Each call uses ``allowFailure=True`` so a single contract reverting
    (e.g. not yet deployed at that block) does not fail the whole batch.

    :param w3:
        Web3 instance connected to Derive Chain.
    :param contract_addresses:
        List of perp contract addresses (``base_asset_address`` from
        the instruments API).
    :param block_number:
        Block number at which to read state.
    :param sub_id:
        Sub-asset identifier. Always ``0`` for perpetuals.
    :return:
        List of :py:class:`PerpSnapshotMulticallResult` in the same
        order as ``contract_addresses``.
    :raises Exception:
        Transient RPC errors propagate to the caller.
    """
    if not contract_addresses:
        return []

    multicall = get_multicall_contract(w3)

    oi_calldata = bytes.fromhex(PERP_OPEN_INTEREST_SELECTOR + f"{sub_id:064x}")
    perp_price_calldata = bytes.fromhex(PERP_GET_PERP_PRICE_SELECTOR)
    index_price_calldata = bytes.fromhex(PERP_GET_INDEX_PRICE_SELECTOR)

    # Build calls: 3 per instrument (OI, perp price, index price)
    calls = []
    for addr in contract_addresses:
        checksum = w3.to_checksum_address(addr)
        calls.append((checksum, True, oi_calldata))
        calls.append((checksum, True, perp_price_calldata))
        calls.append((checksum, True, index_price_calldata))

    raw_results = multicall.functions.aggregate3(calls).call(
        block_identifier=block_number,
    )

    # Parse results in groups of 3
    results: list[PerpSnapshotMulticallResult] = []
    for i in range(len(contract_addresses)):
        base = i * 3

        # openInterest(uint256) → uint256
        oi_success, oi_data = raw_results[base]
        oi = _decode_uint256(oi_data) if oi_success else None

        # getPerpPrice() → (uint256 price, uint256 confidence)
        pp_success, pp_data = raw_results[base + 1]
        perp_price = _decode_uint256(pp_data) if pp_success else None

        # getIndexPrice() → (uint256 price, uint256 confidence)
        ip_success, ip_data = raw_results[base + 2]
        index_price = _decode_uint256(ip_data) if ip_success else None

        results.append(PerpSnapshotMulticallResult(
            open_interest=oi,
            perp_price=perp_price,
            index_price=index_price,
        ))

    return results


def fetch_instrument_details(
    session: Session,
    base_url: str = DERIVE_MAINNET_API_URL,
    timeout: float = 30.0,
) -> dict[str, dict]:
    """Fetch instrument details including on-chain contract addresses.

    Returns a mapping of instrument name to instrument metadata dict,
    including ``base_asset_address`` (the on-chain perp contract),
    ``scheduled_activation`` (Unix timestamp of listing), and other fields.

    Example::

        from eth_defi.derive.api import fetch_instrument_details
        from eth_defi.derive.session import create_derive_session

        session = create_derive_session()
        details = fetch_instrument_details(session)
        eth = details["ETH-PERP"]
        print(eth["base_asset_address"])  # 0xAf65...
        print(eth["scheduled_activation"])  # 1701820800 (Unix timestamp)

    :param session:
        HTTP session from :py:func:`~eth_defi.derive.session.create_derive_session`.
    :param base_url:
        Derive API base URL.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        Dict mapping instrument name to metadata dict.
    :raises ValueError:
        If the API returns an error response.
    """
    url = f"{base_url}/public/get_all_instruments"
    result_map: dict[str, dict] = {}
    page = 1

    while True:
        params = {
            "instrument_type": "perp",
            "expired": False,
            "page": page,
            "page_size": 1000,
        }
        response = session.post(
            url,
            json=params,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()

        result = _unwrap_result(response.json(), "get_all_instruments")
        page_instruments = result.get("instruments", [])

        for inst in page_instruments:
            name = inst.get("instrument_name")
            if name and inst.get("is_active", True):
                result_map[name] = inst

        pagination = result.get("pagination", {})
        num_pages = pagination.get("num_pages", 1)
        if page >= num_pages:
            break
        page += 1

    logger.info("Fetched details for %d active perpetual instruments", len(result_map))
    return result_map
