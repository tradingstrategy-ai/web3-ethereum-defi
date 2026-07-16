"""Manual descriptions for Securitize DSToken funds.

Securitize DSTokens share a token contract interface, but their investment
strategy, fund manager, NAV source and investor terms belong to the individual
fund. This registry covers selected Ethereum investment funds identified in
the DSToken scan; it intentionally excludes company securities and test tokens.
"""

from dataclasses import dataclass
from decimal import Decimal

from eth_typing import HexAddress


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


BUIDL_FUND_PAGE_URL = "https://www.blackrock.com/us/individual/products/buidl/"
BUIDL_I_FUND_PAGE_URL = "https://www.blackrock.com/corporate/compliance/scams-and-fraud/resources"
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


#: BlackRock USD Institutional Digital Liquidity Fund on Ethereum.
#:
#: https://etherscan.io/address/0x7712c34205737192402172409a8f7ccef8aa2aec
BUIDL_ETHEREUM = SecuritizeProduct(
    chain_id=1,
    token=HexAddress("0x7712c34205737192402172409a8f7ccef8aa2aec"),
    product_name="BlackRock USD Institutional Digital Liquidity Fund",
    short_description="Tokenised U.S. dollar liquidity fund",
    description="Tokenised fund investing in cash, U.S. Treasury bills and repurchase agreements.",
    manager_name="BlackRock",
    curator_slug="blackrock",
    homepage=BUIDL_FUND_PAGE_URL,
    notes=f"""BlackRock USD Institutional Digital Liquidity Fund (BUIDL).

- **Curator:** BlackRock / Securitize.
- **Vault strategy:** Tokenised shares in a fund that invests in cash, U.S. Treasury bills and repurchase agreements.
- **Token structure:** BUIDL is a permissioned Securitize DSToken. Investors must complete issuer eligibility and compliance checks before subscribing, redeeming or transferring shares.
- **Stable dollar share value:** BUIDL targets a USD 1 share value. Fund income accrues daily and is distributed monthly as newly issued BUIDL shares to eligible holders, rather than increasing the unit price. The token is therefore modelled at an estimated USD 1 per share and the on-chain share price does not represent total return.
- **Fund page:** [BlackRock BUIDL]({BUIDL_FUND_PAGE_URL}).
""",
    estimated_nav_per_share=Decimal("1"),
    nav_source="estimated_buidl_usd_1",
    denomination="USD",
)

#: BlackRock BUIDL I Class on Ethereum.
#:
#: https://etherscan.io/address/0x6a9da2d710bb9b700acde7cb81f10f1ff8c89041
BUIDL_I_ETHEREUM = SecuritizeProduct(
    chain_id=1,
    token=HexAddress("0x6a9da2d710bb9b700acde7cb81f10f1ff8c89041"),
    product_name="BlackRock USD Institutional Digital Liquidity Fund - I Class",
    short_description="Tokenised U.S. dollar liquidity fund share class",
    description="I Class tokenised shares in BlackRock's U.S. dollar liquidity fund.",
    manager_name="BlackRock",
    curator_slug="blackrock",
    homepage=BUIDL_I_FUND_PAGE_URL,
    notes=f"""BlackRock USD Institutional Digital Liquidity Fund - I Class (BUIDL-I).

- **Curator:** BlackRock / Securitize.
- **Vault strategy:** I Class tokenised shares in the same BlackRock liquidity-fund product family as BUIDL, investing in cash, U.S. Treasury bills and repurchase agreements.
- **Token structure:** BUIDL-I is a permissioned Securitize DSToken. Investors must complete issuer eligibility and compliance checks before subscribing, redeeming or transferring shares.
- **Stable dollar share value:** BUIDL-I targets a USD 1 share value. Fund income accrues daily and is distributed monthly as newly issued shares, rather than increasing the unit price. The token is therefore modelled at an estimated USD 1 per share and the on-chain share price does not represent total return.
- **Official address list:** [BlackRock BUIDL addresses]({BUIDL_I_FUND_PAGE_URL}).
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
    short_description="Tokenised private-credit feeder fund",
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
    short_description="Tokenised short-term U.S. Treasury fund",
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
    short_description="Tokenised AAA-rated CLO fund",
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
    short_description="Tokenised U.S. Treasury fund shares",
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
    short_description="Tokenised venture-capital fund focused on tokenisation",
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
    short_description="Tokenised feeder fund for senior private credit",
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
    short_description="Tokenised venture-capital fund for blockchain companies",
    description="Tokenised fund interests in Blockchain Capital's digital liquid venture fund.",
    manager_name="Blockchain Capital",
    curator_slug="blockchain-capital",
    homepage=BLOCKCHAIN_CAPITAL_FUND_PAGE_URL,
    notes=f"""Blockchain Capital III Digital Liquid Venture Fund (BCAP).

- **Curator:** Blockchain Capital / Securitize.
- **Vault strategy:** Tokenised fund interests in a venture-capital fund investing in companies building blockchain and cryptocurrency products.
- **NAV reporting:** The value follows the fund's net asset value and depends on its venture portfolio. This integration does not use a fixed share-price estimate.
- **Investor access:** BCAP is a permissioned Securitize offering for eligible investors; subscription, redemption and transfer terms follow the fund documentation.
- **Fund page:** [Blockchain Capital]({BLOCKCHAIN_CAPITAL_FUND_PAGE_URL}).
""",
    estimated_nav_per_share=None,
    nav_source="unconfigured",
    denomination="USD",
)

#: COSIMO X fund shares on Ethereum.
#:
#: https://etherscan.io/address/0xc0c61c29ef8beabc694987c93e5fe4af647042e7
COSX_ETHEREUM = SecuritizeProduct(
    chain_id=1,
    token=HexAddress("0xc0c61c29ef8beabc694987c93e5fe4af647042e7"),
    product_name="COSIMO X",
    short_description="Tokenised venture fund for digital-asset businesses",
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
    short_description="Tokenised fund for early-stage blockchain companies",
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
    short_description="Tokenised interests in an actively managed digital-asset fund",
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

#: Supported Securitize investment funds keyed by chain and DSToken address.
SECURITIZE_PRODUCTS: dict[tuple[int, HexAddress], SecuritizeProduct] = {
    (product.chain_id, product.token): product
    for product in (
        BUIDL_ETHEREUM,
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
    )
}

#: Per-vault notes for the shared vault metadata layer.
SECURITIZE_PRODUCT_NOTES: dict[str, str] = {product.token: product.notes for product in SECURITIZE_PRODUCTS.values()}

#: Tokenised-fund vault addresses for descriptive vault flags.
SECURITIZE_TOKENISED_FUND_ADDRESSES: set[str] = set(SECURITIZE_PRODUCT_NOTES)
