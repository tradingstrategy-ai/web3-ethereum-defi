"""Read-only adapter for KAIO's permissioned CASHx fund-share token."""

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.tokenised_fund.kaio.constants import CASHX_ETHEREUM
from eth_defi.tokenised_fund.supply_only import SupplyOnlyTokenisedFundVault


class KaioVault(SupplyOnlyTokenisedFundVault):
    """Expose CASHx identity and supply without inventing a public NAV."""

    product = CASHX_ETHEREUM
    feature = ERC4626Feature.kaio_like
    protocol_name = "KAIO"
    curator = "blackrock"
    manager = "BlackRock"
    homepage = "https://www.kaio.xyz/"
    restricted_flow_reason = "CASHx transfers, issuance and redemption require KAIO-approved investors and book-controlled settlement"
    # TODO: CASHx exposes ERC-20 supply, but KAIO has not published a verified
    # on-chain NAV oracle or historical issuer NAV endpoint for this share class.
    # Do not infer one-dollar NAV or TVL from the token supply.
    nav_unavailable_reason = "CASHx has no verified public on-chain or historical NAV/share interface"
