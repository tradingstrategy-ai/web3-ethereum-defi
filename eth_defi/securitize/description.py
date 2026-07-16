"""Manual descriptions for Securitize DSToken funds.

Securitize DSTokens share a token contract interface, but their investment
strategy, fund manager, NAV source and investor terms belong to the individual
fund. This registry covers the Ethereum products with more than five million
US dollars of on-chain value as of the initial protocol scan.
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
- **NAV reporting:** The fund supports subscriptions and redemptions at daily NAV. Its NAV changes with the underlying credit portfolio, so this integration does not model ACRED at a fixed share price.
- **Investor access:** The product is available to qualifying investors through Securitize Markets.
- **Fund page:** [Apollo Diversified Credit Securitize Fund]({ACRED_FUND_PAGE_URL}).
""",
    estimated_nav_per_share=None,
    nav_source="unconfigured",
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
- **NAV reporting:** The fund seeks to maintain a stable USD 1 net asset value and has daily NAV calculations. A canonical historical price source has not yet been integrated, so this adapter does not assume a fixed share price.
- **Investor access:** The fund is designed for institutional and qualified investors.
- **Fund page:** [VanEck Treasury Fund]({VBILL_FUND_PAGE_URL}).
""",
    estimated_nav_per_share=None,
    nav_source="unconfigured",
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
- **NAV reporting:** The share value follows the NAV of the CLO portfolio and is not a stable-dollar fund. A canonical historical price source has not yet been integrated.
- **Fund page:** [Securitize Tokenized AAA CLO Fund]({STAC_FUND_PAGE_URL}).
""",
    estimated_nav_per_share=None,
    nav_source="unconfigured",
    denomination="USD",
)

#: Supported high-value Securitize funds keyed by chain and DSToken address.
SECURITIZE_PRODUCTS: dict[tuple[int, HexAddress], SecuritizeProduct] = {
    (product.chain_id, product.token): product
    for product in (
        BUIDL_ETHEREUM,
        BUIDL_I_ETHEREUM,
        ACRED_ETHEREUM,
        VBILL_ETHEREUM,
        STAC_ETHEREUM,
    )
}

#: Per-vault notes for the shared vault metadata layer.
SECURITIZE_PRODUCT_NOTES: dict[str, str] = {product.token: product.notes for product in SECURITIZE_PRODUCTS.values()}

#: Tokenised-fund vault addresses for descriptive vault flags.
SECURITIZE_TOKENISED_FUND_ADDRESSES: set[str] = set(SECURITIZE_PRODUCT_NOTES)
