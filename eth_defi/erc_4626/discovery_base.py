"""Events we use in the vault discovery.

- Shared across RPC/Hypersync discovery
- Supports standard ERC-4626 Deposit/Withdraw events
- Supports BrinkVault DepositFunds/WithdrawFunds events
- Supports EmberVault VaultDeposit/RequestRedeemed events
- Supports TokenGateway Deposit(5-arg)/RedeemRequested/RedeemTokenGatewayDepreciated events
- Supports Royco tranche Redeem event
- Supports Upshift multi-asset Deposit/WithdrawalRequested/WithdrawalProcessed events
- Supports Atoma WithdrawalClaimed events
- Supports T3tris DepositRequest/RedeemRequest events
- Supports Securitize DSToken Issue events
"""

import abc
import dataclasses
import datetime
import enum
import logging
from abc import abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Type, TypeAlias

from eth_typing import HexAddress
from web3.contract.contract import ContractEvent

from eth_defi.abi import get_contract
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import ODA_FACT_HARDCODED_LEADS, probe_vaults
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature, get_erc_4626_contract
from eth_defi.maseer_one.constants import MASEER_ONE_HARDCODED_LEADS
from eth_defi.midas.constants import MIDAS_HARDCODED_LEADS
from eth_defi.tokenised_fund.asseto.constants import ASSETO_HARDCODED_LEADS
from eth_defi.tokenised_fund.franklin.constants import FRANKLIN_HARDCODED_LEADS
from eth_defi.tokenised_fund.ondo.constants import ONDO_HARDCODED_LEADS
from eth_defi.tokenised_fund.usyc.constants import USYC_HARDCODED_LEADS
from eth_defi.tokenised_fund.centrifuge.constants import CENTRIFUGE_TRANCHE_HARDCODED_LEADS
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.risk import BROKEN_VAULT_CONTRACTS
from eth_defi.vault_street.constants import VAULT_STREET_HARDCODED_LEADS

logger = logging.getLogger(__name__)

HardcodedVaultLead: TypeAlias = tuple[int, HexAddress, int, datetime.datetime]
HardcodedVaultLeadSource: TypeAlias = tuple[str, tuple[HardcodedVaultLead, ...]]
HardcodedVaultLeadSources: TypeAlias = tuple[HardcodedVaultLeadSource, ...]

#: Protocol deployments that cannot be discovered from supported vault events.
DEFAULT_HARDCODED_VAULT_LEAD_SOURCES: HardcodedVaultLeadSources = (
    ("ODA-FACT", ODA_FACT_HARDCODED_LEADS),
    ("Midas", MIDAS_HARDCODED_LEADS),
    ("Maseer One", MASEER_ONE_HARDCODED_LEADS),
    ("Vault Street", VAULT_STREET_HARDCODED_LEADS),
    ("Ondo", ONDO_HARDCODED_LEADS),
    ("Circle USYC", USYC_HARDCODED_LEADS),
    ("Franklin Templeton", FRANKLIN_HARDCODED_LEADS),
    ("Centrifuge Tranche", CENTRIFUGE_TRANCHE_HARDCODED_LEADS),
)

if TYPE_CHECKING:
    from eth_defi.mellow.discovery import MellowFactoryCandidate


class VaultEventKind(enum.Enum):
    """Classify vault discovery events by their type."""

    #: Deposit-like event (ERC-4626 Deposit or BrinkVault DepositFunds)
    deposit = "deposit"

    #: Withdraw-like event (ERC-4626 Withdraw or BrinkVault WithdrawFunds)
    withdraw = "withdraw"


@dataclasses.dataclass(slots=True, frozen=False)
class PotentialVaultMatch:
    """Categorise contracts that emit vault discovery events."""

    chain: int
    address: HexAddress
    first_seen_at_block: int
    first_seen_at: datetime.datetime
    deposit_count: int = 0
    withdrawal_count: int = 0
    #: Mellow ``Factory.Created`` metadata when this lead came from a Mellow
    #: factory event instead of vault-local deposit/withdraw events.
    #:
    #: Mellow Core Vault user flow events are emitted by queue contracts, not by
    #: the canonical Vault. Keep this metadata on the normal lead object so the
    #: discovery path can still use one lead map and one feature-probe loop.
    mellow_factory_candidate: "MellowFactoryCandidate | None" = None

    def is_candidate(self) -> bool:
        # Compatibility shim: older persisted lead objects may not have this slot; remove after reader state migration.
        if getattr(self, "mellow_factory_candidate", None) is not None:
            return True

        # Deposit-only event streams are valid vault leads.
        # Large curated vaults can have deposits but no withdrawals yet because
        # they are in a pre-deposit phase, have an initial lock-up, or use a
        # delayed withdrawal process. Requiring withdrawal events made us miss
        # RockawayX/Upshift vaults such as Tori Ecosystem Vault and Earn ctUSD.
        # Extra deposit-only matches are still filtered by the later
        # ``probe_vaults()`` feature-detection stage before export.
        return self.deposit_count > 0


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


def get_token_gateway_event_contract(web3):
    """Get ITokenGatewayEvents interface for TokenGateway events.

    TokenGateway (ForgeYieldsUSDC / fyUSDC) uses non-standard ERC-4626 flow events:

    - ``Deposit(address indexed sender, address indexed owner, uint256 assets, uint256 shares, uint256 referralCode)`` — 5-arg deposit with referral code
    - ``RedeemRequested(address indexed owner, address indexed receiver, uint256 shares, uint256 assets, uint256 id, uint256 epoch)`` — async redemption request
    - ``RedeemTokenGatewayDepreciated(address indexed caller, address indexed receiver, uint256 shares, uint256 assets)`` — deprecated direct redeem

    `Etherscan link <https://etherscan.io/address/0x943109DC7C950da4592d85ebd4Cfed007Af64670>`_.
    """
    return get_contract(
        web3,
        "token_gateway/ITokenGatewayEvents.json",
    )


def get_royco_tranche_event_contract(web3):
    """Get Royco tranche interface for custom redemption events.

    Royco senior/junior tranche vaults use the standard ERC-4626 ``Deposit``
    event topic, but do not emit the standard ERC-4626 ``Withdraw`` event.
    Instead, redemptions use:

    - ``Redeem(address indexed sender, address indexed receiver, (uint256,uint256,uint256) claims, uint256 shares)``

    `Etherscan example <https://etherscan.io/address/0x1ba515a409dd702105415cdaae439059aa0b402a>`__.
    """
    return get_contract(
        web3,
        "royco/RoycoSeniorTranche.json",
    )


def get_upshift_multi_asset_event_contract(web3):
    """Get Upshift multi-asset vault interface for custom flow events.

    Upshift ``multiAssetVault`` contracts can accept multiple deposit assets and
    therefore do not emit the standard ERC-4626
    ``Deposit(address,address,uint256,uint256)`` event. Their vault-local
    deposit event is:

    - ``Deposit(address assetIn, uint256 amountIn, uint256 shares, address indexed senderAddr, address indexed receiverAddr)``
    - ``WithdrawalRequested(uint256 shares, address indexed holderAddr, address indexed receiverAddr)``
    - ``WithdrawalProcessed(uint256 assetsAmount, address indexed receiverAddr)``

    The deposit event topic is
    ``0xc436f473cd90c9b4dd731856a14b80f713d384a1688a506d4230140c5b36d5cd``.
    This was observed on Ethereum mainnet Upshift/RockawayX vaults:

    - `Tori Ecosystem Vault on Etherscan <https://etherscan.io/address/0xcd69123b3FBBfC666E1f6a501da27B564C00De54>`__
    - `Earn ctUSD on Etherscan <https://etherscan.io/address/0xc87DBBB8C67e4F19fCD2E297c05937567b2572Ce>`__
    - `Shared implementation on Etherscan <https://etherscan.io/address/0xEB5f80aCEa6060764E91c185bE93752Ab40F01c2#code>`__
    - `Upshift API reference <https://docs.upshift.finance/developer-docs/api-reference>`__

    Both vault addresses are TransparentUpgradeableProxy contracts whose public
    explorer ABI exposes only proxy events. We keep this event-only ABI so lead
    discovery can match the implementation-level log topic emitted at the proxy
    address without depending on a single implementation address.
    """
    return get_contract(
        web3,
        "upshift/IMultiAssetVaultEvents.json",
    )


def get_atoma_vault_event_contract(web3):
    """Get Atoma vault interface for custom flow events.

    Atoma's Arbitrum vault emits the standard ERC-4626 ``Deposit`` event for
    deposits, but direct ``withdraw()``/``redeem()`` revert with
    ``UseRequestWithdrawal``. Redemptions use an epoch-based request/claim flow:

    - ``WithdrawalRequested(address indexed user, uint256 shares, uint256 requestEpoch, uint256 settlementEpoch)``
    - ``WithdrawalClaimed(address indexed user, uint256 indexed epochId, uint256 shares, uint256 assets, uint256 fee)``

    Verified source: https://arbitrum.blockscout.com/address/0xd4242FD8DE6E3128f0435b52DCe29155098CbBFF
    Proxy vault: https://arbiscan.io/address/0xCC56410e1a136aF0eCEb7241c6aE394F4d8b581c
    """
    return get_contract(
        web3,
        "atoma/IAtomaVaultEvents.json",
    )


def get_t3tris_vault_event_contract(web3):
    """Get T3tris vault interface for custom flow events.

    T3tris vaults support the standard ERC-4626 ``Deposit`` and ``Withdraw``
    events in open vault mode. Closed or asynchronous vault mode can instead
    emit ERC-7540-like request events before any standard claim event appears:

    - ``DepositRequest(address indexed receiver, address indexed owner, uint256 indexed requestId, address sender, uint256 assets)``
    - ``RedeemRequest(address indexed receiver, address indexed owner, uint256 indexed requestId, address sender, uint256 shares)``

    ABI source notes: ``eth_defi/abi/t3tris/README.md``.
    """
    return get_contract(
        web3,
        "t3tris/IVault.json",
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


def get_token_gateway_discovery_events(web3) -> list[Type[ContractEvent]]:
    """Get list of TokenGateway events we use in vault discovery.

    TokenGateway uses non-standard ERC-4626 flow events:

    - ``Deposit(address indexed sender, address indexed owner, uint256 assets, uint256 shares, uint256 referralCode)`` — deposit with referral code (distinct topic from standard ERC-4626 Deposit)
    - ``RedeemRequested(address indexed owner, address indexed receiver, uint256 shares, uint256 assets, uint256 id, uint256 epoch)`` — async redemption request
    - ``RedeemTokenGatewayDepreciated(address indexed caller, address indexed receiver, uint256 shares, uint256 assets)`` — deprecated direct redeem
    """
    ITokenGatewayEvents = get_token_gateway_event_contract(web3)
    return [
        ITokenGatewayEvents.events.Deposit,
        ITokenGatewayEvents.events.RedeemRequested,
        ITokenGatewayEvents.events.RedeemTokenGatewayDepreciated,
    ]


def get_royco_tranche_discovery_events(web3) -> list[Type[ContractEvent]]:
    """Get Royco tranche events we use in vault discovery.

    Royco tranche vaults are almost ERC-4626:

    - ``Deposit(address,address,uint256,uint256)`` matches standard ERC-4626
    - ``Redeem(address,address,(uint256,uint256,uint256),uint256)`` replaces
      the standard ``Withdraw`` event

    Only the custom redemption event is returned here because the deposit event
    is already covered by :py:func:`get_standard_erc_4626_vault_discovery_events`.
    """
    IRoycoTranche = get_royco_tranche_event_contract(web3)
    return [
        IRoycoTranche.events.Redeem,
    ]


def get_upshift_multi_asset_discovery_events(web3) -> list[Type[ContractEvent]]:
    """Get Upshift multi-asset events we use in vault discovery.

    Upshift has two vault families relevant for discovery:

    - Older TokenizedAccount/ERC-4626-like vaults, such as Upshift AZT, emit
      the standard ERC-4626 ``Deposit``/``Withdraw`` topics and are already
      covered by :py:func:`get_standard_erc_4626_vault_discovery_events`.
    - Newer ``multiAssetVault`` vaults emit a custom multi-asset ``Deposit``
      topic because the event needs to include ``assetIn``. Some large vaults
      have not emitted withdrawal events yet due to lock-up/pre-deposit
      mechanics, so the deposit event alone must be enough to seed a lead.
      When withdrawal request/processed events exist, we still scan them to
      keep the diagnostic redemption counter useful.

    `Upshift vault API <https://api.upshift.finance/v1/tokenized_vaults/0xcd69123b3FBBfC666E1f6a501da27B564C00De54>`__
    reports Tori Ecosystem Vault as ``internal_type=multiAssetVault``.

    :return:
        List of Upshift multi-asset event types used for lead discovery.
    """
    IUpshiftMultiAssetVaultEvents = get_upshift_multi_asset_event_contract(web3)
    return [
        IUpshiftMultiAssetVaultEvents.events.Deposit,
        IUpshiftMultiAssetVaultEvents.events.WithdrawalRequested,
        IUpshiftMultiAssetVaultEvents.events.WithdrawalProcessed,
    ]


def get_atoma_vault_discovery_events(web3) -> list[Type[ContractEvent]]:
    """Get Atoma custom events we use in vault discovery.

    Deposits are covered by the standard ERC-4626 ``Deposit`` event. Atoma's
    withdrawal flow is asynchronous and emits custom request/claim events.

    We only count ``WithdrawalClaimed`` as a redemption-like discovery event.
    Counting both ``WithdrawalRequested`` and ``WithdrawalClaimed`` would double
    count completed withdrawals, and would count requested-but-unclaimed shares
    as redeemed before the USDC payout exists.

    :return:
        List of Atoma custom event types used for lead discovery.
    """
    IAtomaVaultEvents = get_atoma_vault_event_contract(web3)
    return [
        IAtomaVaultEvents.events.WithdrawalClaimed,
    ]


def get_t3tris_vault_discovery_events(web3) -> list[type[ContractEvent]]:
    """Get T3tris custom events we use in vault discovery.

    Standard ERC-4626 ``Deposit`` and ``Withdraw`` events are already covered
    by :py:func:`get_standard_erc_4626_vault_discovery_events`. These request
    events allow the lead scanner to find T3tris vaults that are operating in
    asynchronous mode before claims emit the standard flow events.

    :return:
        List of T3tris custom event types used for lead discovery.
    """
    t3tris_vault_events = get_t3tris_vault_event_contract(web3)
    return [
        t3tris_vault_events.events.DepositRequest,
        t3tris_vault_events.events.RedeemRequest,
    ]


def get_securitize_dstoken_discovery_events(web3) -> list[Type[ContractEvent]]:
    """Get Securitize DSToken issuance events used for lead discovery.

    DSTokens are non-ERC-4626 security tokens. Their ``Issue`` event identifies
    token issuance and gives the scanner a candidate lead; ABI probes
    subsequently reject unrelated contracts that happen to emit a similarly
    shaped event. Issuance is not necessarily a cash subscription.

    :param web3:
        Web3 connection used to construct the event ABI.
    :return:
        The DSToken ``Issue`` event type.
    """

    dstoken_contract = web3.eth.contract(
        abi=[
            {
                "anonymous": False,
                "inputs": [
                    {"indexed": True, "internalType": "address", "name": "to", "type": "address"},
                    {"indexed": False, "internalType": "uint256", "name": "value", "type": "uint256"},
                    {"indexed": False, "internalType": "uint256", "name": "valueLocked", "type": "uint256"},
                ],
                "name": "Issue",
                "type": "event",
            }
        ]
    )
    return [dstoken_contract.events.Issue]


def get_vault_discovery_events(web3) -> list[Type[ContractEvent]]:
    """Get all events used in vault discovery, including protocol-specific ones.

    This includes:
    - Standard ERC-4626 Deposit/Withdraw events
    - BrinkVault DepositFunds/WithdrawFunds events
    - EmberVault VaultDeposit/RequestRedeemed events
    - TokenGateway Deposit(5-arg)/RedeemRequested/RedeemTokenGatewayDepreciated events
    - Royco tranche Redeem event
    - Upshift multi-asset Deposit/WithdrawalRequested/WithdrawalProcessed events
    - Atoma WithdrawalClaimed event
    - T3tris DepositRequest/RedeemRequest events
    - Securitize DSToken Issue event

    :return:
        List of contract event types in order:
        [ERC4626.Deposit, ERC4626.Withdraw, BrinkVault.Deposited, BrinkVault.Withdrawal,
         EmberVault.VaultDeposit, EmberVault.RequestRedeemed,
         TokenGateway.Deposit, TokenGateway.RedeemRequested, TokenGateway.RedeemTokenGatewayDepreciated,
         RoycoTranche.Redeem,
         UpshiftMultiAsset.Deposit, UpshiftMultiAsset.WithdrawalRequested,
         UpshiftMultiAsset.WithdrawalProcessed,
         AtomaVault.WithdrawalClaimed,
         T3trisVault.DepositRequest, T3trisVault.RedeemRequest,
         SecuritizeDSToken.Issue]
    """
    return get_standard_erc_4626_vault_discovery_events(web3) + get_brink_vault_discovery_events(web3) + get_ember_vault_discovery_events(web3) + get_token_gateway_discovery_events(web3) + get_royco_tranche_discovery_events(web3) + get_upshift_multi_asset_discovery_events(web3) + get_atoma_vault_discovery_events(web3) + get_t3tris_vault_discovery_events(web3) + get_securitize_dstoken_discovery_events(web3)


def get_vault_event_topic_map(web3) -> dict[str, VaultEventKind]:
    """Build a mapping from topic0 signature to event kind.

    Used by discovery implementations to classify events.

    :return:
        Dict mapping topic0 hex string to VaultEventKind
    """
    from eth_defi.abi import get_topic_signature_from_event

    event_groups = (
        (get_standard_erc_4626_vault_discovery_events(web3), (VaultEventKind.deposit, VaultEventKind.withdraw)),
        (get_brink_vault_discovery_events(web3), (VaultEventKind.deposit, VaultEventKind.withdraw)),
        (get_ember_vault_discovery_events(web3), (VaultEventKind.deposit, VaultEventKind.withdraw)),
        (get_token_gateway_discovery_events(web3), (VaultEventKind.deposit, VaultEventKind.withdraw, VaultEventKind.withdraw)),
        (get_royco_tranche_discovery_events(web3), (VaultEventKind.withdraw,)),
        (get_upshift_multi_asset_discovery_events(web3), (VaultEventKind.deposit, VaultEventKind.withdraw, VaultEventKind.withdraw)),
        (get_atoma_vault_discovery_events(web3), (VaultEventKind.withdraw,)),
        (get_t3tris_vault_discovery_events(web3), (VaultEventKind.deposit, VaultEventKind.withdraw)),
        (get_securitize_dstoken_discovery_events(web3), (VaultEventKind.deposit,)),
    )
    return {get_topic_signature_from_event(event): event_kind for events, event_kinds in event_groups for event, event_kind in zip(events, event_kinds, strict=True)}


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


def _prepare_probe_leads(leads: dict[HexAddress, PotentialVaultMatch]) -> tuple[list[HexAddress], dict[str, PotentialVaultMatch], int]:
    """Prepare lead data for the shared feature-probe pass.

    :param leads:
        Vault leads keyed by emitting contract address or canonical Mellow
        Vault address.

    :return:
        Probe addresses, lower-case lead lookup and Mellow factory lead count.
    """

    addresses = []
    leads_by_address = {}
    seen_addresses = set()
    mellow_lead_count = 0
    for address, lead in leads.items():
        lowered_address = address.lower()
        leads_by_address[lowered_address] = lead

        # Compatibility shim: older persisted lead objects may not have this slot; remove after reader state migration.
        if getattr(lead, "mellow_factory_candidate", None) is not None:
            mellow_lead_count += 1

        if lowered_address in BROKEN_VAULT_CONTRACTS or lowered_address in seen_addresses:
            continue
        addresses.append(address)
        seen_addresses.add(lowered_address)

    return addresses, leads_by_address, mellow_lead_count


def create_mellow_potential_vault_match(candidate: "MellowFactoryCandidate") -> PotentialVaultMatch:
    """Create a normal lead object from a Mellow factory candidate.

    :param candidate:
        Decoded Mellow ``Factory.Created`` log.

    :return:
        Lead compatible with the shared ``probe_vaults()`` path.
    """

    return PotentialVaultMatch(
        chain=candidate.chain,
        address=candidate.address,
        first_seen_at_block=candidate.created_block,
        first_seen_at=candidate.created_at,
        # Mellow flow events are emitted by DepositQueue contracts, not by the
        # canonical Vault address discovered from Factory.Created. True flow
        # counts need a second-stage queue scan; initial discovery and price
        # scanning intentionally use feature-based activity-filter exemption.
        deposit_count=0,
        # Mellow redemption events are emitted by RedeemQueue contracts. Keep
        # an integer zero for compatibility with numeric consumers;
        # ERC4626Feature.mellow_like prevents these rows from being silently
        # filtered out as inactive.
        withdrawal_count=0,
        mellow_factory_candidate=candidate,
    )


def add_mellow_factory_candidate_lead(
    report: LeadScanReport,
    leads: dict[HexAddress, PotentialVaultMatch],
    candidate: "MellowFactoryCandidate",
) -> None:
    """Add a Mellow factory candidate to the shared lead map if new.

    :param report:
        Mutable scan report whose counters are updated.

    :param leads:
        Mutable lead map.

    :param candidate:
        Decoded Mellow ``Factory.Created`` candidate.
    """

    key = HexAddress(candidate.address.lower())
    if key not in leads:
        leads[key] = create_mellow_potential_vault_match(candidate)
        report.new_leads += 1


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
        hardcoded_lead_sources: HardcodedVaultLeadSources | None = None,
    ) -> LeadScanReport:
        """Scan vaults.

        - Detect vault leads by events using :py:meth:`scan_potential_vaults`
        - Then perform multicall probing for each vault smart contract to detect protocol

        :param hardcoded_lead_sources:
            Protocol-labelled deployments that cannot be discovered from
            supported vault events. Uses the production protocol set by default.
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

        if hardcoded_lead_sources is None:
            hardcoded_lead_sources = DEFAULT_HARDCODED_VAULT_LEAD_SOURCES

        for protocol_name, protocol_leads in hardcoded_lead_sources:
            for lead_chain, address, first_seen_at_block, first_seen_at in protocol_leads:
                if lead_chain != chain or end_block < first_seen_at_block or address in leads:
                    continue

                leads[address] = PotentialVaultMatch(
                    chain=chain,
                    address=address,
                    first_seen_at_block=first_seen_at_block,
                    first_seen_at=first_seen_at,
                    deposit_count=0,
                    withdrawal_count=0,
                )
                report.new_leads += 1
                logger.info("Added hardcoded %s vault lead %s", protocol_name, address)

        for lead_chain, address, first_seen_at_block, first_seen_at in ASSETO_HARDCODED_LEADS:
            if lead_chain != chain or end_block < first_seen_at_block:
                continue
            if address not in leads:
                leads[address] = PotentialVaultMatch(
                    chain=chain,
                    address=address,
                    first_seen_at_block=first_seen_at_block,
                    first_seen_at=first_seen_at,
                    deposit_count=0,
                    withdrawal_count=0,
                )
                report.new_leads += 1
                logger.info("Added hardcoded Asseto vault lead %s", address)

        addresses, leads_by_address, mellow_lead_count = _prepare_probe_leads(leads)
        logger.info("Found %d vault leads, of which %d are Mellow factory leads", len(leads), mellow_lead_count)
        good_vaults = broken_vaults = 0

        if display_progress:
            progress_bar_desc = f"Identifying vaults, using {self.max_workers} workers"
        else:
            progress_bar_desc = None

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
                continue

            address_key = feature_probe.address.lower()
            lead = leads_by_address[address_key]
            # Compatibility shim: older persisted lead objects may not have this slot; remove after reader state migration.
            candidate = getattr(lead, "mellow_factory_candidate", None)
            if candidate is not None:
                features = set(feature_probe.features)
                features.discard(ERC4626Feature.broken)
                features.add(ERC4626Feature.mellow_like)

                detection = ERC4262VaultDetection(
                    chain=chain,
                    address=feature_probe.address,
                    features=features,
                    first_seen_at_block=candidate.created_block,
                    first_seen_at=candidate.created_at,
                    updated_at=native_datetime_utc_now(),
                    # Mellow flow events are emitted by DepositQueue contracts,
                    # not by the canonical Vault address discovered from
                    # Factory.Created. True flow counts need a second-stage
                    # queue scan; initial discovery and price scanning
                    # intentionally use feature-based activity-filter exemption
                    # instead.
                    deposit_count=0,
                    # Mellow redemption events are emitted by RedeemQueue
                    # contracts. Keep an integer zero for compatibility with
                    # numeric consumers; ERC4626Feature.mellow_like prevents
                    # these rows from being silently filtered out as inactive.
                    redeem_count=0,
                )
                report.detections[feature_probe.address] = detection
                good_vaults += 1
                continue

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
            "Found %d good ERC-4626/Mellow vaults, %d broken vaults",
            good_vaults,
            broken_vaults,
        )

        return report
