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
from dataclasses import dataclass
from decimal import Decimal

from eth_typing import HexAddress

from eth_defi.hyperliquid.session import HyperliquidSession
from eth_defi.utils import from_unix_timestamp

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UserVaultEquity:
    """A user's equity position in a single Hypercore vault.

    Returned by :py:func:`fetch_user_vault_equities`.
    """

    #: Hypercore vault address
    vault_address: HexAddress

    #: USDC equity in the vault
    equity: Decimal

    #: UTC datetime until which withdrawals are locked.
    #:
    #: User-created vaults have a 1 day lock-up, protocol vaults (HLP) have 4 days.
    locked_until: datetime.datetime


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
