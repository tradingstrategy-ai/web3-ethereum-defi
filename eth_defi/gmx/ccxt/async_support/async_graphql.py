"""Async GraphQL client for GMX Subsquid data."""

import logging
from typing import Any

import aiohttp

from eth_defi.gmx.contracts import GMX_SUBSQUID_ENDPOINTS
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

        # Get endpoint URL
        if custom_endpoint:
            self.endpoint = custom_endpoint
        elif self.chain in GMX_SUBSQUID_ENDPOINTS:
            self.endpoint = GMX_SUBSQUID_ENDPOINTS[self.chain]
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
        """Execute GraphQL query.

        :param query: GraphQL query string
        :param variables: Optional query variables
        :return: GraphQL response data
        """
        if not self.session:
            raise RuntimeError("Session not initialized. Use 'async with' context manager.")

        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        async with self.session.post(
            self.endpoint,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            response.raise_for_status()
            result = await response.json()

            if "errors" in result:
                raise RuntimeError(f"GraphQL errors: {result['errors']}")

            return result.get("data", {})

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

    @staticmethod
    def calculate_max_leverage(min_collateral_factor: str) -> float | None:
        """Calculate max leverage from min collateral factor.

        Reuses sync implementation for consistency.
        """
        return GMXSubsquidClient.calculate_max_leverage(min_collateral_factor)
