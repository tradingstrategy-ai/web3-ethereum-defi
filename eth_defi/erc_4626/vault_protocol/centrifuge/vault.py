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

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.centrifuge.centrifuge_utils import fetch_pool_id, fetch_tranche_id

logger = logging.getLogger(__name__)


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
