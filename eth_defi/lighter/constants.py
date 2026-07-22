"""Constants for the Lighter integration.

Shared constants used across the Lighter modules
(:py:mod:`~eth_defi.lighter.daily_metrics`,
:py:mod:`~eth_defi.lighter.vault_data_export`, etc.).
"""

import datetime
from dataclasses import dataclass
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
#:     is **not** an EVM chain. The original deployment's onchain Lighter
#:     deposit/withdraw contract lives on Ethereum mainnet (chain id 1); see
#:     :py:data:`LIGHTER_L1_CONTRACT` below. The Robinhood metrics deployment
#:     is associated with Robinhood Chain (chain id 4663).
LIGHTER_CHAIN_ID: int = 9998

#: Legacy synthetic chain ID used by the first implementation of Lighter on
#: Robinhood support.
#:
#: Both deployments now intentionally use :py:data:`LIGHTER_CHAIN_ID` because
#: they belong to one native Lighter dataset namespace. Keep this old value
#: only so Parquet and VaultDatabase migrations can remove rows written by the
#: short-lived split-chain implementation.
LIGHTER_LEGACY_ROBINHOOD_CHAIN_ID: int = 9996

#: Ethereum mainnet chain ID associated with the original Lighter deployment.
#:
#: This is deliberately separate from :py:data:`LIGHTER_CHAIN_ID`. The latter
#: is the synthetic vault-dataset ID used to partition native Lighter prices,
#: whereas this value identifies the EVM chain associated with the deployment.
#: This distinction became necessary when adding Lighter on Robinhood Chain.
LIGHTER_ETHEREUM_DEPLOYMENT_CHAIN_ID: int = 1

#: Robinhood Chain ID associated with the Robinhood Lighter deployment.
#:
#: For now this field exists specifically so lifetime-metrics consumers can
#: distinguish Lighter on Robinhood Chain from Lighter on Ethereum without
#: replacing the synthetic IDs used by the vault price pipeline.
LIGHTER_ROBINHOOD_DEPLOYMENT_CHAIN_ID: int = 4663

#: LLP account index exposed by the Robinhood Lighter public-pools API.
#:
#: For now this override exists specifically for Lighter on Robinhood. Its
#: ``systemConfig.liquidity_pool_index`` currently points at the next,
#: uninitialised account instead of the live USDG LLP account. Account type 3
#: cannot be used as a generic fallback because Ethereum currently exposes both
#: LLP and XLP with that account type. Keeping the workaround in deployment
#: configuration prevents XLP from being misclassified as the canonical LLP.
LIGHTER_ROBINHOOD_LLP_ACCOUNT_INDEX: int = 281474976710654

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
#:
#: Stored checksummed so it can be passed directly to web3.py calls.
LIGHTER_L1_CONTRACT: HexAddress = "0x3B4D794a66304F130a4Db8F2551B0070dfCf5ca7"

#: USDC on Ethereum mainnet — the Lighter deposit asset.
LIGHTER_USDC_ETHEREUM: HexAddress = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

#: Lighter mainnet API base URL.
LIGHTER_API_URL: str = "https://mainnet.zklighter.elliot.ai"

#: Lighter API for the deployment settling on Robinhood Chain.
LIGHTER_ROBINHOOD_API_URL: str = "https://api.rh.lighter.xyz"

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
#: The original Ethereum Lighter deployment uses USDC as its collateral
#: currency. Robinhood deployment configuration overrides this with USDG.
LIGHTER_DENOMINATION: str = "USDC"

#: Pool cooldown period for withdrawals.
#:
#: Ethereum value from ``systemConfig.liquidity_pool_cooldown_period``
#: (300000ms = 5 minutes). Robinhood deployment configuration overrides it.
LIGHTER_POOL_LOCKUP: datetime.timedelta = datetime.timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class LighterAPIConfig:
    """Configuration for one independently indexed Lighter deployment.

    Lighter account indexes are only unique within a deployment. In
    particular, account ``281474976710654`` exists on both Ethereum Lighter
    and Robinhood Lighter, so downstream storage must always pair an account
    index with :py:attr:`slug`.

    :param slug:
        Stable storage identifier for the deployment.
    :param name:
        Human-readable scanner and scheduling name.
    :param chain_id:
        Synthetic vault-dataset chain ID. This remains the primary identity
        used for price partitions and :class:`~eth_defi.vault.base.VaultSpec`.
    :param deployment_chain_id:
        Real EVM chain associated with this Lighter deployment. For now this
        separate identity is needed to expose whether a Lighter pool belongs
        to the Ethereum or Robinhood deployment in lifetime-metrics exports.
    :param api_url:
        REST API base URL.
    :param app_url:
        Web application base URL used for vault links.
    :param address_prefix:
        Synthetic vault address prefix. Deployment-specific prefixes prevent
        address-only metadata rules from leaking between deployments.
    :param denomination:
        Pool collateral symbol.
    :param lockup:
        Public-pool withdrawal cooldown reported by ``systemConfig``.
    :param llp_account_index_override:
        Deployment-specific canonical LLP account override. ``None`` trusts
        ``systemConfig``. For now Robinhood needs an override because its live
        system configuration points at an uninitialised account.
    """

    slug: str
    name: str
    chain_id: int

    #: Real EVM chain associated with this deployment.
    #:
    #: Do not use this value as the Lighter vault's primary ``chain_id``. The
    #: synthetic :py:attr:`chain_id` is still required to keep native Lighter
    #: price rows separate from ordinary ERC-4626 vaults. Both Lighter
    #: deployments share that synthetic ID and use deployment-specific address
    #: prefixes for uniqueness. For now this second chain ID supports Lighter
    #: on Robinhood in downstream lifetime-metrics exports.
    deployment_chain_id: int

    api_url: str
    app_url: str
    address_prefix: str
    denomination: str
    lockup: datetime.timedelta

    #: Canonical LLP account override for deployments with unreliable system
    #: configuration. For now only Lighter on Robinhood uses this field.
    llp_account_index_override: int | None = None

    def format_pool_address(self, account_index: int) -> str:
        """Create a globally unique synthetic pool address.

        :param account_index:
            Deployment-local Lighter account index.
        :return:
            Synthetic address accepted by :class:`eth_defi.vault.base.VaultSpec`.
        """
        return f"{self.address_prefix}-{account_index}"

    def format_pool_link(self, account_index: int) -> str:
        """Create the deployment-specific public-pool application link.

        :param account_index:
            Deployment-local Lighter account index.
        :return:
            Public-pool detail URL.
        """
        return f"{self.app_url}/public-pools/{account_index}"

    def matches_pool_address(self, address: str) -> bool:
        """Check whether a synthetic pool address belongs to this deployment.

        A numeric suffix is required because the backwards-compatible
        Ethereum prefix ``lighter-pool`` is also the beginning of the
        Robinhood prefix ``lighter-pool-robinhood``. This exact check lets
        partial price merges distinguish the two deployments without relying
        on prefix ordering.

        :param address:
            Synthetic Lighter vault address.
        :return:
            ``True`` when the address has this deployment's prefix and a
            numeric Lighter account index.
        """
        prefix = f"{self.address_prefix}-"
        address = str(address)
        return address.startswith(prefix) and address.removeprefix(prefix).isdigit()

    @property
    def pool_address_pattern(self) -> str:
        """Return the PyArrow regex for this deployment's pool addresses.

        :return:
            Anchored regular expression matching synthetic pool addresses.
        """
        return f"^{self.address_prefix}-[0-9]+$"


#: Ethereum-settled Lighter deployment. The legacy address format and
#: synthetic chain ID are intentionally retained for dataset compatibility.
LIGHTER_ETHEREUM: LighterAPIConfig = LighterAPIConfig(
    slug="ethereum",
    name="Lighter Ethereum",
    chain_id=LIGHTER_CHAIN_ID,
    # Associated EVM deployment chain exported alongside synthetic ID 9998.
    deployment_chain_id=LIGHTER_ETHEREUM_DEPLOYMENT_CHAIN_ID,
    api_url=LIGHTER_API_URL,
    app_url="https://app.lighter.xyz",
    address_prefix="lighter-pool",
    denomination=LIGHTER_DENOMINATION,
    lockup=LIGHTER_POOL_LOCKUP,
)

#: Robinhood Chain-settled Lighter deployment. Its live API reports USDG as
#: collateral and a zero public-pool cooldown.
LIGHTER_ROBINHOOD: LighterAPIConfig = LighterAPIConfig(
    slug="robinhood",
    name="Lighter Robinhood",
    # Both Lighter deployments belong to the same synthetic dataset chain.
    # The Robinhood address prefix below prevents VaultSpec/address collisions.
    chain_id=LIGHTER_CHAIN_ID,
    # Associated EVM deployment chain exported specifically so consumers can
    # identify this pool as Lighter on Robinhood rather than Ethereum.
    deployment_chain_id=LIGHTER_ROBINHOOD_DEPLOYMENT_CHAIN_ID,
    api_url=LIGHTER_ROBINHOOD_API_URL,
    app_url="https://robinhoodchain.lighter.xyz",
    address_prefix="lighter-pool-robinhood",
    denomination="USDG",
    lockup=datetime.timedelta(0),
    # For now Robinhood's systemConfig points at account 281474976710655,
    # which is uninitialised, while publicPoolsMetadata exposes the live USDG
    # LLP at 281474976710654. Do not replace this with ``account_type == 3``:
    # Ethereum also exposes XLP as account type 3 and it is not the LLP.
    llp_account_index_override=LIGHTER_ROBINHOOD_LLP_ACCOUNT_INDEX,
)

#: Production Lighter deployments scanned by the all-chain vault pipeline.
LIGHTER_DEPLOYMENTS: tuple[LighterAPIConfig, ...] = (
    LIGHTER_ETHEREUM,
    LIGHTER_ROBINHOOD,
)

#: Deployment lookup for DuckDB export and migration code.
LIGHTER_DEPLOYMENTS_BY_SLUG: dict[str, LighterAPIConfig] = {deployment.slug: deployment for deployment in LIGHTER_DEPLOYMENTS}


def identify_lighter_pool_deployment(address: str) -> LighterAPIConfig | None:
    """Resolve a synthetic Lighter pool address to its deployment.

    This helper is used by partial Parquet replacement so an independently
    completed Ethereum or Robinhood scan cannot remove the other deployment's
    retained history. :py:meth:`LighterAPIConfig.matches_pool_address`
    validates the numeric account-index suffix, preventing the shorter
    Ethereum prefix from matching Robinhood addresses.

    :param address:
        Synthetic Lighter vault address.
    :return:
        Matching deployment, or ``None`` when the address is not recognised.
    """
    return next((deployment for deployment in LIGHTER_DEPLOYMENTS if deployment.matches_pool_address(address)), None)


#: Set of Lighter system pool addresses (protocol-curated).
#:
#: The LLP (Lighter Liquidity Pool) is the protocol's own liquidity pool;
#: the XLP (Experimental Liquidity Provider) is the protocol-run pool for
#: experimental markets.  Both are community-owned and protocol-operated.
#: Uses each deployment's synthetic pool-address format.
#:
#: These are protocol-operated pools with special properties (no operator fee).
#: Ethereum system pools are fetched separately via ``systemConfig`` when they
#: are absent from ``publicPoolsMetadata``; the Robinhood LLP is currently
#: present in that listing. Useful for filtering protocol pools from
#: user-created pools.
LIGHTER_SYSTEM_POOL_ADDRESSES: set[str] = {
    "lighter-pool-281474976710654",  # LLP (Lighter Liquidity Pool)
    "lighter-pool-281474976680784",  # XLP (Experimental Liquidity Provider)
    "lighter-pool-robinhood-281474976710654",  # Robinhood LLP / insurance pool
}
