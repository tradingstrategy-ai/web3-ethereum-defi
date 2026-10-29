"""Atoma protocol vault support.

Atoma runs Arbitrum USDC vaults that seek delta-neutral yield from perpetual DEX
funding-rate spreads. Users deposit USDC into an ERC-4626 vault share token and
withdraw through an epoch-based request/claim flow.

The verified AtomaVault implementation exposes fixed fee constants:

- ``PERFORMANCE_FEE_BPS = 2000`` (20%)
- ``WITHDRAWAL_FEE_BPS = 50`` (0.5%)
- ``MIN_DEPOSIT = 100e6`` (100 USDC)

The performance fee is internalised through share minting when NAV exceeds the
high-water mark. The withdrawal fee is externalised and deducted from the USDC
payout in ``claimWithdrawal()``.

- App: https://app.atoma.fi/
- AVS proxy: https://arbiscan.io/address/0xCC56410e1a136aF0eCEb7241c6aE394F4d8b581c
- AVS implementation: https://arbiscan.io/address/0x9161be115867c347adfb4c88a80e759920de86dc#code
- AVS2 proxy: https://arbiscan.io/address/0x1C788E14d8e5B446e3F71B5142e2edaBcAB36da1
- AVS2 implementation: https://arbiscan.io/address/0x9161be115867c347adfb4c88a80e759920de86dc#code
"""

import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal
from functools import cached_property
from typing import Final

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import BadFunctionCallOutput, ContractLogicError, Web3Exception

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.atoma.deposit_redeem import AtomaDepositManager
from eth_defi.erc_4626.vault_protocol.atoma.tags import STRATEGY_TAGS
from eth_defi.vault.base import WithdrawalDelayType, WithdrawalPeriod
from eth_defi.vault.deposit_redeem import VaultDepositManagerCapability, VaultFlowUnavailable
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

logger = logging.getLogger(__name__)

#: Reviewed execution implementation at Arbitrum block 519150162.
ATOMA_IMPLEMENTATION = "0x9161be115867c347adfb4c88a80e759920de86dc"
#: Historical decoding implementation at the shared July fork block.
ATOMA_LEGACY_IMPLEMENTATION = "0x9521b08303ae010e85e24fc15d5334a0e506641e"
#: ERC-1967 implementation storage slot.
IMPLEMENTATION_SLOT = int.from_bytes(Web3.keccak(text="eip1967.proxy.implementation"), "big") - 1


@dataclass(slots=True, frozen=True)
class AtomaVaultDescription:
    """Address-scoped display metadata for an Atoma vault.

    Atoma does not expose the strategy descriptions onchain. Keep the overlay
    keyed by vault address so each vault can retain its specific strategy copy.

    :param short_description:
        Listing-friendly strategy summary.
    :param description:
        Longer strategy description, including its authoritative source.
    """

    #: Listing-friendly strategy summary.
    short_description: str

    #: Longer source-linked strategy description.
    description: str


#: Atoma Index vault address on Arbitrum.
#:
#: https://arbiscan.io/address/0xCC56410e1a136aF0eCEb7241c6aE394F4d8b581c
ATOMA_VAULT_ADDRESS = HexAddress("0xcc56410e1a136af0eceb7241c6ae394f4d8b581c")

#: Atoma RWA vault address on Arbitrum.
#:
#: https://arbiscan.io/address/0x1C788E14d8e5B446e3F71B5142e2edaBcAB36da1
ATOMA_VAULT_2_ADDRESS = HexAddress("0x1c788e14d8e5b446e3f71b5142e2edabcab36da1")

#: All supported Atoma vault addresses on Arbitrum.
ATOMA_VAULT_ADDRESSES: frozenset[HexAddress] = frozenset((ATOMA_VAULT_ADDRESS, ATOMA_VAULT_2_ADDRESS))

#: Human-readable strategy copy for Atoma vaults without an offchain metadata API.
#:
#: Atoma Index rotates capital between paired perpetual venues, while Atoma RWA
#: trades real-world-asset perpetual markets. Keep this address-scoped because
#: the vaults use different strategies and risk profiles.
ATOMA_VAULT_DESCRIPTION_OVERLAY: Final[dict[HexAddress, AtomaVaultDescription]] = {
    ATOMA_VAULT_ADDRESS: AtomaVaultDescription(
        short_description="Aggressive multi-venue strategy for maximum venue-point upside.",
        description=("Aggressive multi-venue strategy: capital rotates across paired venues to chase the highest-paying opportunities and maximize points and rewards from each platform. Positions are hedged, but venue and rotation risk is higher — for depositors who want maximum upside from venue points."),
    ),
    ATOMA_VAULT_2_ADDRESS: AtomaVaultDescription(
        short_description="Conservative market-neutral RWA perpetuals strategy.",
        description=("Conservative market-neutral strategy on real-world assets — stock & commodity perp markets on Lighter and trade[XYZ]. Fully hedged positions collect funding-rate spreads and cross-venue price gaps. No directional exposure, lower risk profile — the steady option."),
    ),
}

#: Curated display names for Atoma vaults whose onchain share-token names are generic.
ATOMA_VAULT_NAME_OVERLAY: Final[dict[HexAddress, str]] = {
    ATOMA_VAULT_ADDRESS: "Atoma Index",
    ATOMA_VAULT_2_ADDRESS: "Atoma RWA",
}

#: Atoma performance fee in basis points.
PERFORMANCE_FEE_BPS = 2_000

#: Atoma withdrawal fee in basis points.
WITHDRAWAL_FEE_BPS = 50

#: Basis point divisor used by Atoma fee constants.
BPS_DIVISOR = 10_000

#: Fallback epoch duration if the live contract read is unavailable.
DEFAULT_EPOCH_DURATION = datetime.timedelta(days=7)

#: Minimal ABI for the Atoma ``epochDuration()`` accessor.
_EPOCH_DURATION_ABI = [
    {
        "inputs": [],
        "name": "epochDuration",
        "outputs": [{"internalType": "uint64", "name": "", "type": "uint64"}],
        "stateMutability": "view",
        "type": "function",
    },
]


class AtomaVault(ERC4626Vault):
    """Atoma delta-neutral USDC vault on Arbitrum.

    Atoma uses standard ERC-4626 deposits but disables direct
    ``withdraw()``/``redeem()``. Users call ``requestWithdrawal(shares)`` and
    later ``claimWithdrawal(epochId)`` after the settlement epoch has been
    processed.
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Bind the current verified interface at the proxy address.

        Execution methods independently verify the implementation; a binding
        alone does not certify a future proxy upgrade.

        :return: Bound Atoma contract.
        """
        return get_deployed_contract(self.web3, "atoma/AtomaVault.json", self.vault_address)

    def fetch_implementation(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Read the proxy implementation at a specified block.

        Uses the ERC-1967 slot rather than inferring a version from getters.

        :param block_identifier: Historical block or latest state.
        :return: Lowercase implementation address.
        """
        value = self.web3.eth.get_storage_at(self.address, IMPLEMENTATION_SLOT, block_identifier=block_identifier)
        return HexAddress("0x" + bytes(value[-20:]).hex())

    def check_execution_version(self, block_identifier: BlockIdentifier) -> HexAddress:
        """Reject execution outside the reviewed deployments and version.

        Static capability is separate from this current-state check.

        :param block_identifier: Pinned operation block.
        :return: Reviewed implementation address.
        :raises VaultFlowUnavailable: Unsupported deployment or upgrade.
        """
        if self.chain_id != 42161 or self.web3.eth.chain_id != self.chain_id or self.address.lower() not in ATOMA_VAULT_ADDRESSES or self.fetch_implementation(block_identifier) != ATOMA_IMPLEMENTATION:
            raise VaultFlowUnavailable("Unreviewed Atoma deployment or implementation", protocol="Atoma", vault_address=self.address, phase="preflight", preflight_result="unsupported_implementation")
        return HexAddress(ATOMA_IMPLEMENTATION)

    def fetch_receipt_contract(self, block_number: int) -> tuple[Contract, HexAddress]:
        """Select a historical receipt decoder without guessing across upgrades.

        A block containing an implementation change is deliberately ambiguous:
        retain its receipt for reconciliation instead of retrying the operation.

        :param block_number: Mined receipt block.
        :return: Verified binding and implementation.
        :raises ValueError: Unknown implementation or an upgrade in this block.
        """
        implementation = self.fetch_implementation(block_number)
        interfaces = {ATOMA_IMPLEMENTATION: "atoma/AtomaVault.json", ATOMA_LEGACY_IMPLEMENTATION: "atoma/AtomaVaultLegacy.json"}
        if implementation not in interfaces or self.fetch_implementation(block_number - 1) != implementation:
            raise ValueError("Atoma receipt implementation is unknown or changed within its block; reconcile the mined receipt")
        return get_deployed_contract(self.web3, interfaces[implementation], self.address), implementation

    def fetch_minimum_deposit(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch the denomination-token deposit minimum.

        Read the source-proven constant at the requested block.

        :param block_identifier: State block.
        :return: Decimal USDC minimum.
        """
        return self.denomination_token.convert_to_decimals(self.vault_contract.functions.MIN_DEPOSIT().call(block_identifier=block_identifier))

    def fetch_current_epoch(self, block_identifier: BlockIdentifier = "latest") -> int:
        """Read the active epoch from the contract's mutable schedule.

        :param block_identifier: State snapshot.
        :return: Current epoch id.
        """
        return self.vault_contract.functions.getCurrentEpoch().call(block_identifier=block_identifier)

    def fetch_epoch(self, epoch: int, block_identifier: BlockIdentifier = "latest") -> tuple[int, int, bool]:
        """Read aggregate requests and their fixed settlement NAV.

        A settled epoch need not have enough idle assets for a claim. NAV uses
        the contract's NAV_PRECISION and can subsequently be haircut.

        :param epoch: Settlement epoch id.
        :param block_identifier: State snapshot.
        :return: Raw requested shares, settlement NAV and settled flag.
        """
        return tuple(self.vault_contract.functions.getEpoch(epoch).call(block_identifier=block_identifier))

    def fetch_epoch_end(self, epoch: int, block_identifier: BlockIdentifier = "latest") -> datetime.datetime:
        """Read the earliest operator-settlement time for a specific epoch.

        This uses scheduled duration changes and gives no settlement guarantee.

        :param epoch: Settlement or deposit-lock epoch.
        :param block_identifier: State snapshot.
        :return: Naive UTC epoch end.
        """
        timestamp = self.vault_contract.functions.getEpochEndTime(epoch).call(block_identifier=block_identifier)
        return datetime.datetime.fromtimestamp(timestamp, datetime.UTC).replace(tzinfo=None)

    def fetch_user_epoch_shares(self, owner: HexAddress, epoch: int, block_identifier: BlockIdentifier = "latest") -> int:
        """Read the owner's whole unclaimed request aggregate.

        The shares remain recorded after settlement burns the escrowed tokens.
        A claim consumes this entire aggregate.

        :param owner: Custody caller.
        :param epoch: Settlement epoch.
        :param block_identifier: State snapshot.
        :return: Raw aggregate shares, zero after claim.
        """
        return self.vault_contract.functions.userEpochShares(epoch, owner).call(block_identifier=block_identifier)

    def fetch_accrued_withdrawal_fees(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read retained exit fees, separately from claimable assets.

        This getter is specific to the reviewed current implementation.

        :param block_identifier: State snapshot.
        :return: Uncollected USDC fees in human-readable units.
        """
        block = self.web3.eth.block_number if block_identifier == "latest" else block_identifier
        self.check_execution_version(block)
        return self.denomination_token.convert_to_decimals(self.vault_contract.functions.accruedWithdrawalFees().call(block_identifier=block))

    def get_deposit_manager(self) -> AtomaDepositManager:
        return AtomaDepositManager(self)

    def supports_generic_deposit_manager(self) -> bool:
        return False

    def is_whitelisted_deposit(self) -> bool:
        return False

    def get_deposit_manager_capability(self) -> VaultDepositManagerCapability | None:
        if self.chain_id != 42161 or self.address.lower() not in ATOMA_VAULT_ADDRESSES:
            return None
        return VaultDepositManagerCapability(can_deposit=True, can_redeem=True, deposit_flow="synchronous", redemption_flow="asynchronous", supports_anvil_settlement=True)

    @property
    def name(self) -> str:
        """Return a curated name for a known Atoma strategy.

        The onchain share-token names are generic, so the address-scoped
        overlay provides the established public name. Other Atoma vaults retain
        their onchain token names.

        :return:
            Curated name when available, otherwise the onchain token name.
        """

        name = ATOMA_VAULT_NAME_OVERLAY.get(HexAddress(str(self.vault_address).lower()))
        return name if name else super().name

    @property
    def description(self) -> str | None:
        """Return a source-linked description for a known Atoma strategy.

        The common Atoma contract interface does not distinguish strategy
        details. The address-scoped overlay supplies reviewed offchain copy
        for the reviewed public vaults.

        :return:
            Full strategy description, or ``None`` when no overlay exists.
        """

        metadata = ATOMA_VAULT_DESCRIPTION_OVERLAY.get(HexAddress(str(self.vault_address).lower()))
        return metadata.description if metadata else None

    @property
    def short_description(self) -> str | None:
        """Return a concise description for a known Atoma strategy.

        :return:
            Listing-friendly strategy summary, or ``None`` when no overlay
            exists.
        """

        metadata = ATOMA_VAULT_DESCRIPTION_OVERLAY.get(HexAddress(str(self.vault_address).lower()))
        return metadata.short_description if metadata else None

    def get_strategy_tags(self) -> set[StrategyTag] | None:
        """Return the maintained strategy tags for this Atoma vault.

        :return:
            Copy of the tag set, or ``None`` when this vault has not yet been
            classified.
        """
        return lookup_strategy_tags(STRATEGY_TAGS, self.vault_address)

    def has_custom_fees(self) -> bool:
        """Atoma has a mixed internalised performance fee and external withdrawal fee."""
        _ = self.vault_address
        return True

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Atoma has no management fee in the verified source.

        :param block_identifier:
            Unused block identifier kept for the shared vault fee API.

        :return:
            Management fee as a fraction.
        """
        _ = self.vault_address, block_identifier
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """Return Atoma's fixed 20% high-water-mark performance fee.

        :param block_identifier:
            Unused block identifier kept for the shared vault fee API.

        :return:
            Performance fee as a fraction.
        """
        _ = self.vault_address, block_identifier
        return PERFORMANCE_FEE_BPS / BPS_DIVISOR

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> float:
        """Return Atoma's fixed 0.5% withdrawal fee.

        :param block_identifier:
            Unused block identifier kept for the shared vault fee API.

        :return:
            Withdrawal fee as a fraction.
        """
        _ = self.vault_address, block_identifier
        return WITHDRAWAL_FEE_BPS / BPS_DIVISOR

    def get_estimated_lock_up(self) -> datetime.timedelta:
        """Return the current epoch length as the lock-up estimate.

        Atoma exposes a mutable ``epochDuration()`` value. A user can request
        withdrawal after the deposit epoch, then claim after the settlement
        epoch is processed.

        :return:
            Estimated withdrawal settlement interval.
        """
        try:
            contract = self.web3.eth.contract(address=self.vault_address, abi=_EPOCH_DURATION_ABI)
            duration_seconds = contract.functions.epochDuration().call(block_identifier="latest")
            return datetime.timedelta(seconds=duration_seconds)
        except (BadFunctionCallOutput, ContractLogicError, ValueError, Web3Exception):
            return DEFAULT_EPOCH_DURATION

    def get_withdrawal_period(self) -> WithdrawalPeriod:
        """Describe operator-settled withdrawals without a promised maximum.

        A request in epoch N targets N+1. The operator can settle only after
        that epoch ends; elapsed time alone neither settles nor pays a claim.
        Execution reads the ticket's actual epoch end from the contract.

        :return:
            Epoch-based settlement with no guaranteed timing bounds.
        """
        return WithdrawalPeriod(min_period=None, max_period=None, delay_type=WithdrawalDelayType.epoch)

    def get_link(self, referral: str | None = None) -> str:
        """Return the Atoma vault app link."""
        _ = self.vault_address, referral
        return "https://app.atoma.fi/"
