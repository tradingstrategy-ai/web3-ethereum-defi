"""Read Pear ERC-4626 vaults on HyperEVM.

See the `Pear vault documentation <https://docs.pear.garden/vaults>`__.
"""

from functools import cached_property

from eth_typing import BlockIdentifier
from typing_extensions import override
from web3 import Web3

from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.pear.constants import PEAR_CHAIN_ID
from eth_defi.erc_4626.vault_protocol.pear.tags import STRATEGY_TAGS
from eth_defi.types import Percent
from eth_defi.vault.base import normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.exception import UnsupportedVaultProtocol
from eth_defi.vault.handwritten_metadata import HandwrittenVaultMetadata, get_handwritten_vault_metadata
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

_BASIS_POINTS_PER_UNIT = 10_000
_ABI_WORD_BYTES = 32


class PearVault(ERC4626Vault):
    """Read a Pear trading vault using the standard ERC-4626 price reader.

    NAV includes HyperCore capital. The price scanner samples this adapter
    at the latest block and retains the observed execution block and time.
    """

    @cached_property
    def _reviewed_metadata(self) -> HandwrittenVaultMetadata | None:
        return get_handwritten_vault_metadata(self.chain_id, self.address)

    @property
    def description(self) -> str | None:
        """Return the reviewed Markdown description for this deployment."""
        return normalise_vault_description(self._reviewed_metadata.description) if self._reviewed_metadata else None

    @property
    def short_description(self) -> str | None:
        """Return the reviewed listing summary for this deployment."""
        return normalise_vault_short_description(self._reviewed_metadata.short_description) if self._reviewed_metadata else None

    @override
    def get_link(self, referral: str | None = None) -> str:
        """Return the public page for this vault.

        :param referral: Optional adapter argument. Pear uses an address-only URL.
        :return: Direct vault dashboard URL.
        """
        return f"https://vaults.pear.garden/{self.address.lower()}"

    @override
    def get_strategy_tags(self) -> set[StrategyTag] | None:
        """Return a copy of the reviewed tags, or None for an unreviewed vault."""
        if self.chain_id == PEAR_CHAIN_ID:
            return lookup_strategy_tags(STRATEGY_TAGS, self.address)
        return None

    @override
    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Report the management fee as unknown.

        :param block_identifier: Source block requested by the shared fee reader.
        :return: None because the reviewed getters cover performance and withdrawal fees.
        """
        return None

    @override
    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Read the realised-profit performance fee as a fraction.

        :param block_identifier: Block number or tag at which to read the fee.
        :return: Contract rate in basis points divided by 10,000.
        """
        return self._fetch_fee("getPerformanceFeeBPS()", block_identifier)

    @override
    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Read the withdrawal fee as a fraction.

        :param block_identifier: Block number or tag at which to read the fee.
        :return: Contract rate in basis points divided by 10,000.
        """
        return self._fetch_fee("getWithdrawalFeeBPS()", block_identifier)

    def _fetch_fee(self, signature: str, block_identifier: BlockIdentifier) -> Percent:
        """Read a no-argument Pear fee selector whose implementation ABI is unpublished.

        Both signatures and their basis-point scale were cross-checked against
        the rates shown by the Pear vault app at https://vaults.pear.garden/.

        :param signature: Canonical Solidity fee getter signature.
        :param block_identifier: Block number or tag at which to read the fee.
        :return: Fee fraction, between zero and one inclusive.
        :raise ValueError: If the contract does not return a valid fee word.
        """
        result = self.web3.eth.call({"to": self.address, "data": Web3.keccak(text=signature)[:4]}, block_identifier=block_identifier)
        rate = int.from_bytes(result, "big")
        if len(result) != _ABI_WORD_BYTES or rate > _BASIS_POINTS_PER_UNIT:
            raise ValueError(f"Invalid Pear fee response for {signature} at {self.address}")
        return rate / _BASIS_POINTS_PER_UNIT

    @override
    def supports_generic_deposit_manager(self) -> bool:
        """Pear withdrawals can queue and require Pear-specific settlement."""
        return False

    @override
    def get_deposit_manager(self) -> ERC4626DepositManager:
        """Refuse generic execution because the queued exit is unsupported."""
        message = "Pear withdrawals can require protocol-specific queue settlement"
        raise UnsupportedVaultProtocol(message)
