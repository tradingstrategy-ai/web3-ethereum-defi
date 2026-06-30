"""Mellow Core Vault adapter.

Mellow Core Vaults are modular asset-management vaults for curated on-chain
yield products. They are not ERC-4626 vault contracts, even though the shared
scanner stores them in the ERC-4626-flavoured detection envelope for pipeline
compatibility.

The current Core Vault factory registry follows Mellow's public Core
deployments for Ethereum mainnet, Plasma, Arbitrum and Monad. Base remains
configuration-only until a canonical Core factory is published.

The canonical adapter address is the Mellow ``Vault`` proxy emitted by the Core
Vault ``Factory.Created`` event. This address coordinates the vault, owns the
component graph and is the address stored in ``VaultSpec`` and
``ERC4262VaultDetection``. It is not the ERC-20 share token address.

Mellow splits vault functionality across several contracts:

- ``Vault``: the central entry point. It combines access control, share module
  lifecycle methods and subvault delegation. In scanner terms it is the vault
  identity.
- ``ShareManager``: share accounting, allocation, whitelist, lockup and
  transfer-control contract. Tokenised variants expose ERC-20 metadata and
  ``totalSupply()``; this initial adapter only supports those tokenised
  managers.
- ``DepositQueue`` and ``SignatureDepositQueue``: per-asset deposit entry
  points. Standard queues are time-buffered and oracle-report settled; signature
  queues allow trusted off-chain approvals. Deposit events are expected here,
  not on the canonical ``Vault`` address.
- ``RedeemQueue`` and ``SignatureRedeemQueue``: per-asset redemption entry
  points. Redemptions are asynchronous, oracle-report settled and can involve
  curator-managed liquidity pulls from subvaults. Redeem events are expected
  here.
- ``Oracle``: vault-coupled price-reporting contract. Reports drive queue
  settlement, fee updates, limits and suspicious-price checks. Mellow's fee docs
  describe a non-ERC-4626 price orientation where reported ``priceD18`` is
  shares per asset, so this adapter does not map it to our normal share price
  column until fixed-block checks pin the orientation.
- ``FeeManager``: deposit, redeem, performance and protocol fee accounting.
  Fees are paid in vault shares, not in underlying assets.
- ``RiskManager``: asset support, deposit limits, balances and pending asset
  accounting across the vault and its subvaults.
- ``Subvault`` and verifiers: controlled execution/custody compartments whose
  external calls are constrained by verifier contracts and access roles.

This differs from ERC-4626 in the important accounting places. A generic
ERC-4626 reader expects the vault contract to expose ``asset()``,
``totalAssets()``, ``convertToAssets()`` and usually the share-token ERC-20
surface. Mellow instead has a separate share manager, multiple queue contracts
and oracle-driven settlement. Calling ERC-4626 methods on the canonical vault
would either fail or describe the wrong abstraction.

Historical reading is therefore Mellow-specific. The first reader only records
tokenised ``ShareManager.totalSupply()`` and writes explicit errors for
``share_price`` and ``total_assets`` until the oracle report ABI, price
orientation, registered assets, pending queue state and subvault balance logic
are verified at fixed blocks. The pipeline stores ``deposit_count=0`` and
``redeem_count=0`` for initial Mellow leads because the canonical vault address
does not emit user flow events; ``ERC4626Feature.mellow_like`` is the explicit
activity-filter exemption that keeps these vaults in downstream scans.

Known unsupported cases:

- Non-tokenised ``BasicShareManager`` contracts.
- Active deposit and redemption transaction execution.
- Queue flow accounting from ``DepositQueue`` and ``RedeemQueue`` contracts.
- Canonical on-chain NAV and ERC-4626-style share price.

Reference material:

- `Mellow Core Vaults documentation <https://docs.mellow.finance/core-vaults>`__
- `Mellow Core deployments <https://docs.mellow.finance/core-vaults/core-deployments>`__
- `Mellow Finance application <https://app.mellow.finance/>`__
- `Lido Earn USD vault on Etherscan <https://etherscan.io/address/0x014e6DA8F283C4aF65B2AA0f201438680A004452>`__
- `Lido Earn USD ShareManager on Etherscan <https://etherscan.io/address/0x4Ce1ac8F43E0E5BD7A346A98aF777bF8fbeA1981>`__
"""

# Adapter classes intentionally mirror :class:`VaultBase` method signatures.
# ruff: noqa: ARG002, FBT001, FBT002, PLC0415, PLR0904, PLR0917, PLR6301

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.mellow.abi import ERC20_ABI, VAULT_ABI
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.vault.base import TradingUniverse, VaultBase, VaultDepositManager, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.lower_case_dict import LowercaseDict

logger = logging.getLogger(__name__)


class MellowVaultUnsupportedError(RuntimeError):
    """Raised when a Mellow feature is not implemented by this adapter."""


class MellowVaultInfo(VaultInfo, total=False):
    """Mellow component graph metadata."""

    #: Canonical Mellow Vault address.
    vault: HexAddress

    #: Tokenised ShareManager address.
    share_manager: HexAddress

    #: FeeManager address, if the call succeeds.
    fee_manager: HexAddress | None

    #: RiskManager address, if the call succeeds.
    risk_manager: HexAddress | None

    #: Oracle address, if the call succeeds.
    oracle: HexAddress | None

    #: Registered asset addresses.
    assets: list[HexAddress]

    #: Deposit queue addresses keyed by asset.
    deposit_queues: dict[HexAddress, list[HexAddress]]

    #: Redeem queue addresses keyed by asset.
    redeem_queues: dict[HexAddress, list[HexAddress]]


@dataclass(slots=True, frozen=True)
class MellowApiVaultMetadata:
    """Optional current Mellow API metadata attached to a vault."""

    #: Public vault name.
    name: str | None = None

    #: Public vault symbol.
    symbol: str | None = None

    #: Current API TVL in USD or denomination-token units if known.
    tvl: Decimal | None = None

    #: Base token address from API/configuration, if known.
    base_token_address: HexAddress | None = None

    #: Base token symbol from API/configuration, if known.
    base_token_symbol: str | None = None

    #: Raw API fields kept for diagnostics.
    raw: dict[str, object] = field(default_factory=dict)


class MellowVault(VaultBase):
    """Mellow Core Vault adapter."""

    def __init__(
        self,
        web3: Web3,
        spec: VaultSpec,
        token_cache: dict | None = None,
        features: set[ERC4626Feature] | None = None,
        default_block_identifier: BlockIdentifier | None = None,
        require_denomination_token: bool = False,
        api_metadata: MellowApiVaultMetadata | None = None,
    ):
        """Create a Mellow vault adapter.

        :param web3:
            Web3 connection.

        :param spec:
            Chain/address vault identity. Address must be the Mellow ``Vault``.

        :param token_cache:
            Token metadata cache.

        :param features:
            Shared scanner feature set. Expected to contain ``mellow_like``.

        :param default_block_identifier:
            Block used for metadata reads.

        :param require_denomination_token:
            Whether missing denomination token should raise through the base
            cached property.

        :param api_metadata:
            Optional offchain Mellow metadata enrichment.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3 = web3
        self.spec = spec
        self.features = features or set()
        self.default_block_identifier = default_block_identifier
        self.api_metadata = api_metadata

    def _get_block_identifier(self) -> BlockIdentifier:
        """Resolve default block identifier for metadata reads.

        :return:
            Configured block identifier or ``latest``.
        """

        return self.default_block_identifier or "latest"

    @property
    def chain_id(self) -> int:
        """Chain id for this vault."""

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Canonical Mellow ``Vault`` address."""

        return HexAddress(Web3.to_checksum_address(self.spec.vault_address))

    @property
    def vault_address(self) -> HexAddress:
        """Canonical Mellow ``Vault`` address.

        ERC-4626 adapters expose this convenience property and shared historical
        scan code still uses it for blacklists and diagnostics. For Mellow this
        is intentionally the Core ``Vault`` proxy, not the ShareManager token.
        """

        return self.address

    @property
    def name(self) -> str:
        """Vault share token name."""

        try:
            return self.share_token.name
        except (MellowVaultUnsupportedError, BadFunctionCallOutput, ContractLogicError, ValueError):
            if self.api_metadata and self.api_metadata.name:
                return self.api_metadata.name
            raise

    @property
    def symbol(self) -> str:
        """Vault share token symbol."""

        try:
            return self.share_token.symbol
        except (MellowVaultUnsupportedError, BadFunctionCallOutput, ContractLogicError, ValueError):
            if self.api_metadata and self.api_metadata.symbol:
                return self.api_metadata.symbol
            raise

    @cached_property
    def vault_contract(self) -> Contract:
        """Mellow ``Vault`` contract with minimal ABI."""

        return self.web3.eth.contract(address=self.address, abi=VAULT_ABI)

    @cached_property
    def share_manager_address(self) -> HexAddress:
        """Fetch the ShareManager address from the vault."""

        address = self.vault_contract.functions.shareManager().call(block_identifier=self._get_block_identifier())
        return HexAddress(Web3.to_checksum_address(address))

    @cached_property
    def share_manager_contract(self) -> Contract:
        """Tokenised ShareManager contract with ERC-20 ABI."""

        return self.web3.eth.contract(address=self.share_manager_address, abi=ERC20_ABI)

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the tokenised ShareManager address.

        :param block_identifier:
            Accepted for compatibility with the shared historical multicaller.

        :return:
            ShareManager address.
        """

        return self.share_manager_address

    def fetch_share_token(self) -> TokenDetails:
        """Fetch tokenised ShareManager ERC-20 metadata.

        :return:
            Share token details.
        """

        token = fetch_erc20_details(
            self.web3,
            self.share_manager_address,
            chain_id=self.chain_id,
            raise_on_error=False,
            cache=self.token_cache,
            cause_diagnostics_message=f"Mellow ShareManager for vault {self.address}",
        )
        if token is None:
            raise MellowVaultUnsupportedError(f"Mellow vault {self.address} ShareManager {self.share_manager_address} is not tokenised or token metadata could not be read")
        return token

    def fetch_denomination_token_address(self) -> HexAddress | None:
        """Fetch the base asset used for initial valuation.

        The initial adapter uses API/configured base token if present, otherwise
        falls back to the first registered asset. Mellow can be multi-asset, so
        all registered assets remain available in :py:meth:`fetch_info`.

        :return:
            Base asset address, or ``None`` if unavailable.
        """

        if self.api_metadata and self.api_metadata.base_token_address:
            return self.api_metadata.base_token_address

        assets = self.fetch_assets()
        if assets:
            return assets[0]
        return None

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Fetch the denomination token metadata.

        :return:
            Token details for the base asset, or ``None``.
        """

        token_address = self.fetch_denomination_token_address()
        if token_address is None:
            return None
        return fetch_erc20_details(
            self.web3,
            token_address,
            chain_id=self.chain_id,
            raise_on_error=False,
            cache=self.token_cache,
            cause_diagnostics_message=f"Mellow denomination token for vault {self.address}",
        )

    def fetch_assets(self) -> list[HexAddress]:
        """Fetch registered asset addresses.

        :return:
            List of registered assets.
        """

        try:
            count = self.vault_contract.functions.getAssetCount().call(block_identifier=self._get_block_identifier())
        except (BadFunctionCallOutput, ContractLogicError, ValueError):
            return []

        assets = []
        for index in range(count):
            asset = self.vault_contract.functions.assetAt(index).call(block_identifier=self._get_block_identifier())
            assets.append(HexAddress(Web3.to_checksum_address(asset)))
        return assets

    def fetch_queues(self, asset: HexAddress) -> tuple[list[HexAddress], list[HexAddress]]:
        """Fetch queues for a registered asset.

        :param asset:
            Registered asset address.

        :return:
            Deposit queues and redeem queues.
        """

        try:
            queue_count = self.vault_contract.functions.getQueueCount(asset).call(block_identifier=self._get_block_identifier())
        except (BadFunctionCallOutput, ContractLogicError, ValueError):
            return [], []

        deposit_queues: list[HexAddress] = []
        redeem_queues: list[HexAddress] = []
        for index in range(queue_count):
            queue = HexAddress(Web3.to_checksum_address(self.vault_contract.functions.queueAt(asset, index).call(block_identifier=self._get_block_identifier())))
            try:
                is_deposit_queue = self.vault_contract.functions.isDepositQueue(queue).call(block_identifier=self._get_block_identifier())
            except (BadFunctionCallOutput, ContractLogicError, ValueError):
                is_deposit_queue = False
            if is_deposit_queue:
                deposit_queues.append(queue)
            else:
                redeem_queues.append(queue)
        return deposit_queues, redeem_queues

    def fetch_info(self) -> MellowVaultInfo:
        """Fetch Mellow component graph metadata.

        :return:
            Component graph metadata.
        """

        def fetch_optional_address(function_name: str) -> HexAddress | None:
            try:
                address = getattr(self.vault_contract.functions, function_name)().call(block_identifier=self._get_block_identifier())
                return HexAddress(Web3.to_checksum_address(address))
            except (BadFunctionCallOutput, ContractLogicError, ValueError, AttributeError):
                return None

        assets = self.fetch_assets()
        deposit_queues = {}
        redeem_queues = {}
        for asset in assets:
            deposit, redeem = self.fetch_queues(asset)
            deposit_queues[asset] = deposit
            redeem_queues[asset] = redeem

        return MellowVaultInfo(
            vault=self.address,
            share_manager=self.share_manager_address,
            fee_manager=fetch_optional_address("feeManager"),
            risk_manager=fetch_optional_address("riskManager"),
            oracle=fetch_optional_address("oracle"),
            assets=assets,
            deposit_queues=deposit_queues,
            redeem_queues=redeem_queues,
        )

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Fetch tokenised ShareManager total supply.

        :param block_identifier:
            Block number or tag.

        :return:
            Human-readable share supply.
        """

        raw_total_supply = self.share_manager_contract.functions.totalSupply().call(block_identifier=block_identifier)
        return self.share_token.convert_to_decimals(raw_total_supply)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Fetch Mellow NAV.

        Full on-chain NAV requires oracle and subvault accounting that is not
        confirmed in the initial adapter.

        :param block_identifier:
            Block number or tag.

        :return:
            Current API TVL if attached, otherwise ``None``.
        """

        if self.api_metadata and self.api_metadata.tvl is not None:
            return self.api_metadata.tvl
        return None

    def fetch_portfolio(
        self,
        universe: TradingUniverse,
        block_identifier: BlockIdentifier | None = None,
    ) -> VaultPortfolio:
        """Fetch a partial portfolio.

        The initial adapter does not reconstruct subvault balances. It returns
        an empty portfolio instead of pretending to know full Mellow holdings.

        :param universe:
            Trading universe.

        :param block_identifier:
            Block number or tag.

        :return:
            Empty portfolio placeholder.
        """

        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Whether queue event scanning is implemented.

        :return:
            ``False`` until Mellow queue flow reader is implemented.
        """

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Whether deposits are automatically distributed to positions.

        :return:
            ``False`` because Mellow deposits settle through queues and
            curator/subvault allocation.
        """

        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Get Mellow flow manager.

        :return:
            Placeholder flow manager that raises for all read methods.
        """

        from eth_defi.mellow.flow import MellowVaultFlowManager

        return MellowVaultFlowManager()

    def get_deposit_manager(self) -> VaultDepositManager:
        """Get active deposit manager.

        :return:
            Never returns until active queue transaction execution is
            implemented.
        """

        message = "Mellow active deposits and redemptions are not implemented"
        raise MellowVaultUnsupportedError(message)

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Get the Mellow historical reader.

        :param stateful:
            Whether to use shared adaptive reader state.

        :return:
            Mellow historical reader.
        """

        from eth_defi.mellow.historical import MellowVaultHistoricalReader

        return MellowVaultHistoricalReader(self, stateful=stateful)

    def get_protocol_name(self) -> str:
        """Return protocol name.

        :return:
            ``Mellow``.
        """

        return "Mellow"

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fetch management-like fee.

        Mellow fee manager semantics need ABI confirmation before mapping to
        annual management-fee percentage.

        :param block_identifier:
            Block number or tag.

        :return:
            ``None``.
        """

        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fetch performance fee.

        Mellow fee manager semantics need ABI confirmation before mapping.

        :param block_identifier:
            Block number or tag.

        :return:
            ``None``.
        """

        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get Mellow vault link.

        :param referral:
            Optional referral code, currently unused.

        :return:
            Mellow app link.
        """

        return f"https://app.mellow.finance/vaults/{self.chain_id}/{self.address}"
