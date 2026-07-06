"""T3tris vault support.

T3tris is a tokenised vault protocol for professional asset managers. It builds
vaults around ERC-4626 shares and adds an ERC-7540-like asynchronous request
lifecycle with protocol-specific method selectors.

- `Homepage <https://t3tris.finance/>`__
- `Vault app <https://app.t3tris.finance/vaults>`__
- `Documentation repository <https://github.com/t3tris-finance/mdoc-t3tris>`__
- `Local research notes <../../../../vault_protocols/t3tris/README-t3tris.md>`__
"""

import logging
from functools import cached_property

from eth_typing import BlockIdentifier
from web3.contract import Contract

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.t3tris.offchain_metadata import T3trisVaultMetadata, fetch_t3tris_vault_metadata

logger = logging.getLogger(__name__)

WAD = 10**18


def _wad_to_percent(value: int) -> float:
    """Convert a T3tris WAD fee value to a fraction."""
    return value / WAD


class T3trisVault(ERC4626Vault):
    """T3tris protocol vaults.

    - T3tris vaults expose standard ERC-4626 accounting methods
    - Async deposit/redemption flow uses custom ``DepositRequest`` and
      ``RedeemRequest`` events and custom request/claim methods
    - Fee values are exposed as WAD-scaled integers by the live vault ABI
    - Offchain descriptions are fetched from the T3tris page API
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="t3tris/IVault.json",
        )

    @cached_property
    def t3tris_metadata(self) -> T3trisVaultMetadata | None:
        """Offchain metadata from T3tris' web app API.

        Fetched from ``api.t3tris.finance/api/v1/pages/vault`` and cached on
        disk and in-process to avoid repeated API calls.
        """
        return fetch_t3tris_vault_metadata(self.web3, self.spec.vault_address)

    @property
    def description(self) -> str | None:
        """Full vault strategy description from T3tris' offchain metadata."""
        if self.t3tris_metadata:
            return self.t3tris_metadata.get("description")
        return None

    @property
    def short_description(self) -> str | None:
        """Short vault summary from T3tris' offchain metadata."""
        metadata = self.t3tris_metadata
        if not metadata:
            return None
        parts = [metadata.get("category"), metadata.get("rating")]
        parts.extend(metadata.get("attributes") or [])
        return ", ".join(part for part in parts if part) or None

    @property
    def manager_name(self) -> str | None:
        """T3tris curator name from offchain vault metadata."""
        if self.t3tris_metadata:
            return self.t3tris_metadata.get("curator_name")
        return None

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get the current annual management fee as a fraction.

        T3tris returns ``(managementFeeWad, managementFeeDays)``. The first
        value is WAD-scaled where ``1e18`` is 100%.

        :param block_identifier:
            Block to read.

        :return:
            ``0.02`` means 2%.
        """
        management_fee_wad, _management_fee_days = self.vault_contract.functions.getManagementFee().call(block_identifier=block_identifier)
        return _wad_to_percent(management_fee_wad)

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get the current performance fee as a fraction.

        :param block_identifier:
            Block to read.

        :return:
            ``0.2`` means 20%.
        """
        return _wad_to_percent(self.vault_contract.functions.getPerformanceFee().call(block_identifier=block_identifier))

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current entry fee as a fraction.

        :param block_identifier:
            Block to read.

        :return:
            ``0.01`` means 1%.
        """
        return _wad_to_percent(self.vault_contract.functions.getEntryFee().call(block_identifier=block_identifier))

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get the current exit fee as a fraction.

        :param block_identifier:
            Block to read.

        :return:
            ``0.01`` means 1%.
        """
        return _wad_to_percent(self.vault_contract.functions.getExitFee().call(block_identifier=block_identifier))

    def get_link(self, referral: str | None = None) -> str:
        """Link to the T3tris vault app."""
        url = f"https://app.t3tris.finance/vaults?chainId={self.chain_id}&address={self.vault_address}"
        if referral:
            return f"{url}&ref={referral}"
        return url
