"""Reviewed Ethereum deployments for Ondo tokenised funds."""

import datetime
from dataclasses import dataclass

from eth_typing import HexAddress

from eth_defi.types import Percent


@dataclass(slots=True, frozen=True)
class OndoProduct:
    """Metadata needed to read an Ondo share token and its issuer NAV oracle."""

    chain_id: int
    token: HexAddress
    symbol: str
    product_name: str
    oracle: HexAddress
    oracle_method: str
    oracle_first_seen_at_block: int
    first_seen_at_block: int
    first_seen_at: datetime.datetime
    homepage: str
    description: str
    notes: str
    management_fee: Percent | None = None


ETHEREUM_CHAIN_ID = 1

#: USDY token deployment and redemption-price oracle.
#:
#: Sources: https://docs.ondo.finance/addresses and
#: https://docs.ondo.finance/general-access-products/usdy/basics
ONDO_USDY_ETHEREUM = OndoProduct(
    chain_id=ETHEREUM_CHAIN_ID,
    token=HexAddress("0x96f6ef951840721adbf46ac996b59e0235cb985c"),
    symbol="USDY",
    product_name="Ondo U.S. Dollar Yield",
    oracle=HexAddress("0xa0219aa5b31e65bc920b5b6dfb8edf0988121de0"),
    oracle_method="getPrice",
    oracle_first_seen_at_block=18_485_028,
    first_seen_at_block=17_672_244,
    first_seen_at=datetime.datetime(2023, 7, 11, 18, 46, 23, tzinfo=datetime.UTC).replace(tzinfo=None),
    homepage="https://docs.ondo.finance/general-access-products/usdy/basics",
    description="Permissioned tokenised note whose redemption price accumulates U.S. dollar yield.",
    notes="""Ondo U.S. Dollar Yield (USDY).

- **Curator:** Ondo Finance.
- **Vault strategy:** A permissioned tokenised note backed, depending on issuance date, by short-term U.S. Treasuries, short-term Treasury ETF shares or bank demand deposits.
- **NAV reporting:** USDY is an accumulating token: its redemption price increases as yield accrues. Historical NAV is read from Ondo's published on-chain Redemption Price Oracle.
- **Investor access:** USDY is available to qualifying non-U.S. individual and institutional investors. Transfers, subscriptions and redemptions require issuer onboarding and compliance checks.
- **Fund page:** [Ondo USDY](https://docs.ondo.finance/general-access-products/usdy/basics).
""",
)

#: OUSG token deployment and the current issuer-wide price oracle.
#:
#: Sources: https://docs.ondo.finance/addresses and
#: https://docs.ondo.finance/qualified-access-products/ousg/overview
ONDO_OUSG_ETHEREUM = OndoProduct(
    chain_id=ETHEREUM_CHAIN_ID,
    token=HexAddress("0x1b19c19393e2d034d8ff31ff34c81252fcbbee92"),
    symbol="OUSG",
    product_name="Ondo Short-Term U.S. Government Bond Fund",
    oracle=HexAddress("0x9cad45a8bf0ed41ff33074449b357c7a1fab4094"),
    oracle_method="getAssetPrice",
    oracle_first_seen_at_block=22_141_383,
    first_seen_at_block=16_234_210,
    first_seen_at=datetime.datetime(2022, 12, 21, 16, 16, 23, tzinfo=datetime.UTC).replace(tzinfo=None),
    homepage="https://docs.ondo.finance/qualified-access-products/ousg/overview",
    description="Permissioned tokenised fund share providing short-term U.S. government-securities exposure.",
    notes="""Ondo Short-Term U.S. Government Bond Fund (OUSG).

- **Curator:** Ondo Finance.
- **Vault strategy:** Tokenised shares providing exposure primarily to short-term U.S. Treasuries and government-sponsored-enterprise securities, alongside cash-management holdings.
- **NAV reporting:** Ondo updates the fund NAV at the end of each business day and publishes the NAV per OUSG token through its on-chain price oracle.
- **Investor access:** OUSG is a qualified-access product. Eligible, onboarded investors may use Ondo's supported subscription and redemption process; token transfers remain restricted to onboarded holders.
- **Fund page:** [Ondo OUSG](https://docs.ondo.finance/qualified-access-products/ousg/overview).
""",
    management_fee=0.0015,
)

ONDO_PRODUCTS: dict[tuple[int, HexAddress], OndoProduct] = {(product.chain_id, product.token): product for product in (ONDO_USDY_ETHEREUM, ONDO_OUSG_ETHEREUM)}

ONDO_PRODUCTS_BY_TOKEN: dict[HexAddress, OndoProduct] = {product.token: product for product in ONDO_PRODUCTS.values()}

ONDO_HARDCODED_LEADS = tuple((product.chain_id, product.token, product.first_seen_at_block, product.first_seen_at) for product in ONDO_PRODUCTS.values())

ONDO_PRODUCT_NOTES: dict[str, str] = {product.token: product.notes for product in ONDO_PRODUCTS.values()}
ONDO_TOKENISED_FUND_ADDRESSES: set[str] = set(ONDO_PRODUCT_NOTES)
