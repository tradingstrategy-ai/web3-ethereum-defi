"""Nest vault support.

Nest is Plume's real-world-asset vault infrastructure.  A NestVault is the
chain-specific entrypoint for a separate share token; its contracts implement
ERC-4626 accounting together with ERC-7540 asynchronous redemptions and
ERC-7575 separate share-token support.

- `Nest vault app <https://www.nest.credit/vaults#vaults-explore>`__
- `Nest contract documentation <https://docs.nest.credit/developers/smart-contracts/>`__
- `Verified nOPAL NestVault on Snowtrace <https://snowtrace.io/address/0xd258029cf5a177e3306e09fbea63424543a505c0#code>`__
- `Open-source contracts <https://github.com/plumenetwork/nest-protocol>`__
"""

# ruff: noqa: ARG002, PLR6301

import datetime
from functools import cached_property

from eth_typing import BlockIdentifier
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.nest.offchain_metadata import NestVaultMetadata, fetch_nest_vault_metadata


class NestVault(ERC4626Vault):
    """Nest real-world-asset vault entrypoint.

    Nest products have a dedicated share token and can offer multiple
    denomination and chain routes.  Deposits and redemptions should use Nest's
    Actions API: the public flow can require compliance checks, bridging and an
    asynchronous redemption claim.  Consequently this reader does not certify
    the generic synchronous ERC-4626 deposit manager.
    """

    @cached_property
    def nest_vault_contract(self) -> Contract:
        """Load Nest-specific contract methods not included in the standard ABI.

        :return:
            Deployed NestVault contract with the compact verified Nest ABI.
        """
        return get_deployed_contract(self.web3, "nest/NestVault.json", self.vault_address)

    @cached_property
    def nest_metadata(self) -> NestVaultMetadata | None:
        """Fetch cached first-party Nest product and route metadata.

        :return:
            Nest API and CMS metadata for this chain-specific entrypoint.
        """
        return fetch_nest_vault_metadata(self.web3, self.vault_address)

    @property
    def description(self) -> str | None:
        """Return Nest's full first-party product description, if available."""
        return self.nest_metadata.get("description") if self.nest_metadata else None

    @property
    def short_description(self) -> str | None:
        """Return Nest's concise first-party product summary, if available."""
        return self.nest_metadata.get("short_description") if self.nest_metadata else None

    def fetch_total_pending_shares(self, block_identifier: BlockIdentifier = "latest") -> int:
        """Read the onchain aggregate of queued asynchronous redemptions.

        ``totalPendingShares()`` is a NestVaultCore view that totals redemption
        requests awaiting fulfilment across all controllers.  It is useful for
        observability but does not predict an individual holder's settlement.

        :param block_identifier:
            Block at which the queue balance is read.

        :return:
            Number of raw share units pending redemption.
        """
        return self.nest_vault_contract.functions.totalPendingShares().call(block_identifier=block_identifier)

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Return no universal management fee because Nest configures fees per route.

        :param block_identifier:
            Included for the standard vault-reader interface.

        :return:
            ``None`` until a route-specific fee decoder is implemented.
        """
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Return no universal performance fee because Nest configures fees per route.

        :param block_identifier:
            Included for the standard vault-reader interface.

        :return:
            ``None`` until a route-specific fee decoder is implemented.
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Return Nest's CMS redemption estimate when it publishes one.

        The estimate is a product-level service indication and does not replace
        the onchain asynchronous-redemption state or guarantee settlement time.

        :return:
            Estimated redemption delay, or ``None`` if Nest does not publish it.
        """
        if self.nest_metadata and (days := self.nest_metadata.get("redemption_time_days")) is not None:
            return datetime.timedelta(days=days)
        return None

    def get_notes(self) -> str | None:
        """Use manually curated notes first, then Nest's first-party description.

        :return:
            Operator-set notes or the public Nest product description.
        """
        return super().get_notes() or self.description

    def get_link(self, referral: str | None = None) -> str:
        """Return Nest's public vault catalogue.

        :param referral:
            Unused because Nest does not document a referral URL format.

        :return:
            Nest vault explorer URL.
        """
        return "https://www.nest.credit/vaults#vaults-explore"
