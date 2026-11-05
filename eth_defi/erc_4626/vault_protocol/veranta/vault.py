"""Veranta unified avUSDC vault support.

The verified ``Tranche`` implementation uses synchronous ERC-4626 entry
points with a collateral-health withdrawal fee and post-withdrawal utilisation
limits. It is not a Gains epoch vault despite the shared ``depositCap`` probe.

- https://docs.veranta.xyz/liquidity-providers/veranta-lp-vault-avusdc
- https://basescan.org/address/0xe79184763b27fa8bb0eb2d6af1f0123d43af0e89#code
- https://basescan.org/address/0xc3769e440e3515ba374372837f76aafdc4fba26d#code
"""

import datetime
from functools import cached_property

from eth_typing import BlockIdentifier
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.veranta.constants import IMPLEMENTATION_SLOT, VERANTA_CHAIN_ID, VERANTA_FEE_DENOMINATOR, VERANTA_PUBLIC_IMPLEMENTATIONS, VERANTA_VAULT_ADDRESS
from eth_defi.erc_4626.vault_protocol.veranta.deposit_redeem import VerantaDepositManager
from eth_defi.erc_4626.vault_protocol.veranta.tags import STRATEGY_TAGS
from eth_defi.types import Percent
from eth_defi.vault.base import normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.deposit_redeem import VaultDepositManagerCapability
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags


class VerantaVault(ERC4626Vault):
    """Unified USDC counterparty-liquidity vault on Base.

    Only the reviewed unified deployment is supported. Account admission is
    source-proven for two implementations; an upgrade requires a fresh review.
    Deposit caps and withdrawal liquidity are availability restrictions, not
    account approval requirements.
    """

    @property
    def description(self) -> str | None:
        """Expose reviewed copy for the individual vault page.

        Protocol metadata is published separately and does not populate the
        scanner's vault description. Only the reviewed deployment has copy.

        :return: Normalised Markdown, or ``None`` for an unreviewed deployment.
        """
        if self.chain_id != VERANTA_CHAIN_ID or self.address.lower() != VERANTA_VAULT_ADDRESS:
            return None
        metadata = get_handwritten_vault_metadata(self.chain_id, self.address)
        return normalise_vault_description(metadata.description) if metadata else None

    @property
    def short_description(self) -> str | None:
        """Expose the reviewed one-sentence listing summary.

        Use the same address-scoped source as the full description so scanner
        refreshes and metadata repairs publish consistent text.

        :return: Plain-text summary, or ``None`` for an unreviewed deployment.
        """
        if self.chain_id != VERANTA_CHAIN_ID or self.address.lower() != VERANTA_VAULT_ADDRESS:
            return None
        metadata = get_handwritten_vault_metadata(self.chain_id, self.address)
        return normalise_vault_short_description(metadata.short_description) if metadata else None

    @cached_property
    def vault_contract(self) -> Contract:
        """Bind the verified implementation interface to the proxy.

        :return: ERC-4626 methods and Veranta fee/configuration accessors.
        """
        return get_deployed_contract(self.web3, "veranta/Tranche.json", self.address)

    def is_whitelisted_deposit(self) -> bool:
        """Classify the reviewed implementation's public account admission.

        Both verified sources lack sender and receiver identity gates in the
        inherited ERC-4626 deposit path. Unknown upgrades remain unclassified.
        RPC failures propagate to the scanner's existing unknown-read handling.

        :return: ``False`` for the reviewed deployment and implementations.
        :raise NotImplementedError: If the deployment or implementation is unreviewed.
        """
        if self.chain_id != VERANTA_CHAIN_ID or self.address.lower() != VERANTA_VAULT_ADDRESS:
            raise NotImplementedError("Unreviewed Veranta deployment")
        implementation = self.web3.eth.get_storage_at(self.address, IMPLEMENTATION_SLOT, block_identifier=self.default_block_identifier)
        if "0x" + implementation[-20:].hex() not in VERANTA_PUBLIC_IMPLEMENTATIONS:
            raise NotImplementedError("Unreviewed Veranta implementation")
        return False

    def get_deposit_manager(self) -> VerantaDepositManager:
        """Return synchronous execution with exact redemption preflight.

        :return: Manager emitting standard approval, deposit and redeem calls.
        """
        return VerantaDepositManager(self)

    def get_deposit_manager_capability(self) -> VaultDepositManagerCapability:
        """Describe the guarded, synchronous two-way lifecycle.

        :return: Public capability; live capacity still requires preflight.
        """
        return self.get_synchronous_deposit_manager_capability()

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Leave unpublished management charges unknown.

        :param block_identifier: Requested fee block.
        :return: ``None``; trading revenue does not establish a manager fee.
        """
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Leave unpublished performance charges unknown.

        :param block_identifier: Requested fee block.
        :return: ``None``; no authoritative performance-fee accessor exists.
        """
        return None

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Read the unified vault's deposit fee configuration.

        The reviewed implementations return zero balancing fees after tranche
        unification. Exact quotes still use the contract's rounded previews.

        :param block_identifier: Common block for the fee switch and rate.
        :return: Fractional deposit charge, zero when fees are disabled.
        """
        if not self.vault_contract.functions.feesOn().call(block_identifier=block_identifier):
            return 0.0
        return self.vault_contract.functions.balancingFee(0, True).call(block_identifier=block_identifier) / VERANTA_FEE_DENOMINATOR

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Read the collateral-health charge without rounding transaction amounts.

        ``previewRedeem`` accounts for fee-inclusive rounding; this accessor
        reports the configured nominal rate, not a replacement quote formula.

        :param block_identifier: Common block for the switch and both rates.
        :return: Fractional withdrawal charge, zero when fees are disabled.
        """
        functions = self.vault_contract.functions
        if not functions.feesOn().call(block_identifier=block_identifier):
            return 0.0
        rate = functions.balancingFee(0, False).call(block_identifier=block_identifier) + functions.collateralHealthFee().call(block_identifier=block_identifier)
        return rate / VERANTA_FEE_DENOMINATOR

    def get_estimated_lock_up(self) -> datetime.timedelta:
        """Return the absence of a scheduled redemption delay.

        Liquidity and utilisation limits can still prevent an immediate exit.

        :return: Zero because withdrawals do not use a queue or epoch.
        """
        return datetime.timedelta(0)

    def get_strategy_tags(self) -> set[StrategyTag] | None:
        """Resolve the reviewed deployment's strategy tags.

        :return: Independent tag set, or ``None`` for an unmapped deployment.
        """
        if self.chain_id != VERANTA_CHAIN_ID:
            return None
        return lookup_strategy_tags(STRATEGY_TAGS, self.address)

    def get_link(self, referral: str | None = None) -> str:
        """Return the official liquidity-provider application.

        :param referral: Unused optional referral identifier.
        :return: Earn page for the unified vault.
        """
        return "https://www.avantisfi.com/earn"
