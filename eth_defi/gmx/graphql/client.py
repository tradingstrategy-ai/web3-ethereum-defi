"""GMX Subsquid GraphQL client for fetching positions and historical data.

This provides an alternative to direct contract reads using the Subsquid indexer:
- Faster queries (no blockchain RPC calls)
- Historical data and analytics
- PnL tracking across time periods
- Position history and changes

The original contract-based implementation (GetOpenPositions) remains the source
of truth for on-chain data when executing trades.
"""

from typing import Optional, Any
from decimal import Decimal
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

    Example usage::

        # Create client
        client = GMXSubsquidClient()

        # Get open positions
        positions = client.get_positions(account="0x1234...", only_open=True)

        # Get PnL summary
        pnl_summary = client.get_pnl_summary(account="0x1234...")

        # Get position history
        history = client.get_position_changes(account="0x1234...", limit=50)
    """

    def __init__(self, chain: str = "arbitrum", custom_endpoint: Optional[str] = None):
        """Initialize the Subsquid client.

        :param chain: Chain name ("arbitrum" or "avalanche")
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
            - sizeInTokens: Position size in tokens (BigInt as string)
            - entryPrice: Entry price (30 decimals, BigInt as string)
            - realizedPnl: Realized PnL (30 decimals, BigInt as string)
            - unrealizedPnl: Unrealized PnL (30 decimals, BigInt as string)
            - realizedFees: Realized fees (30 decimals, BigInt as string)
            - unrealizedFees: Unrealized fees (30 decimals, BigInt as string)
            - leverage: Leverage (30 decimals, BigInt as string)
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

    def get_markets(self) -> list[dict[str, Any]]:
        """Get all available markets.

        :return: List of markets with fields:

            - id: Market address
            - indexToken: Index token address
            - longToken: Long token address
            - shortToken: Short token address
        """
        query = """
        query {
          markets {
            id
            indexToken
            longToken
            shortToken
          }
        }
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

        all_time_volume = float(self.parse_bigint(stats["volume"]))

        # Check all-time volume threshold
        if all_time_volume > ALL_TIME_VOLUME:
            return True

        # Get PnL summary to check recent trading
        pnl_summary = self.get_pnl_summary(account)

        # Check 14-day volume (week bucket includes last 7 days, we approximate with month data)
        for bucket in pnl_summary:
            if bucket["bucketLabel"] == "week":
                week_volume = float(self.parse_bigint(bucket["volume"]))
                # Approximate 14-day as 2x weekly volume
                if week_volume * 2 > ROLLING_14_DAY_VOLUME:
                    return True

            # Check for high daily volume in recent activity
            if bucket["bucketLabel"] in ["today", "yesterday"]:
                daily_volume = float(self.parse_bigint(bucket["volume"]))
                if daily_volume > MAX_DAILY_VOLUME:
                    return True

        return False

    @staticmethod
    def parse_bigint(value: str, decimals: int = 30) -> Decimal:
        """Parse a BigInt string value to Decimal with proper scaling.

        :param value: BigInt value as string
        :param decimals: Number of decimals (default 30 for USD values)
        :return: Decimal value scaled by decimals

        Example::

            >>> parse_bigint("8625000000000000000000000000000", 30)
            Decimal('8.625')
        """
        return Decimal(value) / Decimal(10**decimals)

    def get_token_decimals(self, token_address: str) -> int:
        """Get decimals for a token address from GMX API.

        :param token_address: Token contract address
        :return: Number of decimals for the token
        """
        from cchecksum import to_checksum_address

        # Get token metadata from GMX API
        tokens_metadata = self._get_tokens_metadata()

        # Normalize address to checksum format
        checksum_address = to_checksum_address(token_address)

        # Look up token in metadata
        if checksum_address in tokens_metadata:
            return tokens_metadata[checksum_address]["decimals"]

        # Default to 18 for unknown tokens
        return 18

    def format_position(self, position: dict[str, Any]) -> dict[str, Any]:
        """Format a raw position into human-readable values.

        :param position: Raw position dictionary from GraphQL
        :return: Formatted position with human-readable values

        .. note::
            Decimals vary by field:

            - collateralAmount: Depends on collateral token (6 for USDC, 18 for ETH, etc.)
            - sizeInUsd, sizeInTokens: 30 decimals
            - entryPrice: 18 decimals (price per token)
            - PnL and fees: 30 decimals
            - leverage: 4 decimals (10000 = 1x leverage)
        """
        collateral_decimals = self.get_token_decimals(position["collateralToken"])

        return {
            "id": position["id"],
            "position_key": position["positionKey"],
            "account": position["account"],
            "market": position["market"],
            "collateral_token": position["collateralToken"],
            "is_long": position["isLong"],
            "collateral_amount": float(self.parse_bigint(position["collateralAmount"], decimals=collateral_decimals)),
            "size_usd": float(self.parse_bigint(position["sizeInUsd"])),
            "size_tokens": float(self.parse_bigint(position["sizeInTokens"])),
            "entry_price": float(self.parse_bigint(position["entryPrice"], decimals=18)),
            "realized_pnl": float(self.parse_bigint(position["realizedPnl"])),
            "unrealized_pnl": float(self.parse_bigint(position["unrealizedPnl"])),
            "realized_fees": float(self.parse_bigint(position["realizedFees"])),
            "unrealized_fees": float(self.parse_bigint(position["unrealizedFees"])),
            "leverage": float(self.parse_bigint(position["leverage"], decimals=4)),
            "opened_at": position["openedAt"],
        }
