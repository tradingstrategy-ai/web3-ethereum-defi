"""Read Pear ERC-4626 vaults on HyperEVM.

See the `Pear vault documentation <https://docs.pear.garden/vaults>`__.
"""

from decimal import Decimal
from functools import cached_property

from eth_abi import decode
from eth_typing import BlockIdentifier, HexAddress
from typing_extensions import override
from web3 import Web3

from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.pear.tags import get_pear_strategy_tags
from eth_defi.types import Percent
from eth_defi.vault.base import normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.exception import UnsupportedVaultProtocol
from eth_defi.vault.handwritten_metadata import HandwrittenVaultMetadata, get_handwritten_vault_metadata
from eth_defi.vault.strategy_tag import StrategyTag

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
        return get_pear_strategy_tags(self.chain_id, self.address)

    @override
    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Return the documented zero management fee.

        The `Pear vault terms <https://www.pear.garden/vault-terms-of-service#tos%206>`__
        specify no recurring management fee. The contracts expose performance
        and withdrawal rates separately.

        :param block_identifier: Source block requested by the fee reader.
        :return: Annual management fee as a fraction.
        """
        return 0.0

    @override
    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Return the zero entry fee confirmed by the vault terms and deposit previews.

        Vault creation and agent funding are separate operator costs.

        :param block_identifier: Source block requested by the fee reader.
        :return: Depositor entry fee as a fraction.
        """
        return 0.0

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
        :return: Contract rate in basis points divided by 10,000. The separate
            minimum amount is exposed by :py:meth:`fetch_minimum_withdrawal_fee`.
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
        rate = int.from_bytes(self._fetch_word(self.address, signature, block_identifier), "big")
        if rate > _BASIS_POINTS_PER_UNIT:
            raise ValueError(f"Invalid Pear fee response for {signature} at {self.address}")
        return rate / _BASIS_POINTS_PER_UNIT

    def _fetch_word(self, address: HexAddress, signature: str, block_identifier: BlockIdentifier) -> bytes:
        """Read a single value using a getter confirmed against Pear's deployments.

        The deployed implementations have no verified ABI available. Pear's
        public dashboard uses these getters for its trading account and fee display.

        :param address: Checksummed vault or comptroller address.
        :param signature: No-argument Solidity getter signature.
        :param block_identifier: Block number or tag passed to the read.
        :return: One ABI-encoded word.
        """
        result = self.web3.eth.call({"to": address, "data": Web3.keccak(text=signature)[:4]}, block_identifier=block_identifier)
        if len(result) != _ABI_WORD_BYTES:
            raise ValueError(f"Invalid Pear response for {signature} at {address}")
        return result

    def fetch_hypercore_address(self, block_identifier: BlockIdentifier) -> HexAddress | None:
        """Read the trading account whose HyperCore balances contribute to NAV.

        Pear's catalogue calls this account ``agentAddress``. The onchain
        getter lets the scanner pick up changes without querying that catalogue.

        :param block_identifier: Block number or tag passed to the read.
        :return: Lowercase trading account address, or None when unconfigured.
        """
        (address,) = decode(["address"], self._fetch_word(self.address, "agent()", block_identifier))
        return HexAddress(address) if int(address, 16) else None

    def fetch_minimum_withdrawal_fee(self, block_identifier: BlockIdentifier) -> Decimal:
        """Read the comptroller's withdrawal fee floor in denomination tokens.

        The floor applies even when the vault's percentage rate is zero.
        Shieldify's Pear Vault V2 upgrade review describes this behaviour.

        :param block_identifier: Block number or tag for both settings reads.
        :return: Minimum fee in display units of the vault's denomination token.
        """
        (comptroller,) = decode(["address"], self._fetch_word(self.address, "comptroller()", block_identifier))
        raw = int.from_bytes(self._fetch_word(Web3.to_checksum_address(comptroller), "getMinFeeAmount()", block_identifier), "big")
        return self.denomination_token.convert_to_decimals(raw)

    @override
    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Store the live trading account and fee floor alongside vault metadata.

        The metrics exporter publishes this snapshot under ``other_data.pear``.

        :return: Private scan-row data used by individual vault JSON exports.
        """
        block = self._get_block_identifier()
        return {
            "_pear": {
                "hypercore_address": self.fetch_hypercore_address(block),
                "minimum_withdrawal_fee": self.fetch_minimum_withdrawal_fee(block),
            },
        }

    @override
    def supports_generic_deposit_manager(self) -> bool:
        """Pear withdrawals can queue and require Pear-specific settlement."""
        return False

    @override
    def get_deposit_manager(self) -> ERC4626DepositManager:
        """Refuse generic execution because the queued exit is unsupported."""
        message = "Pear withdrawals can require protocol-specific queue settlement"
        raise UnsupportedVaultProtocol(message)
