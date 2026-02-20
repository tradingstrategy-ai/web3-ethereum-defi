"""Events we use in the vault discovery.

- Shared across RPC/Hypersync discovery
- Supports standard ERC-4626 Deposit/Withdraw events
- Supports BrinkVault DepositFunds/WithdrawFunds events
- Supports EmberVault VaultDeposit/RequestRedeemed events
"""

import abc
import dataclasses
import datetime
import enum
import logging
from abc import abstractmethod
from dataclasses import dataclass
from typing import Type, Iterable

from web3.contract.contract import ContractEvent

from eth_defi.abi import get_contract
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import probe_vaults
from eth_defi.erc_4626.core import get_erc_4626_contract, ERC4626Feature, ERC4262VaultDetection
from eth_typing import HexAddress

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.risk import BROKEN_VAULT_CONTRACTS

logger = logging.getLogger(__name__)


class VaultEventKind(enum.Enum):
    """Classify vault discovery events by their type."""

    #: Deposit-like event (ERC-4626 Deposit or BrinkVault DepositFunds)
    deposit = "deposit"

    #: Withdraw-like event (ERC-4626 Withdraw or BrinkVault WithdrawFunds)
    withdraw = "withdraw"


@dataclasses.dataclass(slots=True, frozen=False)
class PotentialVaultMatch:
    """Categorise contracts that emit ERC-4626 like events."""

    chain: int
    address: HexAddress
    first_seen_at_block: int
    first_seen_at: datetime.datetime
    deposit_count: int = 0
    withdrawal_count: int = 0

    def is_candidate(self) -> bool:
        return self.deposit_count > 0 and self.withdrawal_count > 0


def get_brink_vault_contract(web3):
    """Get IBrinkVault interface for BrinkVault events."""
    return get_contract(
        web3,
        "brink/IBrinkVault.json",
    )


def get_ember_vault_event_contract(web3):
    """Get IEmberVaultEvents interface for EmberVault events."""
    return get_contract(
        web3,
        "ember/IEmberVaultEvents.json",
    )


def get_standard_erc_4626_vault_discovery_events(web3) -> list[Type[ContractEvent]]:
    """Get list of standard ERC-4626 events we use in vault discovery.

    .. note::

        This returns only standard ERC-4626 events. For all vault events
        including protocol-specific ones, use :py:func:`get_vault_discovery_events`.
    """
    # event Deposit(
    #     address indexed sender,
    #     address indexed owner,
    #     uint256 assets,
    #     uint256 shares
    #
    # )

    # event Withdraw(
    #     address indexed sender,
    #     address indexed receiver,
    #     address indexed owner,
    #     uint256 assets,
    #     uint256 shares
    # )

    IERC4626 = get_erc_4626_contract(web3)
    return [
        IERC4626.events.Deposit,
        IERC4626.events.Withdraw,
    ]


def get_brink_vault_discovery_events(web3) -> list[Type[ContractEvent]]:
    """Get list of BrinkVault events we use in vault discovery.

    BrinkVault uses custom events instead of standard ERC-4626 Deposit/Withdraw:

    - Deposited(address caller, address recipient, uint256 assets, uint256 shares)
    - Withdrawal(address caller, address recipient, uint256 received, uint256 shares)
    """
    IBrinkVault = get_brink_vault_contract(web3)
    return [
        IBrinkVault.events.Deposited,
        IBrinkVault.events.Withdrawal,
    ]


def get_ember_vault_discovery_events(web3) -> list[Type[ContractEvent]]:
    """Get list of EmberVault events we use in vault discovery.

    EmberVault uses custom events instead of standard ERC-4626 Deposit/Withdraw:

    - VaultDeposit(address indexed vault, address indexed depositor, address indexed receiver, uint256 amountDeposited, uint256 sharesMinted, uint256 totalShares, uint256 timestamp, uint256 sequenceNumber)
    - RequestRedeemed(address indexed vault, address indexed owner, address indexed receiver, uint256 shares, uint256 estimatedWithdrawAmount, uint256 timestamp, uint256 sequenceNumber)
    """
    IEmberVaultEvents = get_ember_vault_event_contract(web3)
    return [
        IEmberVaultEvents.events.VaultDeposit,
        IEmberVaultEvents.events.RequestRedeemed,
    ]


def get_vault_discovery_events(web3) -> list[Type[ContractEvent]]:
    """Get all events used in vault discovery, including protocol-specific ones.

    This includes:
    - Standard ERC-4626 Deposit/Withdraw events
    - BrinkVault DepositFunds/WithdrawFunds events
    - EmberVault VaultDeposit/RequestRedeemed events

    :return:
        List of contract event types in order:
        [ERC4626.Deposit, ERC4626.Withdraw, BrinkVault.Deposited, BrinkVault.Withdrawal, EmberVault.VaultDeposit, EmberVault.RequestRedeemed]
    """
    return get_standard_erc_4626_vault_discovery_events(web3) + get_brink_vault_discovery_events(web3) + get_ember_vault_discovery_events(web3)


def get_vault_event_topic_map(web3) -> dict[str, VaultEventKind]:
    """Build a mapping from topic0 signature to event kind.

    Used by discovery implementations to classify events.

    :return:
        Dict mapping topic0 hex string to VaultEventKind
    """
    from eth_defi.abi import get_topic_signature_from_event

    erc4626_events = get_standard_erc_4626_vault_discovery_events(web3)
    brink_events = get_brink_vault_discovery_events(web3)
    ember_events = get_ember_vault_discovery_events(web3)

    return {
        get_topic_signature_from_event(erc4626_events[0]): VaultEventKind.deposit,
        get_topic_signature_from_event(erc4626_events[1]): VaultEventKind.withdraw,
        get_topic_signature_from_event(brink_events[0]): VaultEventKind.deposit,
        get_topic_signature_from_event(brink_events[1]): VaultEventKind.withdraw,
        get_topic_signature_from_event(ember_events[0]): VaultEventKind.deposit,
        get_topic_signature_from_event(ember_events[1]): VaultEventKind.withdraw,
    }


def is_deposit_event(event_kind: VaultEventKind) -> bool:
    """Check if the event kind represents a deposit."""
    return event_kind == VaultEventKind.deposit


def is_withdraw_event(event_kind: VaultEventKind) -> bool:
    """Check if the event kind represents a withdrawal."""
    return event_kind == VaultEventKind.withdraw


@dataclass(slots=True)
class LeadScanReport:
    """ERC-4626 vault detection data we extract in one duty cycle."""

    #: Any vault-like smart contracts
    leads: dict[HexAddress, PotentialVaultMatch] = dataclasses.field(default_factory=dict)

    #: Confirmed ERC-4626 vaults by smart contract probing calls
    detections: dict[HexAddress, ERC4262VaultDetection] = dataclasses.field(default_factory=dict)

    #: Exported vault-data as rows
    rows: dict[VaultSpec, dict] = dataclasses.field(default_factory=dict)

    #: Accounting / diagnostics
    old_leads: int = 0
    #: Accounting / diagnostics
    new_leads: int = 0
    #: Accounting / diagnostics
    deposits: int = 0
    #: Accounting / diagnostics
    withdrawals: int = 0
    #: Accounting / diagnostics
    backend: "VaultDiscoveryBase | None" = None
    #: Accounting / diagnostics
    start_block: int = 0
    #: Accounting / diagnostics
    end_block: int = 0


class VaultDiscoveryBase(abc.ABC):
    def __init__(
        self,
        max_workers: int,
    ):
        self.max_workers = max_workers
        self.existing_leads = {}

    def seed_existing_leads(self, leads: dict[HexAddress, PotentialVaultMatch]):
        """Seed existing leads to continue the scan where we were left last time."""
        self.existing_leads = leads

    @abstractmethod
    def fetch_leads(
        self,
        start_block: int,
        end_block: int,
        display_progress=True,
    ) -> LeadScanReport:
        pass

    def scan_vaults(
        self,
        start_block: int,
        end_block: int,
        display_progress=True,
    ) -> LeadScanReport:
        """Scan vaults.

        - Detect vault leads by events using :py:meth:`scan_potential_vaults`
        - Then perform multicall probing for each vault smart contract to detect protocol
        """

        chain = self.web3.eth.chain_id

        logger.info("%s.scan_vaults(%d, %d)", self.__class__.__name__, start_block, end_block)

        report = self.fetch_leads(
            start_block,
            end_block,
            display_progress,
        )

        if report is None:
            raise RuntimeError(f"fetch_leads() returned None for {self.__class__.__name__}, start_block={start_block}, end_block={end_block}")

        report.start_block = start_block
        report.end_block = end_block
        assert isinstance(report, LeadScanReport), f"Expected LeadScanReport, got {type(report)}"

        leads = report.leads

        assert type(leads) == dict, f"Expected dict, got {type(leads)}"

        logger.info("Found %d leads", len(leads))
        addresses = list(leads.keys())
        good_vaults = broken_vaults = 0

        if display_progress:
            progress_bar_desc = f"Identifying vaults, using {self.max_workers} workers"
        else:
            progress_bar_desc = None

        # Filter out known bad vaults
        addresses = [a for a in addresses if a.lower() not in BROKEN_VAULT_CONTRACTS]

        for feature_probe in probe_vaults(
            chain,
            self.web3factory,
            addresses,
            block_identifier=end_block,
            max_workers=self.max_workers,
            progress_bar_desc=progress_bar_desc,
        ):
            if feature_probe.address.lower() in BROKEN_VAULT_CONTRACTS:
                logger.warning(f"Skipping known broken vault {feature_probe.address}")

            lead = leads[feature_probe.address]

            detection = ERC4262VaultDetection(
                chain=chain,
                address=feature_probe.address,
                features=feature_probe.features,
                first_seen_at_block=lead.first_seen_at_block,
                first_seen_at=lead.first_seen_at,
                updated_at=native_datetime_utc_now(),
                deposit_count=lead.deposit_count,
                redeem_count=lead.withdrawal_count,
            )
            report.detections[feature_probe.address] = detection

            if ERC4626Feature.broken in feature_probe.features:
                broken_vaults += 1
            else:
                good_vaults += 1

        logger.info(
            "Found %d good ERC-4626 vaults, %d broken vaults",
            good_vaults,
            broken_vaults,
        )

        return report
