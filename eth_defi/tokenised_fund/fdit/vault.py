"""Read-only adapter for Fidelity's permissioned FDIT fund-share token."""

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.tokenised_fund.fdit.constants import FDIT_ETHEREUM
from eth_defi.tokenised_fund.supply_only import SupplyOnlyTokenisedFundVault


class FditVault(SupplyOnlyTokenisedFundVault):
    """Expose FDIT identity and supply without fabricating a token NAV."""

    product = FDIT_ETHEREUM
    feature = ERC4626Feature.fdit_like
    protocol_name = "Fidelity FDIT"
    curator = "fidelity-investments"
    homepage = "https://institutional.fidelity.com/app/funds-and-products/9053/fidelity-treasury-digital-fund-onchain-class-fyoxx.html"
    restricted_flow_reason = "FDIT transfers, issuance and redemption are controlled by Fidelity and DTCC compliance workflows"
    nav_unavailable_reason = "FDIT has no verified public on-chain or historical NAV/share interface"
