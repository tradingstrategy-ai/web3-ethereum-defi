"""Reviewed public information for Gains-family vault deployments.

Contract compatibility does not establish who operates a vault. The inventory
includes official gTrade vaults
and historical or staging deployments for which affiliation is unconfirmed.
Identity is always scoped to both chain and address. Reviewed 2026-10-28 using
the `official address directory <https://docs.gains.trade/what-is-gains-network/contract-addresses>`__
and the public vault export. Nova has its own protocol integration in
:mod:`eth_defi.erc_4626.vault_protocol.nova_markets`.
"""

from dataclasses import dataclass

from eth_typing import HexAddress

#: Current native vault overview, replacing the old singular ``/vault`` route.
GTRADE_VAULT_APP_URL = "https://gains.trade/vaults"
#: Official description of fee income, trader PnL, and the collateral buffer.
GTOKEN_DOCUMENTATION = "https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults"
#: Official withdrawal-window and request/claim explanation.
GTOKEN_WITHDRAWALS = f"{GTOKEN_DOCUMENTATION}/staker-faq"
#: Original product announcement; historical mechanics may have changed.
GTOKEN_ANNOUNCEMENT = ("Introducing gToken vaults (7 December 2022)", "https://medium.com/gains-network/introducing-gtoken-vaults-ea98f10a49d5")
#: Launch of ETH and USDC collateral vaults.
MULTICOLLATERAL_ANNOUNCEMENT = (
    "gTrade v7: gETH, gUSDC and multi-collateral deposits (27 January 2024)",
    "https://medium.com/gains-network/introducing-gtrade-v7-geth-gusdc-and-multi-collateral-deposits-b4dabcf9b991",
)
#: Launch of GNS as trading collateral.
GNS_ANNOUNCEMENT = ("Introducing gGNS (13 March 2025)", "https://medium.com/gains-network/introducing-ggns-unlocking-gns-as-trading-collateral-5a8497d40451")
#: Bifrost partnership and Base gBTCUSD launch on 13 June 2025.
BTCUSD_ANNOUNCEMENT = (
    "Bitcoin-collateralised trading is coming to gTrade (12 June 2025)",
    "https://medium.com/gains-network/bitcoin-collateralized-trading-is-coming-to-gtrade-1e6cc0475f92",
)
#: Official deployment directories, checked against exact addresses.
ARBITRUM_ADDRESSES = "https://docs.gains.trade/what-is-gains-network/contract-addresses/arbitrum-mainnet"
POLYGON_ADDRESSES = "https://docs.gains.trade/what-is-gains-network/contract-addresses/polygon-mainnet"
BASE_ADDRESSES = "https://docs.gains.trade/what-is-gains-network/contract-addresses/base-mainnet"
MEGAETH_ADDRESSES = "https://docs.gains.trade/what-is-gains-network/contract-addresses/megaeth-mainnet"


@dataclass(slots=True, frozen=True)
class GainsVaultMetadata:
    """Public evidence for one reviewed deployment, including unconfirmed ones."""

    #: Product name; an unconfirmed name is the reported listing name only.
    name: str
    #: Underlying collateral established by official documentation, if available.
    collateral: str | None = None
    #: Verified operator; ``None`` means affiliation has not been established.
    curator: str | None = None
    #: Official app destination; omitted when no matching product was verified.
    app_url: str | None = None
    #: Primary source establishing the exact deployment's identity.
    source_url: str | None = None
    #: Dated historical product announcement title and URL, when verified.
    announcement: tuple[str, str] | None = None
    #: Material product-specific context beyond the shared strategy.
    extra_note: str = ""


#: Audited union of the public export and previous strategy mappings: 24
#: deployments, with Nova separately reclassified. Unconfirmed entries are retained for transparent coverage,
#: not treated as official gTrade products. See README-gTrade-metadata.md.
REVIEWED_GAINS_VAULTS: dict[tuple[int, str], GainsVaultMetadata] = {
    #: Polygon gDAI, gETH and gUSDC: exact addresses in POLYGON_ADDRESSES.
    (137, "0x91993f2101cc758d0deb7279d41e880f7defe827"): GainsVaultMetadata("gDAI", "DAI", "gains-network", "https://gains.trade/vaults/gDAI", POLYGON_ADDRESSES, GTOKEN_ANNOUNCEMENT),
    (137, "0x1544e1ff1a6f6bdbfb901622c12bb352a43464fb"): GainsVaultMetadata("gETH", "WETH", "gains-network", "https://gains.trade/vaults/gETH", POLYGON_ADDRESSES, MULTICOLLATERAL_ANNOUNCEMENT),
    (137, "0x29019fe2e72e8d4d2118e8d0318bef389ffe2c81"): GainsVaultMetadata("gUSDC", "USDC", "gains-network", "https://gains.trade/vaults/gUSDC", POLYGON_ADDRESSES, MULTICOLLATERAL_ANNOUNCEMENT),
    #: Arbitrum gDAI, gETH, gUSDC and gGNS: exact addresses in ARBITRUM_ADDRESSES.
    (42161, "0xd85e038593d7a098614721eae955ec2022b9b91b"): GainsVaultMetadata("gDAI", "DAI", "gains-network", "https://gains.trade/vaults/gDAI", ARBITRUM_ADDRESSES, GTOKEN_ANNOUNCEMENT),
    (42161, "0x5977a9682d7af81d347cfc338c61692163a2784c"): GainsVaultMetadata("gETH", "WETH", "gains-network", "https://gains.trade/vaults/gETH", ARBITRUM_ADDRESSES, MULTICOLLATERAL_ANNOUNCEMENT),
    (42161, "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0"): GainsVaultMetadata("gUSDC", "USDC", "gains-network", "https://gains.trade/vaults/gUSDC", ARBITRUM_ADDRESSES, MULTICOLLATERAL_ANNOUNCEMENT),
    (42161, "0x4beef1113f968326905224d2ca272f3032a9a9f4"): GainsVaultMetadata("gGNS", "GNS", "gains-network", "https://gains.trade/vaults/gGNS", ARBITRUM_ADDRESSES, GNS_ANNOUNCEMENT),
    #: Base gUSDC and gBTCUSD: exact addresses in BASE_ADDRESSES.
    (8453, "0xad20523a7dc37babc1cc74897e4977232b3d02e5"): GainsVaultMetadata("gUSDC", "USDC", "gains-network", "https://gains.trade/vaults/gUSDC", BASE_ADDRESSES, MULTICOLLATERAL_ANNOUNCEMENT),
    (8453, "0xb7cb7cb7c3cd96e251c9bf8800b9631134bbadc6"): GainsVaultMetadata(
        "gBTCUSD",
        "BtcUSD",
        "gains-network",
        "https://gains.trade/vaults/gBTCUSD",
        BASE_ADDRESSES,
        BTCUSD_ANNOUNCEMENT,
        "BtcUSD is a Bitcoin-backed stablecoin issued through Bifrost's BTCFi protocol, rather than BTC itself. Gains documents that this vault relies on Bifrost to restore its overcollateralisation buffer instead of minting GNS; this is a partner dependency, not a repayment guarantee.",
    ),
    #: MegaETH gUSDm: exact address in MEGAETH_ADDRESSES.
    (4326, "0x46344456f130e9dcdea7f98cdb0e02fb9f4ab72d"): GainsVaultMetadata("gUSDm", "USDm", "gains-network", "https://gains.trade/vaults/gUSDM", MEGAETH_ADDRESSES),
    #: Historical/staging names from the public export and former tags.py.
    #: The 2026-10-28 review found no primary source linking these exact
    #: deployments to official gTrade products or a supported app destination.
    (42161, "0xfe3e29b3328026003a15bf0846846b03af86b537"): GainsVaultMetadata("gDAI (historical deployment)"),
    (42161, "0xf40808f50b8d858f3ac6d10c441bb61da4564d53"): GainsVaultMetadata("Gains Network DAI (historical deployment)"),
    (42161, "0x992eb7040b66b13abea94e2621d4e61d5ce608bd"): GainsVaultMetadata("mUSDC"),
    (42161, "0x9b9469a68b3d94446c4c466bf8015bdaef971cf4"): GainsVaultMetadata("Gains Network USDC (staging)"),
    (42161, "0xb7058370db10f0712eddb297bc3a58c3a2e5c3a7"): GainsVaultMetadata("Gains Network USDC (staging)"),
    (42161, "0xfc7c19509e96415f32d80f47a08cc9ff84e7580d"): GainsVaultMetadata("Gains Network USDC (staging)"),
    (56, "0xd796a9e7e30bfc1b1a9380f501430f681c31eb78"): GainsVaultMetadata("X-Cloud USD"),
    (56, "0xfb34af2138280e13b0759fd322fe63fccc7508a6"): GainsVaultMetadata("X-Solaris USD"),
    (56, "0x28e1afcd2d91a7f0ea49e81192599fbe1e700169"): GainsVaultMetadata("hsUSDT"),
    (56, "0x1e98b6143a4eaf78ab63de8ea8186eec3dbe5edc"): GainsVaultMetadata("hsUSD1"),
    (8453, "0xd78bd3aef2e8aa7820fea8ffb33eddc4f13fa933"): GainsVaultMetadata("Staked ARA Vault"),
    (999, "0x31297b564fb8ec52a7d84cc2dee437e0992ef2b8"): GainsVaultMetadata("xVault"),
    (80094, "0x6a6e4ad4a5ca14b940cd6949b1a90f947ae21c19"): GainsVaultMetadata("hsHONEY"),
    (80094, "0xdd560bc2c98bc3fa39fcafe256249707f9b83b3c"): GainsVaultMetadata("hsHONEY"),
}


def lookup_gains_vault_metadata(chain_id: int, address: HexAddress | str) -> GainsVaultMetadata | None:
    """Look up a deployment without inferring affiliation from its name.

    Uses only maintained evidence and performs no network reads.

    :param chain_id: Deployment chain ID.
    :param address: Vault address, in either checksum or lowercase form.
    :return: Reviewed information, or ``None`` for an unreviewed deployment.
    """
    return REVIEWED_GAINS_VAULTS.get((chain_id, str(address).lower()))


def format_gains_vault_note(chain_id: int, address: HexAddress | str) -> str | None:
    """Describe the strategy and source history for a reviewed deployment.

    Unverified products receive an explicit information gap rather than a
    borrowed gTrade strategy or fabricated announcement.

    :param chain_id: Deployment chain ID.
    :param address: Vault contract address.
    :return: Markdown note, or ``None`` for an unreviewed deployment.
    """
    metadata = lookup_gains_vault_metadata(chain_id, address)
    if metadata is None:
        return None
    if metadata.curator is None:
        return f"**Strategy and affiliation review (28 October 2026):** {metadata.name}. A published strategy, operator affiliation, launch announcement and matching deposit app could not be verified for this exact deployment. A Gains-compatible contract or a familiar token name alone does not establish that Gains Network operates it. The homepage therefore links to the contract explorer. Compare the [official Gains Network address directory](https://docs.gains.trade/what-is-gains-network/contract-addresses)."
    announcement = f"**Announcement:** [{metadata.announcement[0]}]({metadata.announcement[1]}). This is historical product context, not a separate deployment announcement for every chain." if metadata.announcement else "**Announcements:** A separately dated launch announcement for this deployment was not verified in the 28 October 2026 review."
    extra_note = f"{metadata.extra_note}\n\n" if metadata.extra_note else ""
    return (
        f"**Strategy:** {metadata.name} accepts {metadata.collateral} and provides counterparty liquidity "
        f"for gTrade's synthetic leveraged markets. The [gToken strategy documentation]({GTOKEN_DOCUMENTATION}) "
        "describes income from trading fees, with trader losses building an overcollateralisation buffer "
        "and trader profits paid from vault liquidity. If that buffer is insufficient, depositor value can fall. "
        "Returns depend on fees and trader PnL, with exposure to the underlying collateral's price and credit risks.\n\n"
        f"**Withdrawals:** The [withdrawal rules]({GTOKEN_WITHDRAWALS}) require a request and a later claim "
        "in the designated epoch. The wait is one to three epochs depending on collateralisation; missing the claim "
        "window requires a new request. Check the current app before depositing.\n\n"
        f"{extra_note}"
        f"**Deployment and app:** The [official contract directory]({metadata.source_url}) identifies this "
        f"deployment. Open the [{metadata.name} app]({metadata.app_url}) and select the deployment network.\n\n"
        f"{announcement}"
    )
