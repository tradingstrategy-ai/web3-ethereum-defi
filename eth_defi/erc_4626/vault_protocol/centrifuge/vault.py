"""Centrifuge liquidity pool vault support.

Centrifuge is a protocol for real-world asset (RWA) tokenisation and financing.
Each pool can have multiple tranches, and each tranche is a separate deployment
of an ERC-7540 Vault and a Tranche Token. Additionally, each tranche of a
Centrifuge pool can have multiple Liquidity Pools (vaults) - one for each
supported investment currency.

- Homepage: https://centrifuge.io/
- Documentation: https://docs.centrifuge.io/
- Developer docs: https://developer.centrifuge.io/
- Github: https://github.com/centrifuge/liquidity-pools
- Example vault on Etherscan: https://etherscan.io/address/0xa702ac7953e6a66d2b10a478eb2f0e2b8c8fd23e
"""

import datetime
import logging
from dataclasses import dataclass
from typing import Final

from eth_typing import BlockIdentifier, HexAddress

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.centrifuge.centrifuge_utils import fetch_pool_id, fetch_tranche_id
from eth_defi.erc_4626.vault_protocol.centrifuge.tags import STRATEGY_TAGS
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class CentrifugeVaultDescription:
    """Address-scoped display metadata for a Centrifuge vault.

    Centrifuge's onchain vault interface does not expose product strategy
    descriptions. Keep reviewed copy keyed by vault address because pools can
    share an asset class while having different investor and transfer models.

    :param short_description:
        Listing-friendly product summary.
    :param description:
        Longer Markdown product description with authoritative sources.
    """

    #: Listing-friendly product summary.
    short_description: str

    #: Longer Markdown product description with authoritative sources.
    description: str


#: Janus Henderson Anemoy S&P500 Fund USDC vault on Base.
#:
#: https://docs.centrifuge.io/developer/protocol/deployments/
SPXA_BASE_VAULT_ADDRESS = HexAddress("0x99e9092bae6d4394e54034ecb1e45441678323b9")

#: DeFi Janus Henderson Anemoy S&P500 Fund Token USDC vault on Base.
#:
#: https://docs.centrifuge.io/developer/protocol/deployments/
DESPXA_BASE_VAULT_ADDRESS = HexAddress("0x2da40f061536c2f3a8f95f23a5f4c133d07d393a")

#: Official Anemoy product page for the SPXA fund share class.
SPXA_FUND_PAGE_URL: Final[str] = "https://www.anemoy.io/funds/spxa"

#: Official Centrifuge launch announcement for deSPXA.
DESPXA_ANNOUNCEMENT_URL: Final[str] = "https://centrifuge.io/blog/despxa-on-base"

#: Human-readable product copy for the Base SPXA and deSPXA vaults.
#:
#: These products have related S&P 500 exposure but are separate Centrifuge
#: pools and share tokens. Keep the relationship explicit so downstream UIs do
#: not present them as duplicate vault contracts or aggregate their NAV as
#: independent fund assets.
CENTRIFUGE_VAULT_DESCRIPTION_OVERLAY: Final[dict[HexAddress, CentrifugeVaultDescription]] = {
    SPXA_BASE_VAULT_ADDRESS: CentrifugeVaultDescription(
        short_description="Tokenised S&P 500 index fund share class for professional investors.",
        description="\n\n".join(
            (
                f"[Janus Henderson Anemoy S&P500® Fund (SPXA)]({SPXA_FUND_PAGE_URL}) is a tokenised share class in an open-ended fund designed to track the S&P 500® Index. The fund is managed by Anemoy with Janus Henderson as sub-investment manager.",
                f"**Relationship to deSPXA:** [deSPXA]({DESPXA_ANNOUNCEMENT_URL}) is a separate, freely transferable DeFi debt instrument whose NAV is linked to SPXA's NAV. SPXA is the permissioned fund share class, while deSPXA provides a DeFi distribution layer and is issued through its own Centrifuge pool. Their reported NAVs should not be added together as independent S&P 500 fund assets.",
            )
        ),
    ),
    DESPXA_BASE_VAULT_ADDRESS: CentrifugeVaultDescription(
        short_description="Freely transferable DeFi token with NAV-linked S&P 500 fund exposure.",
        description="\n\n".join(
            (
                f"[deSPXA]({DESPXA_ANNOUNCEMENT_URL}) is a Base-native, freely transferable ERC-20 debt instrument that gives holders exposure linked to the NAV of the Janus Henderson Anemoy S&P500® Fund. Eligible authorised participants mint and redeem it in USDC, while it can be traded and used in compatible DeFi applications.",
                f"**Relationship to SPXA:** [SPXA]({SPXA_FUND_PAGE_URL}) is the permissioned share class of the underlying tokenised fund. deSPXA is a separately issued DeFi instrument linked to that share class's NAV, not a second independent S&P 500 portfolio. It has its own Centrifuge pool and contract, so it is listed separately; do not aggregate its NAV with SPXA as independent assets.",
            )
        ),
    ),
}


class CentrifugeVault(ERC4626Vault):
    """Centrifuge liquidity pool vault.

    Centrifuge is a protocol for real-world asset (RWA) tokenisation and financing.
    Each pool can have multiple tranches, and each tranche is a separate deployment
    of an ERC-7540 Vault and a Tranche Token. Additionally, each tranche of a
    Centrifuge pool can have multiple Liquidity Pools (vaults) - one for each
    supported investment currency.

    Centrifuge vaults implement ERC-7540 (asynchronous deposits/redemptions) on top
    of ERC-4626, enabling integration with the Centrifuge protocol's epoch-based
    investment system.

    This vault covers to detections
    - poolId() + tranceId() + wards(): https://etherscan.io/address/0xa702ac7953e6a66d2b10a478eb2f0e2b8c8fd23e
    - poolId() + wards(): https://etherscan.io/address/0x4880799ee5200fc58da299e965df644fbf46780b#readContract

    - Homepage: https://centrifuge.io/
    - Documentation: https://docs.centrifuge.io/
    - Developer docs: https://developer.centrifuge.io/developer/liquidity-pools/overview/
    - Github: https://github.com/centrifuge/liquidity-pools
    - Example vault on Etherscan: https://etherscan.io/address/0xa702ac7953e6a66d2b10a478eb2f0e2b8c8fd23e
    - Twitter: https://twitter.com/centrifuge
    """

    @property
    def description(self) -> str | None:
        """Return reviewed Markdown product copy for known Centrifuge vaults.

        :return:
            Full product description, or ``None`` when the vault has no
            address-scoped metadata overlay.
        """

        metadata = CENTRIFUGE_VAULT_DESCRIPTION_OVERLAY.get(HexAddress(str(self.vault_address).lower()))
        return metadata.description if metadata else None

    @property
    def short_description(self) -> str | None:
        """Return a concise product summary for known Centrifuge vaults.

        :return:
            Listing-friendly product summary, or ``None`` when the vault has
            no address-scoped metadata overlay.
        """

        metadata = CENTRIFUGE_VAULT_DESCRIPTION_OVERLAY.get(HexAddress(str(self.vault_address).lower()))
        return metadata.short_description if metadata else None

    def get_strategy_tags(self) -> set[StrategyTag] | None:
        """Return the maintained strategy tags for this Centrifuge vault.

        :return:
            Copy of the tag set, or ``None`` when this vault has not yet been
            classified.
        """
        return lookup_strategy_tags(STRATEGY_TAGS, self.vault_address)

    def has_custom_fees(self) -> bool:
        """Centrifuge fees are managed at the pool/protocol level, not vault level."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Centrifuge fees are managed at the pool/protocol level.

        Fee structure varies by pool and is not directly accessible from the vault contract.
        """
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Centrifuge fees are managed at the pool/protocol level.

        Fee structure varies by pool and is not directly accessible from the vault contract.
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Centrifuge uses epoch-based redemptions.

        Redemption requests are processed at the end of each epoch,
        which typically runs daily but can vary by pool configuration.
        """
        return datetime.timedelta(days=1)

    def get_link(self, referral: str | None = None) -> str:
        """Get the link to this vault on the Centrifuge app.

        The vault link is in format: https://app.centrifuge.io/pool/{pool_id}
        """
        pool_id = self.fetch_pool_id()
        return f"https://app.centrifuge.io/pool/{pool_id}"

    def fetch_pool_id(self, block_identifier: BlockIdentifier = "latest") -> int:
        """Fetch the Centrifuge pool ID for this vault.

        :param block_identifier:
            Block number or 'latest'

        :return:
            The pool ID as an integer
        """
        return fetch_pool_id(self.web3, self.vault_address, block_identifier)

    def fetch_tranche_id(self, block_identifier: BlockIdentifier = "latest") -> bytes:
        """Fetch the Centrifuge tranche ID for this vault.

        :param block_identifier:
            Block number or 'latest'

        :return:
            The tranche ID as bytes
        """
        return fetch_tranche_id(self.web3, self.vault_address, block_identifier)
