"""Async GraphQL client for GMX Subsquid data."""

import asyncio
import logging
from typing import Any, Optional

import aiohttp

from eth_defi.gmx.contracts import GMX_SUBSQUID_ENDPOINTS, GMX_SUBSQUID_ENDPOINTS_BACKUP
from eth_defi.gmx.graphql.client import GMXSubsquidClient

logger = logging.getLogger(__name__)


class AsyncGMXSubsquidClient:
    """Async GraphQL client for GMX Subsquid indexed data.

    Async version of GMXSubsquidClient maintaining same query structure.
    """

    def __init__(
        self,
        chain: str,
        custom_endpoint: str | None = None,
    ):
        """Initialize async Subsquid client.

        :param chain: Chain name (e.g., "arbitrum", "avalanche")
        :param custom_endpoint: Optional custom Subsquid endpoint URL
        """
        self.chain = chain.lower()
        self.custom_endpoint = custom_endpoint
        self.session: aiohttp.ClientSession | None = None

        # Get endpoint URLs (primary and backup)
        if custom_endpoint:
            self.endpoint = custom_endpoint
            self.endpoint_backup = None
        elif self.chain in GMX_SUBSQUID_ENDPOINTS:
            self.endpoint = GMX_SUBSQUID_ENDPOINTS[self.chain]
            self.endpoint_backup = GMX_SUBSQUID_ENDPOINTS_BACKUP.get(self.chain)
        else:
            raise ValueError(f"No Subsquid URL configured for chain: {chain}")

    async def __aenter__(self):
        """Async context manager entry."""
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args):
        """Async context manager exit."""
        await self.close()

    async def close(self):
        """Close the HTTP session."""
        if self.session:
            await self.session.close()
            self.session = None

    async def _query(self, query: str, variables: dict | None = None) -> dict:
        """Execute GraphQL query with automatic failover to backup endpoint.

        :param query: GraphQL query string
        :param variables: Optional query variables
        :return: GraphQL response data
        """
        if not self.session:
            raise RuntimeError("Session not initialized. Use 'async with' context manager.")

        endpoints_to_try = [self.endpoint]
        if self.endpoint_backup:
            endpoints_to_try.append(self.endpoint_backup)

        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        last_error = None
        for endpoint in endpoints_to_try:
            try:
                async with self.session.post(
                    endpoint,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    response.raise_for_status()
                    result = await response.json()

                    if "errors" in result:
                        raise RuntimeError(f"GraphQL errors: {result['errors']}")

                    # Log if we used backup endpoint
                    if endpoint != self.endpoint:
                        logger.info("Successfully used backup Subsquid endpoint")

                    return result.get("data", {})

            except (aiohttp.ClientError, TimeoutError) as e:
                last_error = e
                logger.warning(
                    "Async Subsquid query failed on %s: %s",
                    endpoint.split("/")[2],  # Extract domain
                    e,
                )
                continue

        # All endpoints failed
        raise last_error

    async def get_market_infos(
        self,
        market_address: str | None = None,
        limit: int = 200,
        order_by: str = "id_DESC",
    ) -> list[dict[str, Any]]:
        """Fetch market information from Subsquid.

        :param market_address: Optional filter by specific market address
        :param limit: Maximum number of markets to fetch
        :param order_by: Sort order (e.g., "id_DESC")
        :return: List of market info dictionaries
        """
        where_clause = ""
        if market_address:
            where_clause = f'where: {{ marketTokenAddress_eq: "{market_address}" }}'

        # Debug logging
        logger.debug("Querying marketInfos with market_address=%s, limit=%s", market_address, limit)
        logger.debug("Where clause: %s", where_clause)

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

        data = await self._query(query)
        return data.get("marketInfos", [])

    async def get_trade_action_by_order_key(
        self,
        order_key: str,
        timeout_seconds: int = 30,
        poll_interval: float = 0.5,
        max_retries: int = 3,
        account: str | None = None,
    ) -> Optional[dict[str, Any]]:
        """Query for order execution status via Subsquid (async).

        Delegates to the sync :py:class:`GMXSubsquidClient` via
        ``run_in_executor`` to reuse the full tradeActions + positionChanges
        + orderById query logic.

        :param order_key: Order key (hex string with 0x prefix)
        :param timeout_seconds: Max time to wait for indexer (default 30s)
        :param poll_interval: Time between queries in seconds (default 0.5s)
        :param max_retries: Number of retries for failed requests (default 3)
        :param account: Wallet address for tradeActions query filter.
        :return: Trade action dict or None if not found within timeout
        """
        sync_client = GMXSubsquidClient(
            chain=self.chain,
            custom_endpoint=self.custom_endpoint,
        )
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: sync_client.get_trade_action_by_order_key(
                order_key,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
                max_retries=max_retries,
                account=account,
            ),
        )

    @staticmethod
    def calculate_max_leverage(min_collateral_factor: str) -> float | None:
        """Calculate max leverage from min collateral factor.

        Reuses sync implementation for consistency.
        """
        return GMXSubsquidClient.calculate_max_leverage(min_collateral_factor)
