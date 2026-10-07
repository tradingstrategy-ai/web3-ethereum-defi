"""Nest vault support.

Nest is real-world-asset vault infrastructure built by the team behind Plume.
Nest DAO LLC is identified as the primary vault curator. A NestVault is the
chain-specific entrypoint for a separate share token; its contracts implement
ERC-4626 accounting together with ERC-7540 asynchronous redemptions and
ERC-7575 separate share-token support.

- `Nest vault app <https://app.nest.credit/vaults>`__
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
from eth_defi.erc_4626.vault_protocol.nest.offchain_metadata import NEST_ARC_CHAIN_ID, NEST_CURATOR_SLUG, NestVaultMetadata, fetch_nest_vault_metadata, select_nest_manager_name
from eth_defi.erc_4626.vault_protocol.nest.tags import STRATEGY_TAGS
from eth_defi.types import Percent
from eth_defi.vault.base import VaultHistoricalReader
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags


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

        Bind the verified interface to this chain's deposit and redemption
        entrypoint rather than to its separate share token.

        :return:
            Deployed NestVault contract with the compact verified Nest ABI.
        """
        return get_deployed_contract(self.web3, "nest/NestVault.json", self.vault_address)

    @cached_property
    def nest_metadata(self) -> NestVaultMetadata | None:
        """Fetch cached first-party Nest product and route metadata.

        The catalogue supplies advisory product content for this chain and
        address. Contract reads remain authoritative for vault accounting.

        :return:
            Nest API and CMS metadata for this chain-specific entrypoint.
        """
        return fetch_nest_vault_metadata(self.web3, self.vault_address)

    @property
    def name(self) -> str:
        """Use Nest's current application name for active Arc products.

        Other chains retain their onchain share-token name because the new
        application currently serves Arc routes only.

        :return:
            Current Arc display name or onchain share-token name.
        """
        if self.chain_id == NEST_ARC_CHAIN_ID and self.nest_metadata:
            if self.nest_metadata.get("status") == "active" and (display_name := self.nest_metadata.get("display_name")):
                return display_name
        return super().name

    @property
    def description(self) -> str | None:
        """Return Nest's full first-party product description.

        Use the CMS strategy explanation without inferring realised returns.

        :return: Description, or ``None`` when unpublished.
        """
        return self.nest_metadata.get("description") if self.nest_metadata else None

    @property
    def short_description(self) -> str | None:
        """Return Nest's concise first-party product summary.

        Preserve the CMS summary independently of the full strategy text.

        :return: Summary, or ``None`` when unpublished.
        """
        return self.nest_metadata.get("short_description") if self.nest_metadata else None

    @property
    def manager_name(self) -> str | None:
        """Return a reviewed Nest manager or the best available yield partner.

        Yield partners are a CMS display list, so this label alone does not
        establish curator identity. Nest DAO's primary curator identity is
        exported separately for the curator resolver.

        :return:
            Selected first-party partner or reviewed manager name.
        """
        metadata = self.nest_metadata
        return select_nest_manager_name(metadata["slug"], metadata.get("yield_source_partners", [])) if metadata else None

    def get_strategy_tags(self) -> set[StrategyTag] | None:
        """Return reviewed tags for a Nest entrypoint address.

        The maintained table covers eight researched product strategies.
        Other Nest routes remain unclassified until separately researched.

        :return:
            A copy of the tags, or ``None`` for an unmapped address.
        """
        return lookup_strategy_tags(STRATEGY_TAGS, self.vault_address)

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:  # noqa: FBT001 - inherited reader interface accepts positional stateful.
        """Keep Nest's sampled NAV observations in routine scans and backfills.

        Daily NAV changes may be smaller than the common sparse threshold.
        Retain every successful sample taken by the reader. Stateful polling
        can still be daily or weekly for small or inactive vaults; it does not
        guarantee the migration's hourly observation grid.

        :param stateful: Use the scheduled scanner's adaptive read state.
        :return: Standard ERC-4626 reader with full sample retention.
        """
        reader = super().get_historical_reader(stateful)
        reader.write_all_samples = True
        return reader

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

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Report an unavailable management fee.

        This adapter has no decoder for Nest management fees. Missing fees
        must remain unknown rather than being reported as zero.

        :param block_identifier:
            Included for the standard vault-reader interface.

        :return:
            ``None`` until a route-specific fee decoder is implemented.
        """
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Report an unavailable performance fee.

        This adapter has no decoder for Nest performance fees. Exported gross
        returns therefore cannot be certified as returns after all fees.

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
        """Expose Nest's separate yield-source and risk explanations.

        Operator notes take precedence over the advisory CMS explanations.

        :return:
            Operator-set notes or distinct first-party strategy and risk notes.
        """
        if manual_notes := super().get_notes():
            return manual_notes
        if not self.nest_metadata:
            return None
        yield_origin = self.nest_metadata.get("yield_origin")
        risk_summary = self.nest_metadata.get("risk_summary")
        sections = [f"**Yield source:** {yield_origin}" if yield_origin else None, f"**Risks:** {risk_summary}" if risk_summary else None]
        return "\n\n".join(section for section in sections if section) or None

    def get_link(self, referral: str | None = None) -> str:
        """Return the current Arc product page for an active Arc route.

        Nest's current application only supports Arc. Other chain routes use
        the general catalogue rather than an Arc-specific minting page.

        :param referral:
            Unused because Nest does not document a referral URL format.

        :return:
            Active Arc product URL or the general Nest vault catalogue.
        """
        if self.chain_id == NEST_ARC_CHAIN_ID and self.nest_metadata:
            if self.nest_metadata.get("status") == "active" and (slug := self.nest_metadata.get("slug")):
                return f"https://app.nest.credit/vaults/{slug}"
        return "https://app.nest.credit/vaults"

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Persist parsed public Nest metadata alongside scanner fields.

        Declare the reviewed Nest DAO curator separately from indicative
        yield-source partner names.

        :return:
            Nest catalogue and CMS metadata for later export or audit.
        """
        metadata = self.nest_metadata
        return {"_nest_offchain_data": metadata, "_curator_slug": NEST_CURATOR_SLUG}
