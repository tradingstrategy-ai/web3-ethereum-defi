"""Hyperliquid info API client.

Typed wrappers for the `Hyperliquid info endpoint
<https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint>`__.

Uses :py:func:`~eth_defi.hyperliquid.session.create_hyperliquid_session` for
HTTP connections with rate limiting and retry logic.

Example::

    from eth_defi.hyperliquid.api import fetch_user_vault_equities
    from eth_defi.hyperliquid.session import create_hyperliquid_session

    session = create_hyperliquid_session()
    equities = fetch_user_vault_equities(session, user="0xAbc...")
    for eq in equities:
        print(f"Vault {eq.vault_address}: {eq.equity} USDC")
"""

import datetime
import logging
import time
from dataclasses import dataclass
from decimal import Decimal

import requests
from eth_typing import HexAddress

from eth_defi.compat import native_datetime_utc_now
from eth_defi.hyperliquid.session import HyperliquidSession
from eth_defi.utils import from_unix_timestamp

logger = logging.getLogger(__name__)

#: Hyperliquid stats-data leaderboard endpoint (public GET, no auth, 32K+ entries)
LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"

#: Default cache timeout for :py:func:`fetch_user_vault_equity` in seconds (15 minutes).
DEFAULT_VAULT_EQUITY_CACHE_TIMEOUT = 15 * 60

#: Module-level cache: ``(api_url, user) -> (timestamp, list[UserVaultEquity])``
_vault_equity_cache: dict[tuple, tuple[float, list["UserVaultEquity"]]] = {}


@dataclass(slots=True)
class UserVaultEquity:
    """A user's equity position in a single Hypercore vault.

    Returned by :py:func:`fetch_user_vault_equities`.

    Example — check whether a vault deposit can be withdrawn::

        from eth_defi.hyperliquid.api import fetch_user_vault_equity
        from eth_defi.hyperliquid.session import create_hyperliquid_session

        session = create_hyperliquid_session()
        eq = fetch_user_vault_equity(session, user="0xAbc...", vault_address="0xDef...")
        if eq is not None:
            if eq.is_lockup_expired:
                print(f"Withdrawal ready — {eq.equity} USDC available")
            else:
                print(f"Locked for another {eq.lockup_remaining}")
    """

    #: Hypercore vault address
    vault_address: HexAddress

    #: USDC equity in the vault
    equity: Decimal

    #: UTC datetime until which withdrawals are locked.
    #:
    #: User-created vaults have a 1 day lock-up, protocol vaults (HLP) have 4 days.
    locked_until: datetime.datetime

    @property
    def is_lockup_expired(self) -> bool:
        """Whether the lock-up period has passed and withdrawal is allowed.

        Compares :py:attr:`locked_until` against the current UTC time.

        :return:
            ``True`` if the current time is at or past the lock-up deadline.
        """
        return native_datetime_utc_now() >= self.locked_until

    @property
    def lockup_remaining(self) -> datetime.timedelta:
        """Time remaining until the lock-up expires.

        Returns ``timedelta(0)`` if the lock-up has already expired.

        :return:
            Remaining lock-up duration (never negative).
        """
        remaining = self.locked_until - native_datetime_utc_now()
        return max(remaining, datetime.timedelta(0))


@dataclass(slots=True)
class SpotBalance:
    """A single spot token balance on HyperCore.

    Returned inside :py:class:`SpotClearinghouseState`.
    """

    #: Token symbol (e.g. ``"USDC"``, ``"HYPE"``)
    coin: str

    #: Token index on HyperCore
    token: int

    #: Total balance (decimal string parsed to :py:class:`~decimal.Decimal`)
    total: Decimal

    #: Amount on hold (reserved for orders)
    hold: Decimal


@dataclass(slots=True)
class EvmEscrow:
    """USDC bridged to HyperCore but held in EVM escrow.

    When USDC is deposited via ``CoreDepositWallet.deposit()``, it appears
    in the ``evmEscrows`` field of the spot clearinghouse state until
    the HyperCore action is processed.

    Returned inside :py:class:`SpotClearinghouseState`.
    """

    #: Token symbol
    coin: str

    #: Token index
    token: int

    #: Escrowed amount
    total: Decimal


@dataclass(slots=True)
class SpotClearinghouseState:
    """Spot account state for a HyperCore user.

    Returned by :py:func:`fetch_spot_clearinghouse_state`.
    """

    #: Spot token balances
    balances: list[SpotBalance]

    #: USDC in EVM escrow (bridged but not yet processed by HyperCore)
    evm_escrows: list[EvmEscrow]


@dataclass(slots=True)
class MarginSummary:
    """Margin summary for a perpetual account.

    Returned inside :py:class:`PerpClearinghouseState`.
    """

    #: Total account value in USD
    account_value: Decimal

    #: Total notional position size
    total_ntl_pos: Decimal

    #: Total raw USD balance
    total_raw_usd: Decimal

    #: Total margin used
    total_margin_used: Decimal


@dataclass(slots=True)
class AssetPosition:
    """A single perpetual position.

    Returned inside :py:class:`PerpClearinghouseState`.
    """

    #: Perpetual market symbol (e.g. ``"BTC"``, ``"ETH"``)
    coin: str

    #: Position size (negative = short)
    size: Decimal

    #: Entry price
    entry_price: Decimal | None

    #: Unrealised PnL
    unrealised_pnl: Decimal

    #: Margin used for this position
    margin_used: Decimal

    #: Position value in USD
    position_value: Decimal

    #: Liquidation price (``None`` if no position)
    liquidation_price: Decimal | None


@dataclass(slots=True)
class PerpClearinghouseState:
    """Perpetual account state for a HyperCore user.

    Returned by :py:func:`fetch_perp_clearinghouse_state`.
    """

    #: Cross-margin summary
    margin_summary: MarginSummary

    #: Withdrawable balance
    withdrawable: Decimal

    #: Active perpetual positions
    asset_positions: list[AssetPosition]


@dataclass(slots=True)
class PortfolioAllTimeData:
    """All-time PnL and trading volume for a Hyperliquid address.

    Fetched from the ``portfolio`` info endpoint, which works for **any**
    address — not just leaderboard participants.

    The ``pnlHistory`` array in the API response is aggregated data that
    covers the account's full lifetime, unlike fills which are capped at
    ~10K entries per account. The first entry's timestamp therefore gives
    a reliable account creation / first activity date.

    Returned by :py:func:`fetch_portfolio`.
    """

    #: Latest cumulative PnL in USD (from the last entry in ``pnlHistory``)
    all_time_pnl: Decimal | None

    #: All-time trading volume in USD
    all_time_volume: Decimal | None

    #: Timestamp of the first ``pnlHistory`` entry — the account's first
    #: recorded activity. Derived from aggregated data that covers the full
    #: account lifetime (not subject to the ~10K fill API cap).
    first_activity_at: datetime.datetime | None


@dataclass(slots=True)
class LeaderboardEntry:
    """A single trader from the Hyperliquid public leaderboard.

    The leaderboard contains 32K+ traders who have opted in.
    Fetched in bulk by :py:func:`fetch_leaderboard`.
    """

    #: Ethereum address (lowercased)
    address: HexAddress

    #: Display name chosen by the trader (``None`` if not set)
    display_name: str | None

    #: Account value in USD at time of leaderboard snapshot
    account_value: Decimal

    #: All-time PnL in USD
    all_time_pnl: Decimal

    #: All-time ROI as a ratio (e.g. ``0.25`` = 25%)
    all_time_roi: Decimal

    #: All-time trading volume in USD
    all_time_volume: Decimal


def fetch_user_vault_equities(
    session: HyperliquidSession,
    user: HexAddress | str,
    timeout: float = 10.0,
) -> list[UserVaultEquity]:
    """Fetch a user's equity positions across all Hypercore vaults.

    Calls the ``userVaultEquities`` info endpoint to retrieve the user's
    current vault deposits with equity and lock-up status.

    This is the recommended way to verify that a CoreWriter deposit
    landed on HyperCore — no EVM precompile needed.

    Example::

        from eth_defi.hyperliquid.api import fetch_user_vault_equities
        from eth_defi.hyperliquid.session import create_hyperliquid_session, HYPERLIQUID_TESTNET_API_URL

        # Mainnet
        session = create_hyperliquid_session()
        equities = fetch_user_vault_equities(session, user="0xAbc...")

        # Testnet
        session = create_hyperliquid_session(api_url=HYPERLIQUID_TESTNET_API_URL)
        equities = fetch_user_vault_equities(session, user="0xAbc...")

    :param session:
        Session from :py:func:`~eth_defi.hyperliquid.session.create_hyperliquid_session`.

    :param user:
        On-chain address (the Safe address for Lagoon vaults).

    :param timeout:
        HTTP request timeout in seconds.

    :return:
        List of vault equity positions. Empty list if the user has no vault deposits.
    """
    url = f"{session.api_url}/info"
    payload = {"type": "userVaultEquities", "user": user}

    logger.debug("Fetching userVaultEquities for %s from %s", user, url)

    response = session.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()

    results = []
    for entry in data:
        results.append(
            UserVaultEquity(
                vault_address=entry["vaultAddress"],
                equity=Decimal(entry["equity"]),
                locked_until=from_unix_timestamp(entry["lockedUntilTimestamp"] / 1000),
            )
        )

    logger.info(
        "User %s has %d vault position(s)",
        user,
        len(results),
    )

    return results


def fetch_user_vault_equity(
    session: HyperliquidSession,
    user: HexAddress | str,
    vault_address: HexAddress | str,
    cache_timeout: float = DEFAULT_VAULT_EQUITY_CACHE_TIMEOUT,
    timeout: float = 10.0,
    bypass_cache: bool = False,
) -> UserVaultEquity | None:
    """Fetch a user's equity in a single Hypercore vault, with caching.

    Convenience wrapper around :py:func:`fetch_user_vault_equities` that
    fetches all vault positions, caches the result, and returns the one
    matching *vault_address*.

    The cache is keyed by ``(api_url, user)`` and entries expire after
    *cache_timeout* seconds (default 15 minutes).

    Example::

        from eth_defi.hyperliquid.api import fetch_user_vault_equity
        from eth_defi.hyperliquid.session import create_hyperliquid_session

        session = create_hyperliquid_session()
        eq = fetch_user_vault_equity(session, user="0xAbc...", vault_address="0xDef...")
        if eq is not None:
            print(f"Equity: {eq.equity} USDC")

    :param session:
        Session from :py:func:`~eth_defi.hyperliquid.session.create_hyperliquid_session`.

    :param user:
        On-chain address (the Safe address for Lagoon vaults).

    :param vault_address:
        Hypercore vault address to look up.

    :param cache_timeout:
        How long cached results stay valid, in seconds.
        Defaults to :py:data:`DEFAULT_VAULT_EQUITY_CACHE_TIMEOUT` (15 minutes).

    :param timeout:
        HTTP request timeout in seconds (passed to the underlying API call).

    :param bypass_cache:
        If ``True``, skip the cache and always fetch fresh data from the API.
        The fresh result is still stored in the cache for subsequent calls.

    :return:
        The user's equity in the vault, or ``None`` if the user has no
        position in the given vault.
    """
    cache_key = (session.api_url, user.lower())
    now = time.time()

    cached = None
    if not bypass_cache:
        cached = _vault_equity_cache.get(cache_key)
        if cached is not None:
            cached_at, equities = cached
            if now - cached_at < cache_timeout:
                logger.debug("Using cached vault equities for %s (age %.0fs)", user, now - cached_at)
            else:
                cached = None

    if cached is None:
        equities = fetch_user_vault_equities(session, user, timeout=timeout)
        _vault_equity_cache[cache_key] = (now, equities)

    needle = vault_address.lower()
    for eq in equities:
        if eq.vault_address.lower() == needle:
            return eq
    return None


def fetch_vault_lockup_status(
    session: HyperliquidSession,
    user: HexAddress | str,
    vault_address: HexAddress | str,
    cache_timeout: float = DEFAULT_VAULT_EQUITY_CACHE_TIMEOUT,
    timeout: float = 10.0,
) -> UserVaultEquity | None:
    """Fetch a user's vault position and check whether the lock-up has expired.

    Convenience wrapper around :py:func:`fetch_user_vault_equity` that
    fetches the position (with caching) and returns it with lock-up
    status available via :py:attr:`UserVaultEquity.is_lockup_expired`
    and :py:attr:`UserVaultEquity.lockup_remaining`.

    Returns ``None`` if the user has no position in the vault.

    Example::

        from eth_defi.hyperliquid.api import fetch_vault_lockup_status
        from eth_defi.hyperliquid.session import create_hyperliquid_session

        session = create_hyperliquid_session()
        eq = fetch_vault_lockup_status(session, user="0xAbc...", vault_address="0xDef...")
        if eq is not None:
            if eq.is_lockup_expired:
                print("Withdrawal ready")
            else:
                print(f"Locked for another {eq.lockup_remaining}")
        else:
            print("No position in this vault")

    :param session:
        Session from :py:func:`~eth_defi.hyperliquid.session.create_hyperliquid_session`.

    :param user:
        On-chain address (the Safe address for Lagoon vaults).

    :param vault_address:
        Hypercore vault address.

    :param cache_timeout:
        Cache timeout in seconds.

    :param timeout:
        HTTP request timeout in seconds.

    :return:
        The user's vault equity with lock-up properties, or ``None`` if no position.
    """
    return fetch_user_vault_equity(
        session,
        user=user,
        vault_address=vault_address,
        cache_timeout=cache_timeout,
        timeout=timeout,
    )


def fetch_spot_clearinghouse_state(
    session: HyperliquidSession,
    user: HexAddress | str,
    timeout: float = 10.0,
) -> SpotClearinghouseState:
    """Fetch a user's spot account state on HyperCore.

    Calls the ``spotClearinghouseState`` info endpoint to retrieve
    spot token balances and EVM escrow amounts.

    :param session:
        Session from :py:func:`~eth_defi.hyperliquid.session.create_hyperliquid_session`.

    :param user:
        On-chain address.

    :param timeout:
        HTTP request timeout in seconds.

    :return:
        Spot clearinghouse state with balances and EVM escrows.
    """
    url = f"{session.api_url}/info"
    payload = {"type": "spotClearinghouseState", "user": user}

    logger.debug("Fetching spotClearinghouseState for %s from %s", user, url)

    response = session.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()

    balances = [
        SpotBalance(
            coin=b["coin"],
            token=b["token"],
            total=Decimal(b["total"]),
            hold=Decimal(b.get("hold", "0")),
        )
        for b in data.get("balances", [])
    ]

    evm_escrows = [
        EvmEscrow(
            coin=e["coin"],
            token=e["token"],
            total=Decimal(e["total"]),
        )
        for e in data.get("evmEscrows", [])
    ]

    logger.info(
        "User %s: %d spot balance(s), %d EVM escrow(s)",
        user,
        len(balances),
        len(evm_escrows),
    )

    return SpotClearinghouseState(balances=balances, evm_escrows=evm_escrows)


def fetch_perp_clearinghouse_state(
    session: HyperliquidSession,
    user: HexAddress | str,
    timeout: float = 10.0,
) -> PerpClearinghouseState:
    """Fetch a user's perpetual account state on HyperCore.

    Calls the ``clearinghouseState`` info endpoint to retrieve
    margin summary, withdrawable balance, and open positions.

    :param session:
        Session from :py:func:`~eth_defi.hyperliquid.session.create_hyperliquid_session`.

    :param user:
        On-chain address.

    :param timeout:
        HTTP request timeout in seconds.

    :return:
        Perpetual clearinghouse state with margin info and positions.
    """
    url = f"{session.api_url}/info"
    payload = {"type": "clearinghouseState", "user": user}

    logger.debug("Fetching clearinghouseState for %s from %s", user, url)

    response = session.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()

    ms = data["crossMarginSummary"]
    margin_summary = MarginSummary(
        account_value=Decimal(ms["accountValue"]),
        total_ntl_pos=Decimal(ms["totalNtlPos"]),
        total_raw_usd=Decimal(ms["totalRawUsd"]),
        total_margin_used=Decimal(ms["totalMarginUsed"]),
    )

    positions = []
    for ap in data.get("assetPositions", []):
        pos = ap.get("position", ap)
        liq_px = pos.get("liquidationPx")
        entry_px = pos.get("entryPx")
        positions.append(
            AssetPosition(
                coin=pos["coin"],
                size=Decimal(pos.get("szi", "0")),
                entry_price=Decimal(entry_px) if entry_px else None,
                unrealised_pnl=Decimal(pos.get("unrealizedPnl", "0")),
                margin_used=Decimal(pos.get("marginUsed", "0")),
                position_value=Decimal(pos.get("positionValue", "0")),
                liquidation_price=Decimal(liq_px) if liq_px else None,
            )
        )

    logger.info(
        "User %s: perp account value %s, %d position(s)",
        user,
        margin_summary.account_value,
        len(positions),
    )

    return PerpClearinghouseState(
        margin_summary=margin_summary,
        withdrawable=Decimal(data.get("withdrawable", "0")),
        asset_positions=positions,
    )


def fetch_portfolio(
    session: HyperliquidSession,
    address: HexAddress | str,
    timeout: float = 15.0,
) -> PortfolioAllTimeData | None:
    """Fetch all-time PnL and volume for any Hyperliquid address.

    Calls the ``portfolio`` info endpoint which returns account value history,
    PnL history, and volume across multiple time windows (day, week, month, allTime).

    Unlike the leaderboard, this works for **any** address — including those
    that have not opted in to the public leaderboard.

    Example::

        from eth_defi.hyperliquid.api import fetch_portfolio
        from eth_defi.hyperliquid.session import create_hyperliquid_session

        session = create_hyperliquid_session()
        portfolio = fetch_portfolio(session, "0x1234...")
        if portfolio is not None:
            print(f"All-time PnL: {portfolio.all_time_pnl}")
            print(f"All-time volume: {portfolio.all_time_volume}")
            # Example output:
            # All-time PnL: -58459.412942
            # All-time volume: 1893425014.9738

    The raw API response is an array of ``[period, data]`` pairs::

        [["day", {"accountValueHistory": [...], "pnlHistory": [...], "vlm": "..."}], ["allTime", {"accountValueHistory": [...], "pnlHistory": [[ts, pnl], ...], "vlm": "1893425014.9738"}]]

    :param session:
        Session from :py:func:`~eth_defi.hyperliquid.session.create_hyperliquid_session`.

    :param address:
        Hyperliquid user address.

    :param timeout:
        HTTP request timeout in seconds.

    :return:
        All-time PnL, volume, and first activity timestamp,
        or ``None`` on network/API error.
    """
    try:
        url = f"{session.api_url}/info"
        resp = session.post(
            url,
            json={"type": "portfolio", "user": address},
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        # Response is array of [period, {accountValueHistory, pnlHistory, vlm}]
        periods = dict(data)
        all_time = periods.get("allTime", {})
        pnl_history = all_time.get("pnlHistory", [])
        latest_pnl = Decimal(str(pnl_history[-1][1])) if pnl_history else None
        vlm = all_time.get("vlm")
        volume = Decimal(str(vlm)) if vlm else None

        # First pnlHistory entry timestamp = account first activity.
        # pnlHistory is aggregated data covering the full account lifetime,
        # not subject to the ~10K fill API cap.
        first_activity_at = None
        if pnl_history:
            first_ts_ms = pnl_history[0][0]
            first_activity_at = datetime.datetime.fromtimestamp(first_ts_ms / 1000)

        logger.info(
            "Portfolio for %s: pnl=%s, volume=%s, first_activity=%s",
            address,
            latest_pnl,
            volume,
            first_activity_at,
        )

        return PortfolioAllTimeData(
            all_time_pnl=latest_pnl,
            all_time_volume=volume,
            first_activity_at=first_activity_at,
        )
    except Exception:
        logger.warning("Failed to fetch portfolio for %s", address, exc_info=True)
        return None


def fetch_vault_name(
    session: HyperliquidSession,
    vault_address: HexAddress | str,
    timeout: float = 10.0,
) -> str | None:
    """Fetch the display name of a Hyperliquid vault.

    Makes a lightweight ``vaultDetails`` API call and extracts only the
    vault name. Returns ``None`` if the vault is not found or the request
    fails.

    Example::

        from eth_defi.hyperliquid.api import fetch_vault_name
        from eth_defi.hyperliquid.session import create_hyperliquid_session

        session = create_hyperliquid_session()
        name = fetch_vault_name(session, "0x15be61aef0ea4e4dc93c79b668f26b3f1be75a66")
        print(name)  # e.g. "Growi HF"

    :param session:
        Session from :py:func:`~eth_defi.hyperliquid.session.create_hyperliquid_session`.
    :param vault_address:
        Vault address to look up.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        Vault display name, or ``None`` if not found.
    """
    url = f"{session.api_url}/info"
    payload = {"type": "vaultDetails", "vaultAddress": vault_address}

    try:
        response = session.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("name") or None
    except requests.RequestException:
        logger.warning("Failed to fetch vault name for %s", vault_address, exc_info=True)
        return None


def fetch_leaderboard(
    timeout: float = 60.0,
) -> dict[str, LeaderboardEntry]:
    """Fetch the full Hyperliquid trader leaderboard.

    Calls the public ``stats-data.hyperliquid.xyz/Mainnet/leaderboard``
    endpoint which returns 32K+ traders who have opted in, indexed by
    lowercased address for easy lookup.

    Does **not** require a :py:class:`HyperliquidSession` — this is a
    plain GET to a stats endpoint with no rate limiting.

    Example::

        from eth_defi.hyperliquid.api import fetch_leaderboard

        leaderboard = fetch_leaderboard()
        print(f"Leaderboard has {len(leaderboard)} traders")

        # Look up a specific address
        entry = leaderboard.get("0x1234abcd...")
        if entry:
            print(f"{entry.display_name}: PnL={entry.all_time_pnl}, ROI={entry.all_time_roi}")
            # Example output:
            # HyperTrader42: PnL=1234567.89, ROI=0.4523

    The raw API response::

        {"leaderboardRows": [{"ethAddress": "0x...", "accountValue": "123456.78", "displayName": "HyperTrader42", "windowPerformances": [["allTime", {"pnl": "1234567.89", "roi": "0.4523", "vlm": "98765432.10"}], ...]}, ...]}

    :param timeout:
        HTTP request timeout in seconds.

    :return:
        Dict mapping lowercased address to :py:class:`LeaderboardEntry`.
    """
    logger.info("Fetching leaderboard from %s", LEADERBOARD_URL)
    resp = requests.get(LEADERBOARD_URL, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    rows = data["leaderboardRows"]
    logger.info("Got %d leaderboard entries", len(rows))

    index: dict[str, LeaderboardEntry] = {}
    for row in rows:
        addr = row["ethAddress"].lower()
        windows = dict(row.get("windowPerformances", []))
        all_time = windows.get("allTime", {})
        index[addr] = LeaderboardEntry(
            address=addr,
            display_name=row.get("displayName") or None,
            account_value=Decimal(str(row.get("accountValue", 0))),
            all_time_pnl=Decimal(str(all_time.get("pnl", 0))),
            all_time_roi=Decimal(str(all_time.get("roi", 0))),
            all_time_volume=Decimal(str(all_time.get("vlm", 0))),
        )
    return index
