"""Manual descriptions for Securitize DSToken funds.

Securitize DSTokens share a token contract interface, but their investment
strategy, fund manager, NAV source and investor terms belong to the individual
fund. This registry covers reviewed EVM investment-fund deployments identified
from the DSToken scan and official issuer address lists. It intentionally
excludes company securities and test tokens.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from eth_typing import HexAddress

from eth_defi.vault.fee import FeeData, VaultFeeMode


@dataclass(slots=True, frozen=True)
class SecuritizeProduct:
    """Manual metadata and valuation assumptions for a DSToken fund."""

    #: EVM chain hosting this DSToken.
    chain_id: int
    #: DSToken address.
    token: HexAddress
    #: Human-readable product name.
    product_name: str
    #: Compact product description for vault metadata.
    short_description: str
    #: Product description for vault metadata.
    description: str
    #: Fund manager or product issuer shown in vault metadata.
    manager_name: str
    #: Curator slug for the asset manager.
    curator_slug: str
    #: Product page or fund announcement.
    homepage: str
    #: Human-readable fund note shown alongside vault metrics.
    notes: str
    #: Adapter-provided share-price estimate, if safely supported.
    estimated_nav_per_share: Decimal | None
    #: Identifier for the configured price source or estimate.
    nav_source: str
    #: Human-readable denomination for a known fund NAV.
    denomination: str | None = None
    #: Reviewed fund fee schedule, or ``None`` when fees are unknown.
    #:
    #: Excluded from hashing because :py:class:`FeeData` is mutable and
    #: products are used as dictionary keys.
    fee_data: FeeData | None = field(default=None, hash=False, compare=False)


BUIDL_FUND_PAGE_URL = "https://www.blackrock.com/us/individual/products/buidl/"
#: I Class belongs to the BUIDL product family. BlackRock does not publish a
#: separate public I Class landing page, so use the family product page rather
#: than a non-product compliance page.
BUIDL_I_FUND_PAGE_URL = BUIDL_FUND_PAGE_URL
ACRED_FUND_PAGE_URL = "https://securitize.io/primary-market/apollo-diversified-credit-securitize-fund"
VBILL_FUND_PAGE_URL = "https://securitize.io/primary-market/vaneck-vbill"
STAC_FUND_PAGE_URL = "https://www.securitize-stac.com/"
ARCOIN_FUND_PAGE_URL = "https://www.arcalabs.com/fund-overview"
SPICE_FUND_PAGE_URL = "https://spicevc.com/"
HLSCOPE_FUND_PAGE_URL = "https://www.hamiltonlane.com/en-us/strategies/evergreen/global/senior-credit-opportunities-fund"
BLOCKCHAIN_CAPITAL_FUND_PAGE_URL = "https://www.blockchaincapital.com/about-us"
COSIMO_X_FUND_PAGE_URL = "https://www.cosimodigital.com/asset-management/cosimo-x"
SCIENCE_BLOCKCHAIN_FUND_PAGE_URL = "https://www.science-inc.com/blockchain.html"
PROTOS_FUND_PAGE_URL = "https://protosmanagement.com/2024/05/09/protos-asset-management-releases-march-31-2024-prts-token-nav/"
MI4_FUND_PAGE_URL = "https://securitize.io/primary-market/mantle-index-four-fund"
ARKVX_FUND_PAGE_URL = "https://www.ark-funds.com/funds/arkvx"
#: Prospectus and statement of additional information dated 2025-10-28.
ARKVX_PROSPECTUS_URL = "https://www.sec.gov/Archives/edgar/data/1905088/000121390025102648/ea0260971-01_486bpos.htm"
#: Investment Company Act Release No. 36333 permitting a tokenised share class.
ARKVX_SEC_ORDER_URL = "https://www.sec.gov/Archives/edgar/data/1905088/999999999726001524/filename1.pdf"
#: Rule 23c-3 repurchase offer notice with the 2026-09-30 deadline.
ARKVX_REPURCHASE_OFFER_URL = "https://www.sec.gov/Archives/edgar/data/1905088/000121390026096448/ea0304158-01_n23c3a.htm"


def _create_buidl_product(chain_id: int, token: str, chain_name: str) -> SecuritizeProduct:
    """Create metadata for an official EVM BUIDL deployment.

    BlackRock lists the same fund on several EVM chains. Keeping the common
    fund description in one constructor prevents chain copies from drifting.

    :param chain_id:
        EVM chain id.
    :param token:
        Official BlackRock token address.
    :param chain_name:
        Human-readable deployment chain.
    :return:
        Reviewed BUIDL product metadata.
    """

    return SecuritizeProduct(
        chain_id=chain_id,
        token=HexAddress(token.lower()),
        product_name="BlackRock USD Institutional Digital Liquidity Fund",
        short_description="U.S.-dollar liquidity strategy investing in cash, Treasury bills and repurchase agreements",
        description="Tokenised fund investing in cash, U.S. Treasury bills and repurchase agreements.",
        manager_name="BlackRock",
        curator_slug="blackrock",
        homepage=BUIDL_FUND_PAGE_URL,
        notes=f"""BlackRock USD Institutional Digital Liquidity Fund (BUIDL) on {chain_name}.

- **Curator:** BlackRock / Securitize.
- **Vault strategy:** Tokenised shares in a fund that invests in cash, U.S. Treasury bills and repurchase agreements.
- **Token structure:** BUIDL is a permissioned Securitize token. Investors must complete issuer eligibility and compliance checks before subscribing, redeeming or transferring shares.
- **Stable dollar share value:** BUIDL targets a USD 1 share value. Fund income accrues daily and is distributed monthly as newly issued BUIDL shares to eligible holders, rather than increasing the unit price. The token is therefore modelled at an estimated USD 1 per share and the on-chain share price does not represent total return.
- **Fund page:** [BlackRock BUIDL]({BUIDL_FUND_PAGE_URL}).
""",
        estimated_nav_per_share=Decimal("1"),
        nav_source="estimated_buidl_usd_1",
        denomination="USD",
    )


#: BlackRock USD Institutional Digital Liquidity Fund on Ethereum.
#:
#: https://etherscan.io/address/0x7712c34205737192402172409a8f7ccef8aa2aec
BUIDL_ETHEREUM = _create_buidl_product(1, "0x7712c34205737192402172409a8f7ccef8aa2aec", "Ethereum")

#: BlackRock BUIDL on Polygon.
BUIDL_POLYGON = _create_buidl_product(137, "0x2893Ef551B6dD69F661Ac00F11D93E5Dc5Dc0e99", "Polygon")

#: BlackRock BUIDL on Avalanche.
BUIDL_AVALANCHE = _create_buidl_product(43_114, "0x53FC82f14F009009b440a706e31c9021E1196A2F", "Avalanche")

#: BlackRock BUIDL on Optimism.
BUIDL_OPTIMISM = _create_buidl_product(10, "0xa1CDAb15bBA75a80dF4089CaFbA013e376957cF5", "Optimism")

#: BlackRock BUIDL on Arbitrum.
BUIDL_ARBITRUM = _create_buidl_product(42_161, "0xA6525Ae43eDCd03dC08E775774dCAbd3bb925872", "Arbitrum")

#: BlackRock BUIDL I Class on Ethereum.
#:
#: https://etherscan.io/address/0x6a9da2d710bb9b700acde7cb81f10f1ff8c89041
BUIDL_I_ETHEREUM = SecuritizeProduct(
    chain_id=1,
    token=HexAddress("0x6a9da2d710bb9b700acde7cb81f10f1ff8c89041"),
    product_name="BlackRock USD Institutional Digital Liquidity Fund - I Class",
    short_description="U.S.-dollar liquidity strategy investing in cash, Treasury bills and repurchase agreements",
    description="I Class tokenised shares in BlackRock's U.S. dollar liquidity fund.",
    manager_name="BlackRock",
    curator_slug="blackrock",
    homepage=BUIDL_I_FUND_PAGE_URL,
    notes=f"""BlackRock USD Institutional Digital Liquidity Fund - I Class (BUIDL-I).

- **Curator:** BlackRock / Securitize.
- **Vault strategy:** I Class tokenised shares in the same BlackRock liquidity-fund product family as BUIDL, investing in cash, U.S. Treasury bills and repurchase agreements.
- **Token structure:** BUIDL-I is a permissioned Securitize DSToken. Investors must complete issuer eligibility and compliance checks before subscribing, redeeming or transferring shares.
- **Stable dollar share value:** BUIDL-I targets a USD 1 share value. Fund income accrues daily and is distributed monthly as newly issued shares, rather than increasing the unit price. The token is therefore modelled at an estimated USD 1 per share and the on-chain share price does not represent total return.
- **Fund page:** [BlackRock BUIDL]({BUIDL_I_FUND_PAGE_URL}).
""",
    estimated_nav_per_share=Decimal("1"),
    nav_source="estimated_buidl_usd_1",
    denomination="USD",
)

#: Apollo Diversified Credit Securitize Fund on Ethereum.
#:
#: https://etherscan.io/address/0x17418038ecf73ba4026c4f428547bf099706f27b
ACRED_ETHEREUM = SecuritizeProduct(
    chain_id=1,
    token=HexAddress("0x17418038ecf73ba4026c4f428547bf099706f27b"),
    product_name="Apollo Diversified Credit Securitize Fund",
    short_description="Diversified global private-credit strategy",
    description="Tokenised feeder fund providing access to Apollo's diversified global credit strategy.",
    manager_name="Apollo",
    curator_slug="apollo",
    homepage=ACRED_FUND_PAGE_URL,
    notes=f"""Apollo Diversified Credit Securitize Fund (ACRED).

- **Curator:** Apollo / Securitize.
- **Vault strategy:** Tokenised feeder fund investing in Apollo Diversified Credit Fund, a diversified global-credit strategy spanning corporate direct lending, asset-backed lending, and performing, dislocated and structured credit.
- **NAV reporting:** The fund supports subscriptions and redemptions at daily NAV. Its NAV changes with the underlying credit portfolio; historical NAV is read from RedStone's ACRED fundamental feed rather than modelled as a fixed share price.
- **Investor access:** The product is available to qualifying investors through Securitize Markets.
- **Fund page:** [Apollo Diversified Credit Securitize Fund]({ACRED_FUND_PAGE_URL}).
""",
    estimated_nav_per_share=None,
    nav_source="redstone_acred_fundamental",
    denomination="USD",
)

#: VanEck Treasury Fund on Ethereum.
#:
#: https://etherscan.io/address/0x2255718832bc9fd3be1caf75084f4803da14ff01
VBILL_ETHEREUM = SecuritizeProduct(
    chain_id=1,
    token=HexAddress("0x2255718832bc9fd3be1caf75084f4803da14ff01"),
    product_name="VanEck Treasury Fund",
    short_description="Short-term U.S. Treasury cash-management strategy",
    description="Tokenised fund designed to provide U.S. Treasury-backed cash management.",
    manager_name="VanEck",
    curator_slug="vaneck",
    homepage=VBILL_FUND_PAGE_URL,
    notes=f"""VanEck Treasury Fund (VBILL).

- **Curator:** VanEck / Securitize.
- **Vault strategy:** Tokenised fund investing in short-term U.S. Treasury obligations, repurchase agreements collateralised by U.S. Treasury obligations and cash for redemptions.
- **NAV reporting:** The fund seeks to maintain a stable USD 1 net asset value and has daily NAV calculations. Historical NAV is read from RedStone's Ethereum VBILL fundamental feed, so the adapter does not assume a fixed share price.
- **Investor access:** The fund is designed for institutional and qualified investors.
- **Fund page:** [VanEck Treasury Fund]({VBILL_FUND_PAGE_URL}).
""",
    estimated_nav_per_share=None,
    nav_source="redstone_vbill_ethereum_fundamental",
    denomination="USD",
)

#: Securitize Tokenized AAA CLO Fund on Ethereum.
#:
#: https://etherscan.io/address/0x51c2d74017390cbbd30550179a16a1c28f7210fc
STAC_ETHEREUM = SecuritizeProduct(
    chain_id=1,
    token=HexAddress("0x51c2d74017390cbbd30550179a16a1c28f7210fc"),
    product_name="Securitize Tokenized AAA CLO Fund",
    short_description="AAA-rated collateralised-loan-obligation strategy",
    description="Tokenised fund providing exposure to a portfolio of AAA-rated collateralised loan obligations.",
    manager_name="BNY Investments",
    curator_slug="bny-investments",
    homepage=STAC_FUND_PAGE_URL,
    notes=f"""Securitize Tokenized AAA CLO Fund (STAC).

- **Curator:** BNY Investments / Securitize.
- **Vault strategy:** Tokenised fund dedicated to U.S. dollar-denominated collateralised loan obligations with AAA-rated tranches.
- **Fund oversight:** The fund was developed with BNY, which acts as custodian for the underlying assets; BNY Investments is the fund's sub-adviser.
- **NAV reporting:** The share value follows the NAV of the CLO portfolio and is not a stable-dollar fund. Historical NAV is read from RedStone's STAC fundamental feed; Chronicle separately verifies the fund's assets and valuation inputs through its Proof of Asset dashboard.
- **Fund page:** [Securitize Tokenized AAA CLO Fund]({STAC_FUND_PAGE_URL}).
""",
    estimated_nav_per_share=None,
    nav_source="redstone_stac_fundamental",
    denomination="USD",
)

#: Arca U.S. Treasury Fund shares on Ethereum.
#:
#: https://etherscan.io/address/0x252739487c1fa66eaeae7ced41d6358ab2a6bca9
ARCOIN_ETHEREUM = SecuritizeProduct(
    chain_id=1,
    token=HexAddress("0x252739487c1fa66eaeae7ced41d6358ab2a6bca9"),
    product_name="Arca U.S. Treasury Fund",
    short_description="Short-term U.S. Treasury strategy",
    description="Tokenised shares in a fund investing primarily in short-term U.S. Treasury securities.",
    manager_name="Arca",
    curator_slug="arca",
    homepage=ARCOIN_FUND_PAGE_URL,
    notes=f"""Arca U.S. Treasury Fund (ArCoin).

- **Curator:** Arca / Securitize.
- **Vault strategy:** Tokenised shares in a registered fund investing primarily in short-term U.S. Treasury securities, with cash and other high-quality fixed-income instruments permitted for liquidity and portfolio management.
- **NAV reporting:** ArCoin's value follows the fund's net asset value and may change with the portfolio. This integration does not model a fixed share price.
- **Investor access:** The fund makes periodic repurchase offers; eligible investors use the fund's designated process to request repurchase of their shares.
- **Fund page:** [Arca U.S. Treasury Fund]({ARCOIN_FUND_PAGE_URL}).
""",
    estimated_nav_per_share=None,
    nav_source="unconfigured",
    denomination="USD",
)

#: SPiCE Venture Capital Fund shares on Ethereum.
#:
#: https://etherscan.io/address/0x0324dd195d0cd53f9f07bee6a48ee7a20bad738f
SPICE_VC_ETHEREUM = SecuritizeProduct(
    chain_id=1,
    token=HexAddress("0x0324dd195d0cd53f9f07bee6a48ee7a20bad738f"),
    product_name="SPiCE Venture Capital Fund",
    short_description="Venture-capital strategy investing in blockchain and tokenisation businesses",
    description="Tokenised interests in a venture-capital fund investing in blockchain and tokenisation businesses.",
    manager_name="SPiCE VC",
    curator_slug="spice-vc",
    homepage=SPICE_FUND_PAGE_URL,
    notes=f"""SPiCE Venture Capital Fund (SPICE).

- **Curator:** SPiCE VC / Securitize.
- **Vault strategy:** Tokenised interests in a venture-capital fund investing in early-stage companies building blockchain and tokenisation infrastructure.
- **NAV reporting:** SPiCE publishes periodic net-asset-value reports for the fund. The value depends on the underlying venture portfolio, so this integration does not use a fixed share-price estimate.
- **Investor access:** Fund interests are permissioned digital securities and remain subject to the fund's investor-eligibility and transfer rules.
- **Fund page:** [SPiCE VC]({SPICE_FUND_PAGE_URL}).
""",
    estimated_nav_per_share=None,
    nav_source="unconfigured",
    denomination="USD",
)

#: Hamilton Lane SCOPE Securitize tokenised feeder-fund shares on Ethereum.
#:
#: https://etherscan.io/address/0xda2ffa104356688e74d9340519b8c17f00d7752e
HLSCOPE_ETHEREUM = SecuritizeProduct(
    chain_id=1,
    token=HexAddress("0xda2ffa104356688e74d9340519b8c17f00d7752e"),
    product_name="Hamilton Lane SCOPE Securitize Tokenized Feeder Fund",
    short_description="Senior secured private-credit strategy",
    description="Tokenised feeder-fund interests providing exposure to Hamilton Lane's senior private-credit strategy.",
    manager_name="Hamilton Lane",
    curator_slug="hamilton-lane",
    homepage=HLSCOPE_FUND_PAGE_URL,
    notes=f"""Hamilton Lane SCOPE Securitize Tokenized Feeder Fund (HLSCOPE).

- **Curator:** Hamilton Lane / Securitize.
- **Vault strategy:** Tokenised feeder-fund interests providing access to Hamilton Lane's Senior Credit Opportunities Fund, an evergreen private-credit strategy focused on senior secured loans.
- **NAV reporting:** The underlying strategy's valuations are determined periodically and the share value can change with its private-credit holdings. Historical NAV is read from RedStone's HLSCOPE fundamental feed rather than using a fixed share-price estimate.
- **Investor access:** The fund is a permissioned Securitize offering for eligible investors; subscription and redemption terms follow the feeder-fund documentation.
- **Fund page:** [Hamilton Lane Senior Credit Opportunities Fund]({HLSCOPE_FUND_PAGE_URL}).
""",
    estimated_nav_per_share=None,
    nav_source="redstone_hlscope_fundamental",
    denomination="USD",
)

#: Blockchain Capital III Digital Liquid Venture Fund shares on Ethereum.
#:
#: https://etherscan.io/address/0x1f41e42d0a9e3c0dd3ba15b527342783b43200a9
BCAP_ETHEREUM = SecuritizeProduct(
    chain_id=1,
    token=HexAddress("0x1f41e42d0a9e3c0dd3ba15b527342783b43200a9"),
    product_name="Blockchain Capital III Digital Liquid Venture Fund",
    short_description="Digital-liquid venture strategy for blockchain and cryptocurrency companies",
    description="Tokenised fund interests in Blockchain Capital's digital liquid venture fund.",
    manager_name="Blockchain Capital",
    curator_slug="blockchain-capital",
    homepage=BLOCKCHAIN_CAPITAL_FUND_PAGE_URL,
    notes=f"""Blockchain Capital III Digital Liquid Venture Fund (BCAP).

- **Curator:** Blockchain Capital / Securitize.
- **Vault strategy:** Tokenised fund interests in a venture-capital fund investing in companies building blockchain and cryptocurrency products.
- **NAV reporting:** The value follows the fund's net asset value and depends on its venture portfolio. Historical NAV is read from RedStone's BCAP fundamental feed rather than using a fixed share-price estimate.
- **Investor access:** BCAP is a permissioned Securitize offering for eligible investors; subscription, redemption and transfer terms follow the fund documentation.
- **Fund page:** [Blockchain Capital]({BLOCKCHAIN_CAPITAL_FUND_PAGE_URL}).
""",
    estimated_nav_per_share=None,
    nav_source="redstone_bcap_fundamental",
    denomination="USD",
)

#: COSIMO X fund shares on Ethereum.
#:
#: https://etherscan.io/address/0xc0c61c29ef8beabc694987c93e5fe4af647042e7
COSX_ETHEREUM = SecuritizeProduct(
    chain_id=1,
    token=HexAddress("0xc0c61c29ef8beabc694987c93e5fe4af647042e7"),
    product_name="COSIMO X",
    short_description="Evergreen venture strategy for digital-asset businesses",
    description="Tokenised interests in COSIMO digital's evergreen venture fund for digital-asset businesses.",
    manager_name="COSIMO digital",
    curator_slug="cosimo-digital",
    homepage=COSIMO_X_FUND_PAGE_URL,
    notes=f"""COSIMO X (COSX).

- **Curator:** COSIMO digital / Securitize.
- **Vault strategy:** Tokenised interests in COSIMO X, an evergreen venture fund that invests in digital-asset businesses.
- **NAV reporting:** The value follows the fund's net asset value and changes with the underlying venture portfolio. This integration does not use a fixed share-price estimate.
- **Investor access:** COSX is a permissioned Securitize offering for eligible investors; subscription, redemption and transfer terms follow the fund documentation.
- **Fund page:** [COSIMO X]({COSIMO_X_FUND_PAGE_URL}).
""",
    estimated_nav_per_share=None,
    nav_source="unconfigured",
    denomination="USD",
)

#: Science Blockchain fund shares on Ethereum.
#:
#: https://etherscan.io/address/0x682ef9cc637ef56577092b29ae9275a629aae7db
SCI2_ETHEREUM = SecuritizeProduct(
    chain_id=1,
    token=HexAddress("0x682ef9cc637ef56577092b29ae9275a629aae7db"),
    product_name="Science Blockchain",
    short_description="Early-stage venture strategy for blockchain companies",
    description="Tokenised interests in Science Inc.'s investment vehicle for early-stage blockchain companies.",
    manager_name="Science Inc.",
    curator_slug="science-inc",
    homepage=SCIENCE_BLOCKCHAIN_FUND_PAGE_URL,
    notes=f"""Science Blockchain (SCI2).

- **Curator:** Science Inc. / Securitize.
- **Vault strategy:** Tokenised interests in Science Blockchain, an investment vehicle that works with early-stage blockchain companies.
- **NAV reporting:** The value follows the net asset value of the underlying portfolio and can change as the portfolio is valued. This integration does not use a fixed share-price estimate.
- **Investor access:** SCI2 is a permissioned Securitize offering for eligible investors; subscription, redemption and transfer terms follow the fund documentation.
- **Fund page:** [Science Blockchain]({SCIENCE_BLOCKCHAIN_FUND_PAGE_URL}).
""",
    estimated_nav_per_share=None,
    nav_source="unconfigured",
    denomination="USD",
)

#: Protos Cryptocurrency Fund shares on Ethereum.
#:
#: https://etherscan.io/address/0x5e17f6f450dcb0bc69b232ea554e224d7e88067a
PRTS_ETHEREUM = SecuritizeProduct(
    chain_id=1,
    token=HexAddress("0x5e17f6f450dcb0bc69b232ea554e224d7e88067a"),
    product_name="Protos Cryptocurrency Fund",
    short_description="Actively managed digital-asset strategy",
    description="Tokenised fund interests in Protos Asset Management's actively managed digital-asset fund.",
    manager_name="Protos Asset Management",
    curator_slug="protos-asset-management",
    homepage=PROTOS_FUND_PAGE_URL,
    notes=f"""Protos Cryptocurrency Fund (PRTS).

- **Curator:** Protos Asset Management / Securitize.
- **Vault strategy:** Tokenised interests in an actively managed fund investing in digital assets and related instruments.
- **NAV reporting:** The value follows the fund's net asset value and changes with its investment portfolio. This integration does not use a fixed share-price estimate.
- **Investor access:** PRTS is a permissioned Securitize offering for eligible investors; subscription, redemption and transfer terms follow the fund documentation.
- **Fund page:** [Protos Asset Management NAV announcement]({PROTOS_FUND_PAGE_URL}).
""",
    estimated_nav_per_share=None,
    nav_source="unconfigured",
    denomination="USD",
)

#: Mantle Index Four fund shares on Mantle.
#:
#: https://explorer.mantle.xyz/address/0x671642ac281c760e34251d51bc9eef27026f3b7a
MI4_MANTLE = SecuritizeProduct(
    chain_id=5_000,
    token=HexAddress("0x671642ac281c760e34251d51bc9eef27026f3b7a"),
    product_name="Mantle Index Four",
    short_description="Diversified major-digital-asset and staking strategy",
    description="Tokenised fund providing managed exposure to a diversified basket of major digital assets and staking strategies.",
    manager_name="Mantle Guard",
    curator_slug="mantle-guard",
    homepage=MI4_FUND_PAGE_URL,
    notes=f"""Mantle Index Four (MI4).

- **Curator:** Mantle Guard / Securitize.
- **Vault strategy:** Tokenised fund exposure to a diversified basket of BTC, ETH, SOL and U.S. dollar assets, with selected staking strategies and periodic rebalancing.
- **NAV reporting:** The share value follows the fund's portfolio and is not stable. Historical NAV is read from RedStone's Mantle MI4 fundamental feed.
- **Investor access:** MI4 is a permissioned Securitize offering on Mantle for qualifying investors; subscriptions, redemptions and transfers follow the fund terms.
- **Fund page:** [Mantle Index Four]({MI4_FUND_PAGE_URL}).
""",
    estimated_nav_per_share=None,
    nav_source="redstone_mi4_mantle_fundamental",
    denomination="USD",
)

#: Tokenised ARK Venture Fund shares on Ethereum.
#:
#: Fees follow the Class D table in the 2025-10-28 prospectus. The 2% deposit
#: fee is the tokenised-route subscription fee reported by the press and
#: confirmed by onchain settlement prices; see
#: :py:mod:`eth_defi.tokenised_fund.securitize.settlement`.
#:
#: https://etherscan.io/token/0xdf1c8e71cbdf48af50b36f96ad2eb6f5094ba72a
ARKVX_ETHEREUM = SecuritizeProduct(
    chain_id=1,
    token=HexAddress("0xdf1c8e71cbdf48af50b36f96ad2eb6f5094ba72a"),
    product_name="ARK Venture Fund",
    short_description="Interval fund investing in private and public disruptive-innovation companies, with daily NAV and quarterly repurchase offers expected at 5% of shares",
    description=("Tokenised shares of ARK Venture Fund (ARKVX), a registered closed-end interval fund managed by ARK Investment Management LLC that seeks long-term growth of capital by investing 20% to 90% of its assets in private companies and the remainder in public companies linked to disruptive innovation. NAV is struck every business day, and the adviser fair-values the private holdings. The fund's Class D shares carry a 2.75% management fee and 2.90% net annual expenses under a Board-terminable expense cap; the tokenised class shares the management fee but may have its own other expenses. Liquidity is limited to quarterly repurchase offers of 5% to 25% of shares, expected to be 5%."),
    manager_name="ARK Invest",
    curator_slug="ark-invest",
    homepage=ARKVX_FUND_PAGE_URL,
    notes=f"""ARK Venture Fund (ARKVX), tokenised on Ethereum through Securitize.

- **Curator:** ARK Investment Management LLC (ARK Invest) manages the fund. Securitize Markets, LLC, an SEC-registered broker-dealer, distributes the tokenised shares, and Securitize provides the tokenisation and investor-onboarding infrastructure. The Bank of New York Mellon is the fund's custodian.
- **Vault strategy:** The fund's objective is "to seek long-term growth of capital". It invests 20% to 90% of its assets in private companies and the remainder in public companies aligned with disruptive innovation, such as artificial intelligence, space, robotics, energy storage, fintech and genomics. On 2026-08-31 it held 74.15% in private companies and had USD 1.30 billion of net assets across all share classes. Its largest positions were SpaceX (7.54%), Kalshi (5.81%), Ayar Labs (5.65%), OpenAI (5.26%), Stripe (4.16%) and Anthropic (3.86%).
- **Prospectus and regulatory status:** ARKVX is a closed-end interval fund registered under the Investment Company Act of 1940 (file 811-23778). Its current [prospectus and statement of additional information]({ARKVX_PROSPECTUS_URL}) are dated 2025-10-28. On 2026-09-21 the SEC granted [amended exemptive relief]({ARKVX_SEC_ORDER_URL}) (Release No. 36333) that permits a tokenised share class. The press describes the tokens as Class D shares held one-to-one at BNY Mellon, whereas the SEC application describes a separate Tokenized Class that is not yet registered in a prospectus amendment. Read the prospectus before investing.
- **Fees:** The prospectus fee table for Class D shows a 2.75% management fee, 0.04% interest on borrowed funds, 0.69% other expenses (including a 0.15% distribution and servicing fee) and 0.01% acquired fund fees: 3.49% gross annual expenses. ARK waives or reimburses 0.59%, giving **2.90% net annual expenses for Class D**. The expense cap stays in place until the fund's Board approves its termination, and ARK cannot recoup waived amounts. According to the SEC application, the tokenised class has the same management fee and no sales load, but may bear its own other expenses, including blockchain gas costs, so its total expense ratio may differ. There is no performance fee or early repurchase fee. Press coverage of the Securitize route reports a **2% subscription fee**, deducted before units are priced at NAV; it is not in the SEC filings, but onchain settlement prices are consistent with it.
- **Fund value promise:** The fund does not promise a stable or guaranteed share value. NAV is calculated each business day as of the NYSE close, and ARK, as the Rule 2a-5 valuation designee, fair-values the private holdings. NAV can therefore differ from the price at which those holdings could be sold, and can move sharply when private companies are revalued. The prospectus states that investors "should invest in the Fund only if they can sustain a complete loss". Shares are not bank deposits and are not FDIC-insured or bank-guaranteed.
- **Liquidity and redemptions:** The fund's shares are not listed on an exchange, and the prospectus says no secondary market is expected. The SEC relief permits tokenised shares to trade on alternative trading systems, but ARK's tokenisation announcement still states that no secondary market is expected to develop. The fund's main liquidity route is its quarterly Rule 23c-3 repurchase offers, made in March, June, September and December, for 5% to 25% of outstanding shares at NAV; the fund expects to offer 5%. When tenders exceed the offer, repurchases are prorated, so a holder may not be able to sell all of their shares in a given quarter. The current offer's deadline is 2026-09-30 ([notice]({ARKVX_REPURCHASE_OFFER_URL})).
- **Distributions:** The fund intends to make annual distributions, reinvested in shares unless the holder opts out. How the tokenised class receives distributions is not yet documented.
- **Token structure and eligibility:** ARKVX tokens are Securitize DSTokens. Only investors who have passed identity and eligibility checks can subscribe, redeem or receive transfers, and only into whitelisted wallets. Subscriptions are paid in USDC through Securitize's ERC-7540-style subscription vault. They are batched into generations that settle after NAV is struck; the first settlements came one to two business days apart. The prospectus sets a USD 500 minimum investment for Class D, and press coverage reports the same minimum for the tokenised route.
- **Price data in this listing:** The share price is rebuilt from onchain deposit settlement events. Each settlement records a USDC price that includes the 2% subscription fee, so NAV/share is the settlement price multiplied by 0.98, rounded to cents. This matches ARK's published NAV for the business day before each settlement. The price updates only when deposits settle, so it lags the fund's own NAV by at least one business day and stays at the last settled value until deposits settle again. TVL covers onchain tokenised shares only, not the whole fund.
- **Fund page:** [ARK Venture Fund]({ARKVX_FUND_PAGE_URL}).
""",
    estimated_nav_per_share=None,
    nav_source="settlement_arkvx_deposit_generation",
    denomination="USD",
    fee_data=FeeData(
        # Fund expenses accrue daily in NAV, so the share price is net of them.
        fee_mode=VaultFeeMode.internalised_skimming,
        management=0.0275,
        performance=0.0,
        # Subscription fee deducted before pricing, outside the share price.
        deposit=0.02,
        withdraw=0.0,
    ),
)

#: Supported Securitize investment funds keyed by chain and DSToken address.
SECURITIZE_PRODUCTS: dict[tuple[int, HexAddress], SecuritizeProduct] = {
    (product.chain_id, product.token): product
    for product in (
        BUIDL_ETHEREUM,
        BUIDL_POLYGON,
        BUIDL_AVALANCHE,
        BUIDL_OPTIMISM,
        BUIDL_ARBITRUM,
        BUIDL_I_ETHEREUM,
        ACRED_ETHEREUM,
        VBILL_ETHEREUM,
        STAC_ETHEREUM,
        ARCOIN_ETHEREUM,
        SPICE_VC_ETHEREUM,
        HLSCOPE_ETHEREUM,
        BCAP_ETHEREUM,
        COSX_ETHEREUM,
        SCI2_ETHEREUM,
        PRTS_ETHEREUM,
        MI4_MANTLE,
        ARKVX_ETHEREUM,
    )
}

#: Per-vault notes for the shared vault metadata layer.
SECURITIZE_PRODUCT_NOTES: dict[str, str] = {product.token: product.notes for product in SECURITIZE_PRODUCTS.values()}

#: Tokenised-fund vault addresses for descriptive vault flags.
SECURITIZE_TOKENISED_FUND_ADDRESSES: set[str] = set(SECURITIZE_PRODUCT_NOTES)
