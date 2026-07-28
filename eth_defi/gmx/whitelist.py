"""
GMX market whitelisting for Lagoon vaults.

This module provides utilities for fetching and whitelisting GMX markets
in Lagoon vault Guard contracts. It enables programmatic management of
which GMX perpetual markets are allowed for trading through a Lagoon vault.

Getting a list of GMX markets
-----------------------------

To fetch all available GMX markets on a chain::

    from web3 import Web3
    from eth_defi.gmx.whitelist import fetch_all_gmx_markets

    web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
    markets = fetch_all_gmx_markets(web3)

    for address, info in markets.items():
        print(f"{info.market_symbol}: {address}")

Using the CLI script::

    export JSON_RPC_ARBITRUM="https://..."
    python scripts/gmx/list-gmx-markets.py

    # For Python-pasteable output
    python scripts/gmx/list-gmx-markets.py --python

Whitelisting markets in Guard contract
--------------------------------------

Markets must be whitelisted individually by the vault owner (Safe).
After vault deployment, impersonate or execute through the Safe to whitelist::

    # Direct call (only works if caller is Guard owner)
    guard.functions.whitelistGMXMarket(
        "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336",  # ETH/USD market
        "ETH/USD perpetuals",
    ).transact({"from": safe_address})

Or use the helper function for batch whitelisting::

    from eth_defi.gmx.whitelist import whitelist_gmx_markets

    tx_hashes = whitelist_gmx_markets(
        guard=guard_contract,
        markets=[ETH_USD_MARKET, BTC_USD_MARKET],
        owner=safe_address,
    )

GMX deployment configuration
----------------------------

When deploying a new Lagoon vault with GMX support, use the :class:`GMXDeployment`
dataclass to configure all GMX-related whitelisting::

    from eth_defi.gmx.whitelist import GMXDeployment

    # create_arbitrum() dynamically fetches the latest GMX contract addresses
    gmx_deployment = GMXDeployment.create_arbitrum(
        markets=[
            "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336",  # ETH/USD
            "0x47c031236e19d024b42f8AE6780E44A573170703",  # BTC/USD
        ],
    )

    # Pass to deployment function
    deployment = deploy_automated_lagoon_vault(
        ...
        gmx_deployment=gmx_deployment,
    )

Security considerations
-----------------------

- **Never use anyAsset=True in production**: This bypasses all market checks
- **Whitelist specific markets only**: Restrict trading to known, liquid markets
- **Review markets before whitelisting**: Verify the market address on Arbiscan
- **Markets can be removed**: Use ``removeGMXMarket()`` to revoke access
- **Receiver must be whitelisted**: The Safe must be whitelisted as a receiver
  before GMX trading will work

See also
--------

- :mod:`eth_defi.gmx.core.markets` - Low-level market data fetching
- :func:`eth_defi.erc_4626.vault_protocol.lagoon.deployment.deploy_automated_lagoon_vault` - Vault deployment with GMX support
- :mod:`eth_defi.gmx.lagoon.wallet` - GMX trading through Lagoon wallet
"""

import logging
from dataclasses import dataclass, field
from typing import Iterator

from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.contract import Contract

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.markets import MarketInfo, Markets

logger = logging.getLogger(__name__)


#: GMX contract addresses on Arbitrum mainnet.
#:
#: These are the official GMX V2 contract addresses required for
#: whitelisting GMX trading in a Guard contract.
#:
#: .. warning::
#:
#:     Duplicated as literals, so this can drift when GMX rotates contracts —
#:     it previously held an ExchangeRouter older than any supported release.
#:     Prefer :func:`get_gmx_arbitrum_addresses` or
#:     :meth:`GMXDeployment.create_arbitrum`, which read
#:     :data:`eth_defi.gmx.contracts.PINNED_CONTRACTS` directly and therefore
#:     cannot drift.
#:
#: Kept in sync with the ``v2.2c`` entry of
#: :data:`eth_defi.gmx.contracts.PINNED_CONTRACTS`; the literals exist only to
#: avoid a circular import between this module and :mod:`eth_defi.gmx.contracts`.
#: ``test_whitelist_constants_track_the_pinned_release`` enforces the match.
GMX_ARBITRUM_ADDRESSES: dict[str, HexAddress] = {
    "exchange_router": "0x7dE39FF2e232A2203196788d37e234cF8F1b83f1",
    "synthetics_router": "0x7452c558d45f8afC8c83dAe62C3f8A5BE19c71f6",
    "order_vault": "0x31eF83a530Fde1B38EE9A18093A333D8Bbbc40D5",
}


def get_gmx_arbitrum_addresses() -> dict[str, HexAddress]:
    """Resolve the GMX contract addresses for Arbitrum mainnet.

    Unlike :data:`GMX_ARBITRUM_ADDRESSES`, which duplicates the addresses as
    literals and can drift, this reads
    :data:`eth_defi.gmx.contracts.PINNED_CONTRACTS` through
    :func:`eth_defi.gmx.contracts.get_contract_addresses`, honouring the
    ``GMX_CONTRACT_RELEASE`` pin.

    :return:
        Dictionary with keys ``exchange_router``, ``synthetics_router``, ``order_vault``.

    :raises ValueError:
        If the configured release or chain is not supported.
    """
    from eth_defi.gmx.contracts import get_contract_addresses

    addresses = get_contract_addresses("arbitrum")
    return {
        "exchange_router": addresses.exchangerouter,
        "synthetics_router": addresses.syntheticsrouter,
        "order_vault": addresses.ordervault,
    }


#: Popular GMX markets on Arbitrum with human-readable names
#:
#: Use these addresses when whitelisting specific markets.
#: For a full list, use :func:`fetch_all_gmx_markets`.
GMX_POPULAR_MARKETS: dict[str, HexAddress] = {
    "ETH/USD": "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336",
    "BTC/USD": "0x47c031236e19d024b42f8AE6780E44A573170703",
    "SOL/USD": "0x09400D9DB990D5ed3f35D7be61DfAEB900Af03C9",
    "LINK/USD": "0x7f1fa204bb700853D36994DA19F830b6Ad18455C",
    "ARB/USD": "0xC25cEf6061Cf5dE5eb761b50E4743c1F5D7E5407",
    "DOGE/USD": "0x6853EA96FF216fAb11D2d930CE3C508556A4bdc4",
    "AVAX/USD": "0xB7e69749E3d2EDd90ea59A4932EFEa2D41E245d7",
    "NEAR/USD": "0x63Dc80EE90F26363B3FCD609F64CA3045b44199E",
    "AAVE/USD": "0xbfAE4fd8c6C60a13f7717160C67111D744198D9C",
}


@dataclass(slots=True)
class GMXDeployment:
    """GMX deployment configuration for Guard whitelisting.

    This dataclass encapsulates all GMX-related configuration needed
    when deploying a Lagoon vault with GMX perpetuals trading support.
    Pass an instance to ``deploy_automated_lagoon_vault()`` to automatically
    whitelist GMX contracts and markets during deployment.

    Example::

        # Recommended: use factory method with dynamic address fetch
        gmx_deployment = GMXDeployment.create_arbitrum(
            markets=[
                "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336",  # ETH/USD
                "0x47c031236e19d024b42f8AE6780E44A573170703",  # BTC/USD
            ],
        )
    """

    #: GMX ExchangeRouter contract address
    exchange_router: HexAddress

    #: GMX SyntheticsRouter contract address
    synthetics_router: HexAddress

    #: GMX OrderVault contract address
    order_vault: HexAddress

    #: List of GMX market addresses to whitelist for trading
    markets: list[HexAddress] = field(default_factory=list)

    #: Optional: specific tokens to whitelist as collateral
    #: If None, tokens are not explicitly whitelisted (use anyAsset or manual whitelisting)
    tokens: list[HexAddress] | None = None

    def __post_init__(self):
        """Validate and checksum addresses."""
        self.exchange_router = Web3.to_checksum_address(self.exchange_router)
        self.synthetics_router = Web3.to_checksum_address(self.synthetics_router)
        self.order_vault = Web3.to_checksum_address(self.order_vault)
        self.markets = [Web3.to_checksum_address(m) for m in self.markets]
        if self.tokens:
            self.tokens = [Web3.to_checksum_address(t) for t in self.tokens]

    @classmethod
    def create_arbitrum(
        cls,
        markets: list[HexAddress] | None = None,
        tokens: list[HexAddress] | None = None,
    ) -> "GMXDeployment":
        """Create a GMXDeployment for Arbitrum mainnet with dynamically fetched addresses.

        Fetches the latest GMX contract addresses from the GMX contracts registry
        on GitHub, ensuring addresses are always up-to-date even after GMX upgrades.

        :param markets:
            List of market addresses to whitelist. If None, no markets are whitelisted.

        :param tokens:
            List of token addresses to whitelist as collateral.

        :return:
            GMXDeployment configured for Arbitrum mainnet.

        :raises ValueError:
            If addresses cannot be fetched from the GMX API.
        """
        addresses = get_gmx_arbitrum_addresses()
        return cls(
            exchange_router=addresses["exchange_router"],
            synthetics_router=addresses["synthetics_router"],
            order_vault=addresses["order_vault"],
            markets=markets or [],
            tokens=tokens,
        )


def fetch_all_gmx_markets(web3: Web3) -> dict[HexAddress, MarketInfo]:
    """Fetch all available GMX markets from the blockchain.

    This function queries the GMX Reader contract to get a complete list
    of all available perpetual markets with their metadata.

    Example::

        from web3 import Web3
        from eth_defi.gmx.whitelist import fetch_all_gmx_markets

        web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
        markets = fetch_all_gmx_markets(web3)

        for address, info in markets.items():
            print(f"{info.market_symbol}: {address}")

    :param web3:
        Web3 instance connected to Arbitrum or another GMX-supported chain.

    :return:
        Dictionary mapping market addresses to MarketInfo objects.
    """
    config = GMXConfig(web3=web3)
    markets_fetcher = Markets(config)
    raw_markets = markets_fetcher.get_available_markets()

    result: dict[HexAddress, MarketInfo] = {}
    for market_address, market_data in raw_markets.items():
        info = markets_fetcher.get_market_info(market_address)
        if info:
            result[market_address] = info

    return result


def get_gmx_market_addresses(web3: Web3) -> Iterator[HexAddress]:
    """Get iterator of all GMX market addresses for a chain.

    Convenience function for scripting and batch operations.

    Example::

        from web3 import Web3
        from eth_defi.gmx.whitelist import get_gmx_market_addresses

        web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))

        for market_address in get_gmx_market_addresses(web3):
            print(market_address)

    :param web3:
        Web3 instance connected to Arbitrum or another GMX-supported chain.

    :return:
        Iterator of market addresses.
    """
    markets = fetch_all_gmx_markets(web3)
    return iter(markets.keys())


def resolve_gmx_market_labels(web3: Web3) -> dict[HexAddress, str]:
    """Build address-to-label mapping for GMX markets by querying on-chain data.

    Fetches all available GMX markets and builds a dictionary mapping
    each market address to a human-readable label like ``"GMX ETH/USD"``.

    This is useful for display purposes, e.g. passing the result as
    ``known_labels`` to :func:`format_guard_config_report`.

    Example::

        from eth_defi.gmx.whitelist import resolve_gmx_market_labels

        labels = resolve_gmx_market_labels(web3)
        # {"0x70d95587d40A2caf56bd97485aB3Eec10Bee6336": "GMX ETH/USD", ...}

    :param web3:
        Web3 instance connected to Arbitrum or another GMX-supported chain.

    :return:
        Dictionary mapping checksummed market addresses to labels.
    """
    labels: dict[HexAddress, str] = {}
    for addr, info in fetch_all_gmx_markets(web3).items():
        labels[Web3.to_checksum_address(addr)] = f"GMX {info.market_symbol}/USD"
    return labels


def whitelist_gmx_markets(
    guard: Contract,
    markets: list[HexAddress],
    owner: HexAddress,
    notes_prefix: str = "GMX market",
) -> list[HexBytes]:
    """Whitelist multiple GMX markets in a Guard contract.

    This function whitelists each market individually by calling
    ``whitelistGMXMarket()`` on the Guard contract. The caller must
    be the Guard owner (typically the Safe).

    Example::

        from eth_defi.gmx.whitelist import whitelist_gmx_markets

        tx_hashes = whitelist_gmx_markets(
            guard=guard_contract,
            markets=[
                "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336",  # ETH/USD
                "0x47c031236e19d024b42f8AE6780E44A573170703",  # BTC/USD
            ],
            owner=safe_address,
        )

    :param guard:
        Guard contract instance (GuardV0).

    :param markets:
        List of GMX market addresses to whitelist.

    :param owner:
        Address of the Guard owner (must have permission to whitelist).

    :param notes_prefix:
        Prefix for the notes string in each whitelist call.

    :return:
        List of transaction hashes for each whitelist operation.
    """
    tx_hashes = []

    for idx, market in enumerate(markets, start=1):
        market = Web3.to_checksum_address(market)
        note = f"{notes_prefix} #{idx}"

        logger.info("Whitelisting GMX market %s: %s", note, market)

        tx_hash = guard.functions.whitelistGMXMarket(
            market,
            note,
        ).transact({"from": owner})

        tx_hashes.append(tx_hash)

    return tx_hashes


def remove_gmx_markets(
    guard: Contract,
    markets: list[HexAddress],
    owner: HexAddress,
    notes_prefix: str = "Remove GMX market",
) -> list[HexBytes]:
    """Remove GMX markets from Guard whitelist.

    This function removes each market individually by calling
    ``removeGMXMarket()`` on the Guard contract.

    :param guard:
        Guard contract instance (GuardV0).

    :param markets:
        List of GMX market addresses to remove.

    :param owner:
        Address of the Guard owner (must have permission).

    :param notes_prefix:
        Prefix for the notes string in each remove call.

    :return:
        List of transaction hashes for each remove operation.
    """
    tx_hashes = []

    for idx, market in enumerate(markets, start=1):
        market = Web3.to_checksum_address(market)
        note = f"{notes_prefix} #{idx}"

        logger.info("Removing GMX market %s: %s", note, market)

        tx_hash = guard.functions.removeGMXMarket(
            market,
            note,
        ).transact({"from": owner})

        tx_hashes.append(tx_hash)

    return tx_hashes


def setup_gmx_whitelisting(
    guard: Contract,
    gmx_deployment: GMXDeployment,
    owner: HexAddress,
    safe_address: HexAddress,
) -> dict[str, list[HexBytes]]:
    """Set up complete GMX whitelisting on a Guard contract.

    This function performs all necessary whitelisting for GMX trading:

    1. Whitelist GMX router contracts (ExchangeRouter, SyntheticsRouter, OrderVault)
    2. Whitelist the Safe as a receiver
    3. Whitelist all specified markets
    4. Optionally whitelist collateral tokens

    Example::

        from eth_defi.gmx.whitelist import GMXDeployment, setup_gmx_whitelisting

        gmx = GMXDeployment.create_arbitrum(
            markets=["0x70d95587d40A2caf56bd97485aB3Eec10Bee6336"],
        )

        tx_hashes = setup_gmx_whitelisting(
            guard=guard_contract,
            gmx_deployment=gmx,
            owner=safe_address,
            safe_address=safe_address,
        )

    :param guard:
        Guard contract instance (GuardV0).

    :param gmx_deployment:
        GMX deployment configuration with router and market addresses.

    :param owner:
        Address of the Guard owner (must have permission to whitelist).

    :param safe_address:
        Safe address to whitelist as receiver.

    :return:
        Dictionary with transaction hashes grouped by operation type.
    """
    result: dict[str, list[HexBytes]] = {
        "router": [],
        "receiver": [],
        "markets": [],
        "tokens": [],
    }

    # 1. Whitelist GMX routers
    logger.info(
        "Whitelisting GMX routers: exchange=%s, synthetics=%s, order_vault=%s",
        gmx_deployment.exchange_router,
        gmx_deployment.synthetics_router,
        gmx_deployment.order_vault,
    )
    collateral_tokens = gmx_deployment.tokens or []
    tx_hash = guard.functions.whitelistGMX(
        gmx_deployment.exchange_router,
        gmx_deployment.synthetics_router,
        gmx_deployment.order_vault,
        collateral_tokens,
        "GMX router whitelisting",
    ).transact({"from": owner})
    result["router"].append(tx_hash)
    result["tokens"] = collateral_tokens

    # 2. Whitelist Safe as receiver
    logger.info("Whitelisting Safe as receiver: %s", safe_address)
    tx_hash = guard.functions.allowReceiver(
        safe_address,
        "Safe receiver for GMX",
    ).transact({"from": owner})
    result["receiver"].append(tx_hash)

    # 3. Whitelist markets
    if gmx_deployment.markets:
        market_tx_hashes = whitelist_gmx_markets(
            guard=guard,
            markets=gmx_deployment.markets,
            owner=owner,
        )
        result["markets"].extend(market_tx_hashes)

    return result


class GMXRouterNotWhitelisted(Exception):
    """The GMX ExchangeRouter we would trade through is not allowed by the Guard.

    Raised by :func:`assert_gmx_router_whitelisted`. Every order created through
    this router would revert on-chain with ``Target not allowed``, so this is a
    fatal startup condition rather than a per-order error.
    """


def assert_gmx_router_whitelisted(
    guard: Contract,
    exchange_router: HexAddress | str,
    *,
    order_vault: HexAddress | str | None = None,
) -> None:
    """Check a Guard allows the GMX ExchangeRouter before any order is sent.

    GMX rotates its ``ExchangeRouter`` between releases, while a Lagoon vault Guard
    enforces a fixed address allowlist. When the two disagree, every order-creating
    transaction reverts with ``Target not allowed`` — including exits, so the bot
    cannot flatten risk. The failure is otherwise only discovered by spending gas
    on a reverting transaction, once per order.

    Call this at startup so the mismatch surfaces as one clear error instead.

    Example::

        from eth_defi.gmx.contracts import get_contract_addresses
        from eth_defi.gmx.whitelist import assert_gmx_router_whitelisted

        addresses = get_contract_addresses("arbitrum")
        assert_gmx_router_whitelisted(
            guard,
            addresses.exchangerouter,
            order_vault=addresses.ordervault,
        )

    :param guard:
        Guard contract instance (GuardV0), usually the Safe's
        ``TradingStrategyModuleV0``.

    :param exchange_router:
        GMX ExchangeRouter address that orders would be routed through.

    :param order_vault:
        If given, also assert the Guard maps this router to this OrderVault.
        ``sendWnt``/``sendTokens`` receiver validation uses that mapping, so a
        stale value fails at order time even when the router itself is allowed.

    :raises GMXRouterNotWhitelisted:
        If the router is not whitelisted, or is mapped to a different OrderVault.
    """
    exchange_router = Web3.to_checksum_address(exchange_router)

    if not guard.functions.isAllowedGMXRouter(exchange_router).call():
        raise GMXRouterNotWhitelisted(
            f"GMX ExchangeRouter {exchange_router} is not whitelisted on guard {guard.address}. Every order through this router will revert with 'Target not allowed'. Fix: the guard owner must call whitelistGMX(exchangeRouter, syntheticsRouter, orderVault, collateralTokens, notes) for this router, or pin GMX contract resolution to a release whose router is already allowed (see eth_defi.gmx.contracts.GMX_CONTRACT_RELEASE_ENV_VAR).",
        )

    if order_vault is not None:
        order_vault = Web3.to_checksum_address(order_vault)
        mapped = Web3.to_checksum_address(guard.functions.gmxOrderVaults(exchange_router).call())
        if mapped != order_vault:
            raise GMXRouterNotWhitelisted(
                f"GMX ExchangeRouter {exchange_router} is whitelisted on guard {guard.address}, but maps to OrderVault {mapped} instead of the expected {order_vault}. sendWnt()/sendTokens() receiver validation will reject orders. Fix: re-run whitelistGMX() with the correct OrderVault.",
            )

    logger.info(
        "GMX ExchangeRouter %s is whitelisted on guard %s",
        exchange_router,
        guard.address,
    )
