"""Constants for the Lighter integration.

Shared constants used across the Lighter modules
(:py:mod:`~eth_defi.lighter.daily_metrics`,
:py:mod:`~eth_defi.lighter.vault_data_export`, etc.).
"""

import datetime
from pathlib import Path

from eth_typing import HexAddress

from eth_defi.vault.fee import VaultFeeMode

#: Synthetic in-house chain ID for Lighter (ZK-rollup, non-standard EVM).
#:
#: Added to :py:data:`eth_defi.chain.CHAIN_NAMES` as ``9998: "Lighter"``.
#:
#: .. warning::
#:
#:     This is a synthetic id used by the off-chain pool-metrics pipeline — it
#:     is **not** an EVM chain. The on-chain Lighter deposit/withdraw contract
#:     lives on Ethereum mainnet (chain id 1); see
#:     :py:data:`LIGHTER_L1_CONTRACT` below.
LIGHTER_CHAIN_ID: int = 9998

#: Lighter L1 contract (``ZkLighter`` proxy), Ethereum mainnet (chain id 1).
#:
#: Holds all user deposits and the canonical zk-rollup state root. This is the
#: contract whitelisted by the GuardV0 / TradingStrategyModuleV0 Lighter
#: integration for deposits/withdrawals from an asset-managed Safe.
#:
#: NOTE: distinct from :py:data:`LIGHTER_CHAIN_ID` (9998), which is the synthetic
#: chain id used by the off-chain metrics pipeline.
#:
#: See https://etherscan.io/address/0x3b4d794a66304f130a4db8f2551b0070dfcf5ca7
LIGHTER_L1_CONTRACT: HexAddress = "0x3b4d794a66304f130a4db8f2551b0070dfcf5ca7"

#: USDC on Ethereum mainnet — the Lighter deposit asset.
LIGHTER_USDC_ETHEREUM: HexAddress = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

#: Lighter mainnet API base URL.
LIGHTER_API_URL: str = "https://mainnet.zklighter.elliot.ai"

#: Default path for Lighter daily metrics DuckDB database.
LIGHTER_DAILY_METRICS_DATABASE: Path = Path.home() / ".tradingstrategy" / "vaults" / "lighter-pools.duckdb"

#: Default rate limit for Lighter API requests per second.
#:
#: Conservative estimate based on observed API behaviour.
LIGHTER_DEFAULT_REQUESTS_PER_SECOND: float = 2.0

#: Fee mode for Lighter native pools.
#:
#: Pool operators can set an ``operator_fee`` (0-100%). The share prices
#: from the API already reflect the operator's fee deduction, so the
#: pipeline sees net-of-fees prices. This matches internalised skimming.
LIGHTER_POOL_FEE_MODE: VaultFeeMode = VaultFeeMode.internalised_skimming

#: Pool denomination currency.
#:
#: Lighter uses USDC as the exchange base currency.
LIGHTER_DENOMINATION: str = "USDC"

#: Pool cooldown period for withdrawals.
#:
#: From ``systemConfig.liquidity_pool_cooldown_period`` (300000ms = 5 minutes).
LIGHTER_POOL_LOCKUP: datetime.timedelta = datetime.timedelta(minutes=5)


#: Set of Lighter system pool addresses (protocol-curated).
#:
#: The LLP (Lighter Liquidity Pool) is the protocol's own liquidity pool;
#: the XLP (Experimental Liquidity Provider) is the protocol-run pool for
#: experimental markets.  Both are community-owned and protocol-operated.
#: Uses the synthetic address format ``lighter-pool-{account_index}``.
#:
#: These are protocol-operated pools with special properties (no operator fee,
#: not listed in ``publicPoolsMetadata``, fetched separately via ``systemConfig``).
#: Useful for filtering protocol pools from user-created pools.
LIGHTER_SYSTEM_POOL_ADDRESSES: set[str] = {
    "lighter-pool-281474976710654",  # LLP (Lighter Liquidity Pool)
    "lighter-pool-281474976680784",  # XLP (Experimental Liquidity Provider)
}
