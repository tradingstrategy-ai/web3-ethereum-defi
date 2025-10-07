"""Euler Vault Kit specific integrations.

- Metadata repo https://github.com/euler-xyz/euler-labels/blob/master/130/vaults.json
"""

from functools import cached_property
import logging


from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.euler.offchain_metadata import EulerVaultMetadata, fetch_euler_vault_metadata


logger = logging.getLogger(__name__)


class EulerVault(ERC4626Vault):
    """Euler vault support.

    - Handle special offchain metadata
    - Example vault https://etherscan.io/address/0x1e548CfcE5FCF17247E024eF06d32A01841fF404#code

    TODO: Fees
    """

    @cached_property
    def euler_metadata(self) -> EulerVaultMetadata:
        return fetch_euler_vault_metadata(self.web3, self.vault_address)

    @property
    def name(self) -> str:
        if self.euler_metadata:
            return self.euler_metadata["name"]
        return super().name

    @property
    def description(self) -> str | None:
        return self.euler_metadata.get("description")

    @property
    def entity(self) -> str | None:
        return self.euler_metadata.get("entity")
