"""GMX Subsquid GraphQL client for fetching positions and historical data.

This provides an alternative to direct contract reads using the Subsquid indexer:
- Faster queries (no blockchain RPC calls)
- Historical data and analytics
- PnL tracking across time periods
- Position history and changes

The original contract-based implementation (GetOpenPositions) remains the source
of truth for on-chain data when executing trades.
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any, Optional

import requests
from eth_utils import is_address, to_checksum_address

logger = logging.getLogger(__name__)

from eth_defi.gmx.constants import GMX_MIN_DISPLAY_STAKE
from eth_defi.gmx.contracts import GMX_SUBSQUID_ENDPOINTS, GMX_SUBSQUID_ENDPOINTS_BACKUP, get_tokens_metadata_dict

# Thresholds from GMX interface (in USD, 30 decimals)
# Just Random values ChatGPT gave
MAX_DAILY_VOLUME = 340_000
ROLLING_14_DAY_VOLUME = 1_800_000
ALL_TIME_VOLUME = 5_800_000


class GMXSubsquidClient:
    """Client for querying GMX data via Subsquid GraphQL endpoint.

    This client fetches position and PnL data from the GMX Subsquid indexer,
    providing fast access to current and historical trading data.

    .. important::
        **GraphQL Limitations:** This client provides snapshot position data but lacks
        real-time borrowing/funding fields that require on-chain computation:

        - borrowing_factor
        - funding_fee_amount_per_size
        - long_token_claimable_funding_amount_per_size
        - short_token_claimable_funding_amount_per_size

        These fields require the Reader contract and are only available via
        ``GetOpenPositions`` with ``use_graphql=False``.

        **Trade-off:** GraphQL is 10-100x faster but provides static data.
        Use RPC for real-time funding/borrowing calculations.

    Example usage::

        # Create client
        client = GMXSubsquidClient()

        # Get open positions (fast, but no borrowing/funding data)
        positions = client.get_positions(account="0x1234...", only_open=True)

        # Format position with proper decimal handling
        formatted = client.format_position(positions[0])

        # Get PnL summary
        pnl_summary = client.get_pnl_summary(account="0x1234...")

        # Get position history
        history = client.get_position_changes(account="0x1234...", limit=50)
    """

    MIN_DISPLAY_STAKE = GMX_MIN_DISPLAY_STAKE

    def __init__(
        self,
        chain: str = "arbitrum",
        custom_endpoint: Optional[str] = None,
    ):
        """Initialize the Subsquid client.

        :param chain: Chain name ("arbitrum", "avalanche", or "arbitrum_sepolia")
        :param custom_endpoint: Optional custom GraphQL endpoint URL
        """
        self.chain = chain.lower()

        if custom_endpoint:
            self.endpoint = custom_endpoint
            self.endpoint_backup = None
        elif self.chain in GMX_SUBSQUID_ENDPOINTS:
            self.endpoint = GMX_SUBSQUID_ENDPOINTS[self.chain]
            self.endpoint_backup = GMX_SUBSQUID_ENDPOINTS_BACKUP.get(self.chain)
        else:
            raise ValueError(
                f"Unsupported chain: {chain}. Supported chains: {', '.join(GMX_SUBSQUID_ENDPOINTS.keys())}",
            )

        # Cache token metadata from GMX API (address -> {symbol, decimals, synthetic})
        self._tokens_metadata: Optional[dict[str, dict]] = None
        # Cache markets data (market_address -> {indexToken, longToken, shortToken})
        self._markets_cache: Optional[dict[str, dict]] = None

        # HTTP session with connection pooling for better performance
        self._session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=10,
            max_retries=3,
        )
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def _get_tokens_metadata(self) -> dict[str, dict]:
        """Get token metadata from GMX API, with caching.

        :return: Dictionary mapping token addresses to metadata {symbol, decimals, synthetic}
        """
        if self._tokens_metadata is None:
            self._tokens_metadata = get_tokens_metadata_dict(self.chain)
        return self._tokens_metadata

    def _query(
        self,
        query: str,
        variables: Optional[dict[str, Any]] = None,
        timeout: int = 60,
    ) -> dict[str, Any]:
        """Execute a GraphQL query with automatic failover to backup endpoint.

        :param query: GraphQL query string
        :param variables: Optional query variables
        :param timeout: Request timeout in seconds (default 60)
        :return: Query response data
        :raises requests.HTTPError: If the request fails on all endpoints
        :raises ValueError: If GraphQL returns errors
        """
        endpoints_to_try = [self.endpoint]
        if self.endpoint_backup:
            endpoints_to_try.append(self.endpoint_backup)

        last_error = None
        for endpoint in endpoints_to_try:
            try:
                response = self._session.post(
                    endpoint,
                    json={"query": query, "variables": variables or {}},
                    headers={"Content-Type": "application/json"},
                    timeout=timeout,
                )
                response.raise_for_status()

                data = response.json()

                if "errors" in data:
                    errors = ", ".join(err["message"] for err in data["errors"])
                    raise ValueError(f"GraphQL query failed: {errors}")

                # Log if we used backup endpoint
                if endpoint != self.endpoint:
                    logger.info("Successfully used backup Subsquid endpoint")

                return data.get("data", {})

            except (requests.RequestException, requests.Timeout) as e:
                last_error = e
                logger.warning(
                    "Subsquid query failed on %s: %s",
                    endpoint.split("/")[2],  # Extract domain
                    e,
                )
                continue

        # All endpoints failed
        raise last_error

    def _query_with_retry(
        self,
        query: str,
        variables: Optional[dict[str, Any]] = None,
        timeout: int = 60,
        max_retries: int = 3,
        method_name: str = "query",
    ) -> dict[str, Any]:
        """Execute a GraphQL query with retry logic.

        Wraps _query() with exponential backoff retry logic to handle
        Subsquid timeouts and transient failures gracefully.

        :param query: GraphQL query string
        :param variables: Optional query variables
        :param timeout: Request timeout in seconds
        :param max_retries: Maximum retry attempts (default 3)
        :param method_name: Method name for logging
        :return: Query response data
        :raises Exception: If all retries fail
        """
        for attempt in range(max_retries):
            try:
                return self._query(query, variables=variables, timeout=timeout)
            except Exception as e:
                if attempt < max_retries - 1:
                    backoff_time = 2 ** (attempt + 1)  # 2s, 4s, 8s
                    logger.warning(
                        "Subsquid %s attempt %d/%d failed: %s. Retrying in %ds...",
                        method_name,
                        attempt + 1,
                        max_retries,
                        e,
                        backoff_time,
                    )
                    time.sleep(backoff_time)
                else:
                    logger.error(
                        "Subsquid %s failed after %d retries: %s",
                        method_name,
                        max_retries,
                        e,
                    )
                    raise

    def get_positions(
        self,
        account: str,
        only_open: bool = True,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get positions for an account.

        .. note::
            GraphQL provides fast queries but lacks real-time borrowing/funding data.
            Fields NOT available in GraphQL (require on-chain Reader contract):

            - borrowing_factor
            - funding_fee_amount_per_size
            - long_token_claimable_funding_amount_per_size
            - short_token_claimable_funding_amount_per_size

            For these fields, use GetOpenPositions with use_graphql=False.

        :param account: Wallet address (checksummed or lowercase)
        :param only_open: If True, only return positions with size > 0
        :param limit: Maximum number of positions to return
        :return: List of position dictionaries with fields:

            - id: Position ID
            - positionKey: Unique position key
            - account: Account address
            - market: Market address
            - collateralToken: Collateral token address
            - isLong: True for long, False for short
            - collateralAmount: Collateral amount (BigInt as string)
            - sizeInUsd: Position size in USD (30 decimals, BigInt as string)
            - sizeInTokens: Position size in tokens (index_token_decimals, BigInt as string)
            - entryPrice: Entry price (30-index_decimals, BigInt as string)
            - realizedPnl: Realized PnL (30 decimals, BigInt as string)
            - unrealizedPnl: Unrealized PnL (30 decimals, BigInt as string)
            - realizedFees: Realized fees (30 decimals, BigInt as string)
            - unrealizedFees: Unrealized fees (30 decimals, BigInt as string)
            - leverage: Leverage (4 decimals, BigInt as string, 10000 = 1x)
            - openedAt: Opening timestamp
        """
        query = """
        query GetPositions($account: String!, $limit: Int!) {
          positions(
            limit: $limit,
            where: {
              account_eq: $account
            }
          ) {
            id
            positionKey
            account
            market
            collateralToken
            isLong
            collateralAmount
            sizeInTokens
            sizeInUsd
            entryPrice
            realizedPnl
            unrealizedPnl
            realizedFees
            unrealizedFees
            realizedPriceImpact
            unrealizedPriceImpact
            leverage
            openedAt
          }
        }
        """

        data = self._query(
            query,
            variables={
                "account": account,  # Keep original case - Subsquid is case-sensitive
                "limit": limit,
            },
        )

        positions = data.get("positions", [])

        # Filter for open positions if requested
        if only_open:
            positions = [p for p in positions if int(p["sizeInUsd"]) > 0]

        return positions

    def get_position_by_key(self, position_key: str) -> Optional[dict[str, Any]]:
        """Get a specific position by its key.

        :param position_key: Position key (bytes32 hex string)
        :return: Position dictionary or None if not found
        """
        query = """
        query GetPositionByKey($positionKey: String!) {
          positions(
            where: {
              positionKey_eq: $positionKey
            }
            limit: 1
          ) {
            id
            positionKey
            account
            market
            collateralToken
            isLong
            collateralAmount
            sizeInTokens
            sizeInUsd
            entryPrice
            realizedPnl
            unrealizedPnl
            realizedFees
            unrealizedFees
            leverage
            openedAt
          }
        }
        """

        data = self._query(query, variables={"positionKey": position_key})
        positions = data.get("positions", [])

        return positions[0] if positions else None

    def get_pnl_summary(self, account: str) -> list[dict[str, Any]]:
        """Get PnL summary statistics for an account across time periods.

        :param account: Wallet address (checksummed or lowercase)
        :return: List of PnL summary dictionaries, one per time period:

            - bucketLabel: Period label ("today", "yesterday", "week", "month", "year", "all")
            - pnlUsd: Total PnL in USD (30 decimals, BigInt as string)
            - realizedPnlUsd: Realized PnL (30 decimals, BigInt as string)
            - unrealizedPnlUsd: Unrealized PnL (30 decimals, BigInt as string)
            - startUnrealizedPnlUsd: Unrealized PnL at period start (30 decimals)
            - volume: Total trading volume (30 decimals, BigInt as string)
            - wins: Number of winning trades
            - losses: Number of losing trades
            - winsLossesRatioBps: Win/loss ratio in basis points
            - usedCapitalUsd: Total capital used (30 decimals, BigInt as string)
            - pnlBps: PnL in basis points
        """
        query = """
        query AccountPnlSummary($account: String!) {
          accountPnlSummaryStats(account: $account) {
            bucketLabel
            losses
            pnlBps
            pnlUsd
            realizedPnlUsd
            unrealizedPnlUsd
            startUnrealizedPnlUsd
            volume
            wins
            winsLossesRatioBps
            usedCapitalUsd
          }
        }
        """

        data = self._query(query, variables={"account": account})
        return data.get("accountPnlSummaryStats", [])

    def get_position_changes(
        self,
        account: Optional[str] = None,
        position_key: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get position change history (increases/decreases).

        :param account: Optional wallet address filter
        :param position_key: Optional position key filter
        :param limit: Maximum number of changes to return
        :return: List of position change dictionaries with fields:

            - id: Change ID
            - account: Account address
            - market: Market address
            - collateralToken: Collateral token address
            - isLong: True for long, False for short
            - sizeInUsd: Position size after this change (30 decimals)
            - collateralAmount: Collateral amount after this change (30 decimals)
        """
        # Build where clause
        where_conditions = []
        variables = {"limit": limit}

        if account:
            where_conditions.append("account_eq: $account")
            variables["account"] = account  # Keep original case

        if position_key:
            where_conditions.append("positionKey_eq: $positionKey")
            variables["positionKey"] = position_key

        where_clause = ", ".join(where_conditions) if where_conditions else ""

        query = f"""
        query GetPositionChanges($limit: Int!{", $account: String!" if account else ""}{", $positionKey: String!" if position_key else ""}) {{
          positionChanges(
            limit: $limit
            {f"where: {{ {where_clause} }}" if where_clause else ""}
          ) {{
            id
            account
            market
            collateralToken
            isLong
            sizeInUsd
            collateralAmount
          }}
        }}
        """

        # Use retry logic to handle Subsquid timeouts gracefully
        try:
            data = self._query_with_retry(
                query,
                variables=variables,
                timeout=60,
                max_retries=3,
                method_name="get_position_changes",
            )
            return data.get("positionChanges", [])
        except Exception:
            # Return empty list instead of crashing Freqtrade
            logger.error("get_position_changes failed, returning empty list")
            return []

    def get_account_stats(self, account: str) -> Optional[dict[str, Any]]:
        """Get overall account statistics.

        :param account: Wallet address (checksummed or lowercase)
        :return: Account statistics dictionary or None if not found with fields:

            - id: Account address (lowercase)
            - volume: Total trading volume (30 decimals)
            - closedCount: Number of closed positions
            - wins: Number of winning trades
            - losses: Number of losing trades
            - realizedPnl: Realized PnL (30 decimals)
            - realizedFees: Realized fees (30 decimals)
            - realizedPriceImpact: Realized price impact (30 decimals)
            - cumsumCollateral: Cumulative collateral used (30 decimals)
            - cumsumSize: Cumulative position size (30 decimals)
            - sumMaxSize: Sum of max position sizes (30 decimals)
            - maxCapital: Maximum capital used (30 decimals)
            - netCapital: Net capital (30 decimals)
        """
        query = """
        query GetAccountStats($account: String!) {
          accountStats(
            where: {
              id_eq: $account
            }
            limit: 1
          ) {
            id
            volume
            closedCount
            wins
            losses
            realizedPnl
            realizedFees
            realizedPriceImpact
            cumsumCollateral
            cumsumSize
            sumMaxSize
            maxCapital
            netCapital
          }
        }
        """

        data = self._query(query, variables={"account": account})
        stats = data.get("accountStats", [])

        return stats[0] if stats else None

    def get_market_infos(
        self,
        market_address: Optional[str] = None,
        limit: int = 100,
        order_by: str = "id_DESC",
    ) -> list[dict[str, Any]]:
        """Get market information snapshots.

        Retrieves historical market data including open interest, funding rates,
        and borrowing rates.

        :param market_address: Filter by specific market address
        :param limit: Maximum number of records to return
        :param order_by: Sort order (e.g., "id_DESC")
        :return: List of market info snapshots with fields:

            - id: Market info ID
            - marketTokenAddress: Market token address
            - indexTokenAddress: Index token address
            - longTokenAddress: Long token address
            - shortTokenAddress: Short token address
            - longOpenInterestUsd: Long open interest in USD (30 decimals)
            - shortOpenInterestUsd: Short open interest in USD (30 decimals)
            - longOpenInterestInTokens: Long open interest in tokens
            - shortOpenInterestInTokens: Short open interest in tokens
            - fundingFactorPerSecond: Funding rate per second (30 decimals)
            - longsPayShorts: Direction of funding (True if longs pay shorts)
            - borrowingFactorPerSecondForLongs: Borrowing rate for longs (30 decimals)
            - borrowingFactorPerSecondForShorts: Borrowing rate for shorts (30 decimals)
            - minCollateralFactor: Minimum collateral factor (30 decimals)
            - minCollateralFactorForOpenInterestLong: Multiplier for long OI-based collateral (30 decimals)
            - minCollateralFactorForOpenInterestShort: Multiplier for short OI-based collateral (30 decimals)
            - maxOpenInterestLong: Maximum allowed long open interest (30 decimals)
            - maxOpenInterestShort: Maximum allowed short open interest (30 decimals)
        """
        where_clause = ""
        if market_address:
            where_clause = f'where: {{ marketTokenAddress_eq: "{market_address}" }}'

        query = f"""
        query {{
          marketInfos(
            {where_clause}
            orderBy: [{order_by}]
            limit: {limit}
          ) {{
            id
            marketTokenAddress
            indexTokenAddress
            longTokenAddress
            shortTokenAddress
            longOpenInterestUsd
            shortOpenInterestUsd
            longOpenInterestInTokens
            shortOpenInterestInTokens
            fundingFactorPerSecond
            longsPayShorts
            borrowingFactorPerSecondForLongs
            borrowingFactorPerSecondForShorts
            minCollateralFactor
            minCollateralFactorForOpenInterestLong
            minCollateralFactorForOpenInterestShort
            maxOpenInterestLong
            maxOpenInterestShort
          }}
        }}
        """

        result = self._query(query)
        return result.get("marketInfos", [])

    def get_borrowing_rate_snapshots(
        self,
        market_address: Optional[str] = None,
        is_long: Optional[bool] = None,
        since_timestamp: Optional[int] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get historical borrowing rate snapshots.

        :param market_address: Filter by market address
        :param is_long: Filter by long (True) or short (False) positions
        :param since_timestamp: Filter records after this timestamp (seconds)
        :param limit: Maximum number of records
        :return: List of borrowing rate snapshots with fields:

            - id: Snapshot ID
            - marketAddress: Market address
            - isLong: True for long positions, False for short
            - borrowingRate: Borrowing rate (30 decimals)
            - timestamp: Timestamp in seconds
        """
        where_conditions = []
        if market_address:
            where_conditions.append(f'marketAddress_eq: "{market_address}"')
        if is_long is not None:
            where_conditions.append(f"isLong_eq: {str(is_long).lower()}")
        if since_timestamp:
            where_conditions.append(f"timestamp_gte: {since_timestamp}")

        where_clause = ""
        if where_conditions:
            where_clause = f"where: {{ {', '.join(where_conditions)} }}"

        query = f"""
        query {{
          borrowingRateSnapshots(
            {where_clause}
            orderBy: [timestamp_DESC]
            limit: {limit}
          ) {{
            id
            marketAddress
            isLong
            borrowingRate
            timestamp
          }}
        }}
        """

        result = self._query(query)
        return result.get("borrowingRateSnapshots", [])

    def get_markets(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get all available markets.

        :param limit: Maximum number of markets to return (default 100)
        :return: List of markets with fields:

            - id: Market address
            - indexToken: Index token address
            - longToken: Long token address
            - shortToken: Short token address
        """
        query = f"""
        query {{
          markets(limit: {limit}) {{
            id
            indexToken
            longToken
            shortToken
          }}
        }}
        """

        result = self._query(query)
        return result.get("markets", [])

    def is_large_account(self, account: str) -> bool:
        """Determine if an account qualifies as a "large" account.

        Based on GMX interface criteria, an account is considered large if it meets ANY of:

        - Maximum single-day volume > $340,000
        - 14-day cumulative volume > $1,800,000
        - All-time volume > $5,800,000

        :param account: Wallet address (checksummed or lowercase)
        :return: True if account meets large account criteria, False otherwise
        """
        # Fetch stats and PnL summary in parallel for better performance
        stats = None
        pnl_summary = None

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                stats_future = executor.submit(self.get_account_stats, account)
                pnl_future = executor.submit(self.get_pnl_summary, account)

                stats = stats_future.result(timeout=30)
                pnl_summary = pnl_future.result(timeout=30)
        except Exception as e:
            logger.warning("Parallel query failed, falling back to sequential: %s", e)
            stats = self.get_account_stats(account)
            pnl_summary = self.get_pnl_summary(account)

        if not stats:
            return False

        all_time_volume = float(self.from_fixed_point(stats["volume"]))

        # Check all-time volume threshold
        if all_time_volume > ALL_TIME_VOLUME:
            return True

        # Check 14-day volume (week bucket includes last 7 days, we approximate with month data)
        for bucket in pnl_summary:
            if bucket["bucketLabel"] == "week":
                week_volume = float(self.from_fixed_point(bucket["volume"]))
                # Approximate 14-day as 2x weekly volume
                if week_volume * 2 > ROLLING_14_DAY_VOLUME:
                    return True

            # Check for high daily volume in recent activity
            if bucket["bucketLabel"] in ["today", "yesterday"]:
                daily_volume = float(self.from_fixed_point(bucket["volume"]))
                if daily_volume > MAX_DAILY_VOLUME:
                    return True

        return False

    @staticmethod
    def from_fixed_point(value: str, decimals: int = 30) -> Decimal:
        """Convert fixed-point integer string to Decimal with proper scaling.

        :param value: Fixed-point integer value as string
        :param decimals: Number of decimals (default 30 for USD values)
        :return: Decimal value scaled by decimals

        Example::

            >>> from_fixed_point("8625000000000000000000000000000", 30)
            Decimal('8.625')
        """
        return Decimal(value) / Decimal(10**decimals)

    @staticmethod
    def calculate_max_leverage(min_collateral_factor: str) -> float | None:
        """Calculate maximum UI leverage from minCollateralFactor.

        Positions are liquidated when collateral factor falls below minCollateralFactor.
        The theoretical max leverage is 1 / minCollateralFactor, but we apply a 2x buffer
        to prevent positions from being liquidated immediately upon opening.

        :param min_collateral_factor: Minimum collateral factor (30 decimals, as string)
        :return: Maximum leverage to display in UI (e.g., 100.0 for 100x), or None if invalid

        Example::

            >>> # minCollateralFactor = 0.5% = "5000000000000000000000000000"
            >>> calculate_max_leverage("5000000000000000000000000000")
            100.0
            >>> # minCollateralFactor = 1% = "10000000000000000000000000000"
            >>> calculate_max_leverage("10000000000000000000000000000")
            50.0
            >>> # minCollateralFactor = 0 (invalid)
            >>> calculate_max_leverage("0")
            None
        """
        # Parse the 30-decimal value to get decimal representation
        min_collateral_decimal = Decimal(min_collateral_factor) / Decimal(10**30)

        # Handle zero or negative values
        if min_collateral_decimal <= 0:
            return None

        # Max theoretical leverage = 1 / minCollateralFactor
        # UI leverage = theoretical / 2 (to provide liquidation buffer)
        # Simplified: max_ui_leverage = 1 / minCollateralFactor / 2
        max_ui_leverage = Decimal(1) / min_collateral_decimal / Decimal(2)

        return float(max_ui_leverage)

    @staticmethod
    def calculate_leverage_tiers(
        market_info: dict[str, Any],
        is_long: bool,
        num_tiers: int = 5,
    ) -> list[dict[str, Any]]:
        """Calculate leverage tiers for a market based on open interest.

        GMX uses a continuous leverage model where minimum collateral requirements increase
        with open interest. This method approximates the continuous model as discrete tiers
        for CCXT compatibility.

        :param market_info: Market info dictionary from get_market_infos()
        :param is_long: True for long positions, False for short positions
        :param num_tiers: Number of discrete tiers to generate (default 5)
        :return: List of leverage tier dictionaries in CCXT format with fields:

            - tier: Tier number (1-indexed)
            - minNotional: Minimum position size in USD for this tier
            - maxNotional: Maximum position size in USD for this tier
            - maxLeverage: Maximum allowed leverage for this tier
            - minCollateralFactor: Actual min collateral factor at this OI level (decimal)

        Example::

            >>> market_info = {
            ...     "minCollateralFactor": "5000000000000000000000000000",
            ...     "minCollateralFactorForOpenInterestLong": "10000000000000000",
            ...     "longOpenInterestUsd": "50000000000000000000000000000000",
            ...     "maxOpenInterestLong": "100000000000000000000000000000000"
            ... }
            >>> tiers = calculate_leverage_tiers(market_info, is_long=True)
            >>> # Returns tiers showing leverage decreasing as position size increases
        """
        PRECISION = Decimal(10**30)

        # Extract relevant fields
        base_min_collateral = Decimal(market_info.get("minCollateralFactor", "0"))
        multiplier_key = "minCollateralFactorForOpenInterestLong" if is_long else "minCollateralFactorForOpenInterestShort"
        oi_multiplier = Decimal(market_info.get(multiplier_key, "0"))

        max_oi_key = "maxOpenInterestLong" if is_long else "maxOpenInterestShort"
        max_open_interest = Decimal(market_info.get(max_oi_key, "0"))

        current_oi_key = "longOpenInterestUsd" if is_long else "shortOpenInterestUsd"
        current_oi = Decimal(market_info.get(current_oi_key, "0"))

        # Handle invalid data
        if base_min_collateral <= 0 or max_open_interest <= 0:
            return []

        # Calculate tiers based on open interest levels
        tiers = []
        tier_size = max_open_interest / num_tiers

        for i in range(num_tiers):
            # Calculate OI range for this tier
            tier_start_oi = tier_size * i
            tier_end_oi = tier_size * (i + 1) if i < num_tiers - 1 else max_open_interest

            # Calculate min collateral factor at the END of this tier (most restrictive)
            oi_based_collateral = (tier_end_oi * oi_multiplier) / PRECISION
            actual_min_collateral = max(base_min_collateral, oi_based_collateral)

            # Skip if collateral factor is zero
            if actual_min_collateral <= 0:
                continue

            # Calculate max leverage for this tier (with 2x buffer)
            max_leverage = PRECISION / actual_min_collateral / Decimal(2)

            # Convert to USD notional (from 30 decimals)
            min_notional = float(tier_start_oi / PRECISION)
            max_notional = float(tier_end_oi / PRECISION)

            maintenance_margin_rate = float(actual_min_collateral / PRECISION)

            tiers.append(
                {
                    "tier": i + 1,
                    "minNotional": min_notional,
                    "maxNotional": max_notional,
                    "maxLeverage": float(max_leverage),
                    "minCollateralFactor": maintenance_margin_rate,
                    "maintenanceMarginRate": maintenance_margin_rate,
                    "info": {
                        "tier": i + 1,
                        "minCollateralFactor": maintenance_margin_rate,
                        "maxOpenInterest": float(max_open_interest / PRECISION),
                        "openInterestRange": [min_notional, max_notional],
                    },
                }
            )

        if tiers:
            last_tier = tiers[-1]
            max_leverage_value = last_tier.get("maxLeverage")
            if max_leverage_value:
                min_required_notional = GMXSubsquidClient.MIN_DISPLAY_STAKE * max_leverage_value
                if last_tier["maxNotional"] < min_required_notional:
                    last_tier["maxNotional"] = min_required_notional
                    info = last_tier.get("info") or {}
                    info["openInterestRange"] = [
                        info.get("openInterestRange", [0.0, 0.0])[0],
                        min_required_notional,
                    ]
                    last_tier["info"] = info

        return tiers

    def get_token_decimals(self, token_address: str) -> int:
        """Get decimals for a token address from GMX API.

        :param token_address: Token contract address
        :return: Number of decimals for the token
        """
        # Validate address before normalizing
        if not is_address(token_address):
            # Invalid address format, default to 18 decimals
            return 18

        try:
            # Normalize address to checksum format
            checksum_address = to_checksum_address(token_address)
        except (ValueError, TypeError):
            # Invalid address format, default to 18 decimals
            return 18

        # Get token metadata from GMX API
        tokens_metadata = self._get_tokens_metadata()

        # Look up token in metadata
        if checksum_address in tokens_metadata:
            return tokens_metadata[checksum_address]["decimals"]

        # Default to 18 for unknown tokens
        return 18

    def _get_markets_cache(self) -> dict[str, dict]:
        """Get markets data with caching.

        :return: Dictionary mapping market addresses to market info
        """
        if self._markets_cache is None:
            markets_list = self.get_markets(limit=200)
            self._markets_cache = {}
            for market in markets_list:
                market_addr = to_checksum_address(market["id"])
                self._markets_cache[market_addr] = market
        return self._markets_cache

    def get_index_token_decimals(self, market_address: str) -> int:
        """Get decimals for the index token of a market.

        :param market_address: Market contract address
        :return: Number of decimals for the index token
        """
        # Validate address before normalizing
        if not is_address(market_address):
            # Invalid address format, default to 18 decimals
            return 18

        try:
            # Normalize market address
            market_addr = to_checksum_address(market_address)
        except (ValueError, TypeError):
            # Invalid address format, default to 18 decimals
            return 18

        # Get market info from cache
        markets = self._get_markets_cache()

        if market_addr in markets:
            index_token = markets[market_addr].get("indexToken")
            if index_token:
                return self.get_token_decimals(index_token)

        # Default to 18 if market or index token not found
        return 18

    def format_position(self, position: dict[str, Any]) -> dict[str, Any]:
        """Format a raw position into human-readable values.

        :param position: Raw position dictionary from GraphQL
        :return: Formatted position with human-readable values

        .. note::
            Decimals vary by field:

            - collateralAmount: Depends on collateral token (6 for USDC, 18 for ETH, etc.)
            - sizeInUsd: 30 decimals
            - sizeInTokens: Depends on index token (8 for BTC, 18 for ETH, etc.)
            - entryPrice: 30 - index_token_decimals (e.g., 22 for BTC with 8 decimals)
            - PnL and fees: 30 decimals
            - leverage: 4 decimals (10000 = 1x leverage)
        """
        collateral_decimals = self.get_token_decimals(position["collateralToken"])
        index_decimals = self.get_index_token_decimals(position["market"])

        # Entry price uses: 30 - index_decimals
        # This accounts for the index token's decimal places
        price_decimals = 30 - index_decimals

        return {
            "id": position["id"],
            "position_key": position["positionKey"],
            "account": position["account"],
            "market": position["market"],
            "collateral_token": position["collateralToken"],
            "is_long": position["isLong"],
            "collateral_amount": float(self.from_fixed_point(position["collateralAmount"], decimals=collateral_decimals)),
            "size_usd": float(self.from_fixed_point(position["sizeInUsd"])),
            "size_tokens": float(self.from_fixed_point(position["sizeInTokens"], decimals=index_decimals)),
            "entry_price": float(self.from_fixed_point(position["entryPrice"], decimals=price_decimals)),
            "realized_pnl": float(self.from_fixed_point(position["realizedPnl"])),
            "unrealized_pnl": float(self.from_fixed_point(position["unrealizedPnl"])),
            "realized_fees": float(self.from_fixed_point(position["realizedFees"])),
            "unrealized_fees": float(self.from_fixed_point(position["unrealizedFees"])),
            "leverage": float(self.from_fixed_point(position["leverage"], decimals=4)),
            "opened_at": position["openedAt"],
        }

    def get_trade_action_by_order_key(
        self,
        order_key: str,
        timeout_seconds: int = 30,
        poll_interval: float = 0.5,
        max_retries: int = 3,
        account: str | None = None,
    ) -> Optional[dict[str, Any]]:
        """Query for order execution status via Subsquid.

        Polls Subsquid until the order status appears or timeout.
        Much faster than on-chain polling (typically < 1 second).

        This uses a three-query approach for better reliability:
        1. First tries tradeActions (has real fee fields, requires account filter)
        2. Falls back to positionChanges (fast, for executed position changes)
        3. Falls back to orderById (fast and reliable for all order statuses)

        The tradeActions query uses an additional ``account_eq`` filter to avoid
        504 Gateway Timeout errors that occurred when filtering by orderKey alone.

        :param order_key: Order key (hex string with 0x prefix)
        :param timeout_seconds: Max time to wait for indexer (default 30s)
        :param poll_interval: Time between queries in seconds (default 0.5s)
        :param max_retries: Number of retries for failed requests (default 3)
        :param account: Wallet address for tradeActions query filter.
            When provided, enables the tradeActions query which returns real
            fee breakdown (positionFee, borrowingFee, fundingFee).
        :return: Trade action dict or None if not found within timeout

        Example::

            client = GMXSubsquidClient(chain="arbitrum")
            action = client.get_trade_action_by_order_key(
                "0x1234...abcd",
                timeout_seconds=30,
                account="0xabcd...1234",
            )

            if action:
                if action["eventName"] == "OrderExecuted":
                    print(f"Executed at {action['executionPrice']}")
                elif action["eventName"] == "OrderCancelled":
                    print(f"Cancelled: {action['reason']}")
        """
        # Query 1: positionChanges (faster, has tx hash in id field)
        query_position_changes = """
        query GetPositionChange($orderKey: String!) {
          positionChanges(
            where: { orderKey_eq: $orderKey }
            limit: 1
          ) {
            id
            type
            orderKey
            isLong
            sizeDeltaUsd
            executionPrice
            priceImpactUsd
            proportionalPendingImpactUsd
            basePnlUsd
            feesAmount
            block
            timestamp
          }
        }
        """

        # Query 2: tradeActions (has real fee fields, but requires account filter
        # to avoid 504 timeouts that occurred with orderKey-only filtering)
        query_trade_actions = """
        query GetTradeAction($orderKey: String!, $account: String!) {
          tradeActions(
            where: { orderKey_eq: $orderKey, account_eq: $account, eventName_eq: "OrderExecuted" }
            limit: 1
          ) {
            id
            eventName
            orderKey
            orderType
            isLong
            sizeDeltaUsd
            executionPrice
            priceImpactUsd
            basePnlUsd
            positionFeeAmount
            borrowingFeeAmount
            fundingFeeAmount
            initialCollateralTokenAddress
            collateralTokenPriceMax
            proportionalPendingImpactUsd
            timestamp
            transaction { hash }
          }
        }
        """

        # Query 3: orderById (fast and reliable for all order statuses)
        query_order_by_id = """
        query GetOrderById($id: String!) {
          orderById(id: $id) {
            id
            status
            orderType
            isLong
            sizeDeltaUsd
            acceptablePrice
            triggerPrice
            cancelledReason
            cancelledReasonBytes
            frozenReason
            frozenReasonBytes
            createdTxn { hash timestamp }
            cancelledTxn { hash timestamp }
            executedTxn { hash timestamp }
          }
        }
        """

        start_time = time.time()
        consecutive_failures = 0

        while time.time() - start_time < timeout_seconds:
            try:
                # Try tradeActions first if account is provided (has real fee fields)
                if account:
                    try:
                        logger.info(
                            "Trying tradeActions query with account=%s for order %s",
                            account,
                            order_key,
                        )
                        ta_data = self._query(
                            query_trade_actions,
                            variables={"orderKey": order_key, "account": account},
                            timeout=30,
                        )
                        actions = ta_data.get("tradeActions", [])
                        if actions:
                            action = actions[0]
                            result = {
                                "eventName": action.get("eventName", "OrderExecuted"),
                                "orderKey": action["orderKey"],
                                "orderType": action.get("orderType"),
                                "isLong": action.get("isLong"),
                                "executionPrice": str(action.get("executionPrice") or 0),
                                "sizeDeltaUsd": str(action.get("sizeDeltaUsd") or 0),
                                "priceImpactUsd": str(action.get("priceImpactUsd") or 0),
                                "pendingPriceImpactUsd": str(action.get("proportionalPendingImpactUsd") or 0),
                                "pnlUsd": str(action.get("basePnlUsd") or 0) if action.get("basePnlUsd") else None,
                                "positionFeeAmount": str(action.get("positionFeeAmount") or 0),
                                "borrowingFeeAmount": str(action.get("borrowingFeeAmount") or 0),
                                "fundingFeeAmount": str(action.get("fundingFeeAmount") or 0),
                                "collateralToken": action.get("initialCollateralTokenAddress"),
                                "collateralTokenPriceMax": str(action.get("collateralTokenPriceMax") or 0) if action.get("collateralTokenPriceMax") else None,
                                "timestamp": action.get("timestamp"),
                                "transaction": action.get("transaction", {}),
                            }
                            logger.info(
                                "Subsquid tradeActions returned fee data: eventName=%s | positionFee=%s, borrowingFee=%s, fundingFee=%s | collateral=%s (price=%s) | orderKey=%s",
                                result["eventName"],
                                result["positionFeeAmount"],
                                result["borrowingFeeAmount"],
                                result["fundingFeeAmount"],
                                result["collateralToken"],
                                result["collateralTokenPriceMax"],
                                order_key[:16] + "..." if len(order_key) > 16 else order_key,
                            )
                            return result
                    except Exception as e:
                        logger.warning(
                            "tradeActions query failed (will fall back to positionChanges): %s",
                            e,
                        )

                # Try positionChanges (faster, has tx hash in id field)
                logger.debug("=" * 70)
                logger.debug("Querying positionChanges")
                logger.debug("=" * 70)
                logger.debug("Query: %s", query_position_changes)
                logger.debug("Variables: orderKey=%s", order_key)
                logger.debug("=" * 70)

                data = self._query(query_position_changes, variables={"orderKey": order_key}, timeout=60)
                changes = data.get("positionChanges", [])

                logger.debug("positionChanges response: %s", json.dumps({"positionChanges": changes}, indent=2, default=str))
                logger.debug("=" * 70)

                if changes:
                    change = changes[0]
                    # Convert positionChange to tradeAction format
                    # The id field IS the transaction hash!
                    result = {
                        "eventName": "OrderExecuted" if change["type"] == "increase" or change["type"] == "decrease" else "OrderCancelled",
                        "orderKey": change["orderKey"],
                        "orderType": 2 if change["type"] == "increase" else 3,  # MarketIncrease/MarketDecrease
                        "isLong": change.get("isLong"),
                        "executionPrice": str(change.get("executionPrice") or 0),
                        "sizeDeltaUsd": str(change.get("sizeDeltaUsd") or 0),
                        "priceImpactUsd": str(change.get("priceImpactUsd") or 0),
                        "pendingPriceImpactUsd": str(change.get("proportionalPendingImpactUsd") or 0),
                        "pnlUsd": str(change.get("basePnlUsd") or 0) if change.get("basePnlUsd") else None,
                        "positionFeeAmount": str(change.get("feesAmount") or 0),
                        "borrowingFeeAmount": "0",
                        "fundingFeeAmount": "0",
                        "timestamp": change.get("timestamp"),
                        "transaction": {
                            "hash": change["id"],  # id IS the tx hash!
                            "blockNumber": change.get("block"),
                            "timestamp": change.get("timestamp"),
                        },
                    }
                    logger.debug("Returning result from positionChanges: %s", json.dumps(result, indent=2, default=str))
                    logger.debug("=" * 70)
                    return result

                # Reset failure counter on successful query
                consecutive_failures = 0

                # If positionChanges didn't return data, try orderById
                logger.debug("=" * 70)
                logger.debug("positionChanges returned no data, trying orderById")
                logger.debug("=" * 70)
                logger.debug("Query: %s", query_order_by_id)
                logger.debug("Variables: id=%s", order_key)
                logger.debug("=" * 70)

                try:
                    data = self._query(query_order_by_id, variables={"id": order_key}, timeout=30)
                    order = data.get("orderById")

                    logger.debug("orderById response: %s", json.dumps({"orderById": order}, indent=2, default=str))
                    logger.debug("=" * 70)

                    if order:
                        # Convert order status to eventName format
                        status = order.get("status", "").lower()
                        if status == "executed":
                            event_name = "OrderExecuted"
                            txn = order.get("executedTxn") or {}
                        elif status == "cancelled":
                            event_name = "OrderCancelled"
                            txn = order.get("cancelledTxn") or {}
                        elif status == "frozen":
                            event_name = "OrderFrozen"
                            txn = order.get("createdTxn") or {}
                        elif status == "created":
                            # Order still pending - don't return yet
                            logger.debug("Order is still in Created status, continuing to poll")
                            time.sleep(poll_interval)
                            continue
                        else:
                            event_name = f"Order{status.title()}"
                            txn = order.get("createdTxn") or {}

                        result = {
                            "eventName": event_name,
                            "orderKey": order.get("id"),
                            "orderType": order.get("orderType"),
                            "isLong": order.get("isLong"),
                            "sizeDeltaUsd": str(order.get("sizeDeltaUsd") or 0),
                            "acceptablePrice": str(order.get("acceptablePrice") or 0),
                            "triggerPrice": str(order.get("triggerPrice") or 0),
                            "reason": order.get("cancelledReason") or order.get("frozenReason") or "",
                            "reasonBytes": order.get("cancelledReasonBytes") or order.get("frozenReasonBytes") or "",
                            "timestamp": txn.get("timestamp"),
                            "transaction": {
                                "hash": txn.get("hash"),
                                "timestamp": txn.get("timestamp"),
                            },
                        }
                        logger.debug("Returning result from orderById: %s", json.dumps(result, indent=2, default=str))
                        logger.debug("=" * 70)
                        return result
                except Exception as e:
                    logger.debug("orderById query failed: %s", e)
                    logger.debug("=" * 70)
                    pass  # Continue polling

            except Exception as e:
                consecutive_failures += 1
                logger.debug(
                    "Subsquid query attempt failed (%d/%d): %s",
                    consecutive_failures,
                    max_retries,
                    e,
                )

                # If we've hit max retries, give up
                if consecutive_failures >= max_retries:
                    logger.warning(
                        "Subsquid query failed after %d retries: %s",
                        max_retries,
                        e,
                    )
                    return None

                # Exponential backoff: wait 2^failures seconds before retry
                backoff_time = min(2**consecutive_failures, 10)  # Cap at 10 seconds
                logger.debug(
                    "Retrying Subsquid query in %.1fs...",
                    backoff_time,
                )
                time.sleep(backoff_time)
                continue

            time.sleep(poll_interval)

        return None
