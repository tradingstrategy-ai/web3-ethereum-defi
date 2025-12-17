"""GMX Subsquid GraphQL client for fetching positions and historical data.

This provides an alternative to direct contract reads using the Subsquid indexer:
- Faster queries (no blockchain RPC calls)
- Historical data and analytics
- PnL tracking across time periods
- Position history and changes

The original contract-based implementation (GetOpenPositions) remains the source
of truth for on-chain data when executing trades.
"""

from decimal import Decimal
from typing import Any, Optional

import requests

from eth_defi.gmx.contracts import GMX_SUBSQUID_ENDPOINTS, get_tokens_metadata_dict

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

    MIN_DISPLAY_STAKE = 20.0

    def __init__(self, chain: str = "arbitrum", custom_endpoint: Optional[str] = None):
        """Initialize the Subsquid client.

        :param chain: Chain name ("arbitrum", "avalanche", or "arbitrum_sepolia")
        :param custom_endpoint: Optional custom GraphQL endpoint URL
        """
        self.chain = chain.lower()

        if custom_endpoint:
            self.endpoint = custom_endpoint
        elif self.chain in GMX_SUBSQUID_ENDPOINTS:
            self.endpoint = GMX_SUBSQUID_ENDPOINTS[self.chain]
        else:
            raise ValueError(
                f"Unsupported chain: {chain}. Supported chains: {', '.join(GMX_SUBSQUID_ENDPOINTS.keys())}",
            )

        # Cache token metadata from GMX API (address -> {symbol, decimals, synthetic})
        self._tokens_metadata: Optional[dict[str, dict]] = None
        # Cache markets data (market_address -> {indexToken, longToken, shortToken})
        self._markets_cache: Optional[dict[str, dict]] = None

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
    ) -> dict[str, Any]:
        """Execute a GraphQL query.

        :param query: GraphQL query string
        :param variables: Optional query variables
        :return: Query response data
        :raises requests.HTTPError: If the request fails
        :raises ValueError: If GraphQL returns errors
        """
        response = requests.post(
            self.endpoint,
            json={"query": query, "variables": variables or {}},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        response.raise_for_status()

        data = response.json()

        if "errors" in data:
            errors = ", ".join(err["message"] for err in data["errors"])
            raise ValueError(f"GraphQL query failed: {errors}")

        return data.get("data", {})

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

        data = self._query(query, variables=variables)
        return data.get("positionChanges", [])

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

        # Get all-time stats
        stats = self.get_account_stats(account)
        if not stats:
            return False

        all_time_volume = float(self.from_fixed_point(stats["volume"]))

        # Check all-time volume threshold
        if all_time_volume > ALL_TIME_VOLUME:
            return True

        # Get PnL summary to check recent trading
        pnl_summary = self.get_pnl_summary(account)

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
        from eth_utils import is_address, to_checksum_address

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
                from eth_utils import to_checksum_address

                market_addr = to_checksum_address(market["id"])
                self._markets_cache[market_addr] = market
        return self._markets_cache

    def get_index_token_decimals(self, market_address: str) -> int:
        """Get decimals for the index token of a market.

        :param market_address: Market contract address
        :return: Number of decimals for the index token
        """
        from eth_utils import is_address, to_checksum_address

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
