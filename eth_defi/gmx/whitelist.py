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
#: .. warning::
#:
#:     These addresses may become stale when GMX upgrades contracts.
#:     Prefer using :func:`get_gmx_arbitrum_addresses` or
#:     :meth:`GMXDeployment.create_arbitrum` which fetch addresses
#:     dynamically from the GMX contracts registry.
#:
#: These are the official GMX V2 contract addresses required for
#: whitelisting GMX trading in a Guard contract.
GMX_ARBITRUM_ADDRESSES: dict[str, HexAddress] = {
    "exchange_router": "0x7C68C7866A64FA2160F78EEaE12217FFbf871fa8",
    "synthetics_router": "0x7452c558d45f8afC8c83dAe62C3f8A5BE19c71f6",
    "order_vault": "0x31eF83a530Fde1B38EE9A18093A333D8Bbbc40D5",
}


def get_gmx_arbitrum_addresses() -> dict[str, HexAddress]:
    """Fetch current GMX contract addresses for Arbitrum mainnet.

    Unlike :data:`GMX_ARBITRUM_ADDRESSES` which may become stale,
    this function dynamically fetches the latest addresses from
    the GMX contracts registry on GitHub.

    :return:
        Dictionary with keys ``exchange_router``, ``synthetics_router``, ``order_vault``.

    :raises ValueError:
        If addresses cannot be fetched from the GMX API.
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
    tx_hash = guard.functions.whitelistGMX(
        gmx_deployment.exchange_router,
        gmx_deployment.synthetics_router,
        gmx_deployment.order_vault,
        "GMX router whitelisting",
    ).transact({"from": owner})
    result["router"].append(tx_hash)

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

    # 4. Whitelist tokens if specified
    if gmx_deployment.tokens:
        for idx, token in enumerate(gmx_deployment.tokens, start=1):
            token = Web3.to_checksum_address(token)
            logger.info("Whitelisting GMX collateral token #%d: %s", idx, token)
            tx_hash = guard.functions.whitelistToken(
                token,
                f"GMX collateral token #{idx}",
            ).transact({"from": owner})
            result["tokens"].append(tx_hash)

    return result
