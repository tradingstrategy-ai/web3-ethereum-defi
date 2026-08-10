"""Verified Spiko tokenised-fund deployment constants."""

import datetime
from dataclasses import dataclass

from eth_typing import HexAddress

from eth_defi.types import Percent


@dataclass(slots=True, frozen=True)
class SpikoProduct:
    """One reviewed Spiko permissioned fund-share deployment.

    :param chain_id:
        EVM chain hosting the token and issuer NAV oracle.
    :param token:
        ERC-20 proxy address representing shares in the fund.
    :param price_oracle:
        Spiko ``Oracle`` proxy implementing the Chainlink AggregatorV3 view
        interface for the fund's NAV/share.
    :param first_seen_at_block:
        Token proxy deployment block.
    :param first_seen_at:
        Token proxy deployment timestamp as a naive UTC datetime.
    :param oracle_first_seen_at_block:
        First block containing the product's official NAV oracle.
    :param symbol:
        Onchain ERC-20 symbol.
    :param denomination_symbol:
        Currency in which Spiko publishes NAV/share.
    :param description:
        Public investment-strategy description.
    :param short_description:
        Concise public product description.
    :param management_fee:
        Published annual management fee as a fraction.
    :param product_url:
        Official Spiko product page.
    :param usd_price_oracle:
        Optional Chainlink-compatible FX oracle returning USD per issuer NAV
        currency unit. Non-USD products use this to publish USD-normalised
        history to the shared vault feed.
    """

    chain_id: int
    token: HexAddress
    price_oracle: HexAddress
    first_seen_at_block: int
    first_seen_at: datetime.datetime
    oracle_first_seen_at_block: int
    symbol: str
    denomination_symbol: str
    description: str
    short_description: str
    management_fee: Percent
    product_url: str
    usd_price_oracle: HexAddress | None = None

    @property
    def nav_source(self) -> str:
        """Return the stable identifier for the issuer NAV source.

        :return:
            Product-specific Chainlink-compatible oracle source label.
        """

        return f"spiko_{self.symbol.lower()}_oracle_latestRoundData"


#: Ethereum mainnet chain id.
SPIKO_ETHEREUM_CHAIN_ID = 1

#: Arbitrum One chain id.
SPIKO_ARBITRUM_CHAIN_ID = 42161

#: Spiko US T-Bills Money Market Fund ERC-20 proxy on Ethereum.
#:
#: https://etherscan.io/address/0xe4880249745eac5f1ed9d8f7df844792d560e750
USTBL_PRODUCT = SpikoProduct(
    chain_id=SPIKO_ETHEREUM_CHAIN_ID,
    token=HexAddress("0xe4880249745eac5f1ed9d8f7df844792d560e750"),
    price_oracle=HexAddress("0x021289588cd81dc1ac87ea91e91607eef68303f5"),
    first_seen_at_block=19_690_265,
    first_seen_at=datetime.datetime(2024, 4, 19, 15, 6, 11, tzinfo=datetime.UTC).replace(tzinfo=None),
    oracle_first_seen_at_block=19_690_267,
    symbol="USTBL",
    denomination_symbol="USD",
    description="Tokenised share in Spiko's U.S. Treasury-bill money-market fund",
    short_description="U.S. Treasury-bill money-market strategy",
    management_fee=0.0025,
    product_url="https://www.spiko.io/spiko-treasury-bills-dollar",
)

#: Spiko EU T-Bills Money Market Fund ERC-20 proxy on Arbitrum One.
#:
#: Token and oracle deployment data from Spiko's canonical configuration:
#: https://github.com/spiko-tech/contracts/blob/main/subgraph/config/arbitrum-one.json
EUTBL_PRODUCT = SpikoProduct(
    chain_id=SPIKO_ARBITRUM_CHAIN_ID,
    token=HexAddress("0xcbeb19549054cc0a6257a77736fc78c367216ce7"),
    price_oracle=HexAddress("0xe4880249745eac5f1ed9d8f7df844792d560e750"),
    first_seen_at_block=267_473_436,
    first_seen_at=datetime.datetime(2024, 10, 25, 10, 12, 40, tzinfo=datetime.UTC).replace(tzinfo=None),
    oracle_first_seen_at_block=267_473_497,
    symbol="EUTBL",
    denomination_symbol="EUR",
    description="Tokenised share in Spiko's Eurozone Treasury-bill money-market fund",
    short_description="Eurozone Treasury-bill money-market strategy",
    management_fee=0.0025,
    product_url="https://www.spiko.io/spiko-treasury-bills-euro",
    # Chainlink EUR / USD on Arbitrum. The oracle is already available at the
    # EUTBL deployment block and publishes 8 decimal USD-per-EUR answers.
    # https://data.chain.link/feeds/arbitrum/mainnet/eur-usd
    usd_price_oracle=HexAddress("0xa14d53bc1f1c0f31b4aa3bd109344e5009051a84"),
)

#: Reviewed Spiko products keyed by chain and ERC-20 token address.
SPIKO_PRODUCTS: dict[tuple[int, HexAddress], SpikoProduct] = {(product.chain_id, product.token): product for product in (USTBL_PRODUCT, EUTBL_PRODUCT)}

#: Reviewed Spiko products keyed by their unique public symbol.
SPIKO_PRODUCTS_BY_SYMBOL: dict[str, SpikoProduct] = {product.symbol: product for product in SPIKO_PRODUCTS.values()}

#: Hardcoded discovery leads for non-ERC-4626 Spiko tokenised funds.
SPIKO_HARDCODED_LEADS = tuple((product.chain_id, product.token, product.first_seen_at_block, product.first_seen_at) for product in SPIKO_PRODUCTS.values())

#: Backwards-compatible aliases for the initial USTBL integration.
SPIKO_CHAIN_ID = USTBL_PRODUCT.chain_id
USTBL_TOKEN_ADDRESS = USTBL_PRODUCT.token
USTBL_PRICE_ORACLE_ADDRESS = USTBL_PRODUCT.price_oracle
USTBL_FIRST_SEEN_AT_BLOCK = USTBL_PRODUCT.first_seen_at_block
USTBL_FIRST_SEEN_AT = USTBL_PRODUCT.first_seen_at
USTBL_ORACLE_FIRST_SEEN_AT_BLOCK = USTBL_PRODUCT.oracle_first_seen_at_block
USTBL_NAV_SOURCE = USTBL_PRODUCT.nav_source
USTBL_MANAGEMENT_FEE = USTBL_PRODUCT.management_fee

#: EUTBL convenience aliases used by callers that target the Arbitrum product.
EUTBL_TOKEN_ADDRESS = EUTBL_PRODUCT.token
EUTBL_PRICE_ORACLE_ADDRESS = EUTBL_PRODUCT.price_oracle
EUTBL_FIRST_SEEN_AT_BLOCK = EUTBL_PRODUCT.first_seen_at_block
EUTBL_FIRST_SEEN_AT = EUTBL_PRODUCT.first_seen_at
EUTBL_ORACLE_FIRST_SEEN_AT_BLOCK = EUTBL_PRODUCT.oracle_first_seen_at_block
EUTBL_NAV_SOURCE = EUTBL_PRODUCT.nav_source
EUTBL_MANAGEMENT_FEE = EUTBL_PRODUCT.management_fee
