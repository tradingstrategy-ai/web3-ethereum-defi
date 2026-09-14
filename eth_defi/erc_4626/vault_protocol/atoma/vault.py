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
- AVS implementation: https://arbitrum.blockscout.com/address/0xd4242FD8DE6E3128f0435b52DCe29155098CbBFF
- AVS2 proxy: https://arbiscan.io/address/0x1C788E14d8e5B446e3F71B5142e2edaBcAB36da1
- AVS2 implementation: https://arbitrum.blockscout.com/address/0x9521B08303AE010e85e24fC15D5334A0E506641E
"""

import datetime
import logging
from dataclasses import dataclass
from typing import Final

from eth_typing import BlockIdentifier, HexAddress
from web3.exceptions import BadFunctionCallOutput, ContractLogicError, Web3Exception

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.atoma.tags import STRATEGY_TAGS
from eth_defi.vault.base import WithdrawalDelayType, WithdrawalPeriod
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

logger = logging.getLogger(__name__)


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
        """Return the epoch-bounded Atoma request-to-claim window.

        Atoma accepts a withdrawal request and pays it from the next processed
        settlement epoch. A request can be made as soon as the current epoch
        permits it, while the normal upper bound is one configured epoch.
        See the `Atoma vault implementation <https://arbitrum.blockscout.com/address/0xd4242FD8DE6E3128f0435b52DCe29155098CbBFF>`__.

        :return:
            Zero-to-one epoch withdrawal period.
        """
        return WithdrawalPeriod(
            min_period=datetime.timedelta(0),
            max_period=self.get_estimated_lock_up(),
            delay_type=WithdrawalDelayType.epoch,
        )

    def get_link(self, referral: str | None = None) -> str:
        """Return the Atoma vault app link."""
        _ = self.vault_address, referral
        return "https://app.atoma.fi/"
