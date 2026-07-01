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
  settlement, fee updates, limits and suspicious-price checks. Mellow reports
  ``priceD18`` as raw shares per raw asset, so this adapter converts it to the
  normal asset-per-share price convention used by the shared vault pipeline.
- ``FeeManager``: deposit, redeem, performance and protocol fee accounting.
  Fees are configured as D6 rates and paid in vault shares, not in underlying
  assets. The shared vault schema does not have a separate protocol-fee column,
  so the adapter maps Mellow's annual time-based ``protocolFeeD6`` to the
  management-fee field and documents it as management-like.
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

Historical reading is therefore Mellow-specific. The first reader records the
tokenised ``ShareManager.totalSupply()`` and reads the latest oracle report for
the denomination asset. Mellow oracle ``priceD18`` is oriented as raw
``shares = assets * priceD18 / 1e18``; the adapter converts it to our
asset-per-share ``VaultHistoricalRead.share_price`` by accounting for the
share-token and denomination-token decimals. The pipeline stores
``deposit_count=0`` and
``redeem_count=0`` for initial Mellow leads because the canonical vault address
does not emit user flow events; ``ERC4626Feature.mellow_like`` is the explicit
activity-filter exemption that keeps these vaults in downstream scans.

Current scan-record TVL uses the same denomination-token accounting convention
as the historical reader: Mellow oracle share price multiplied by tokenised
``ShareManager.totalSupply()``. Public API USD TVL is kept as off-chain
diagnostics and is not used for ``NAV``.

Known unsupported cases:

- Non-tokenised ``BasicShareManager`` contracts.
- Active deposit and redemption transaction execution.
- Queue flow accounting from ``DepositQueue`` and ``RedeemQueue`` contracts.
- Full portfolio composition and subvault-level NAV breakdowns.

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
from dataclasses import dataclass
from decimal import Decimal
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.mellow.abi import ERC20_ABI_FILENAME, FEE_MANAGER_ABI_FILENAME, ORACLE_ABI_FILENAME, VAULT_ABI_FILENAME
from eth_defi.mellow.offchain_metadata import MellowApiVaultMetadata
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.vault.base import TradingUniverse, VaultBase, VaultDepositManager, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import BROKEN_FEE_DATA, FeeData
from eth_defi.vault.lower_case_dict import LowercaseDict

logger = logging.getLogger(__name__)


def convert_mellow_price_d18_to_share_price(
    price_d18: int,
    share_token_decimals: int,
    asset_decimals: int,
) -> Decimal | None:
    """Convert Mellow ``priceD18`` to denomination-token assets per share.

    Mellow Core Vault reports use raw-token accounting:
    ``raw_shares = raw_assets * priceD18 / 1e18``. Our historical vault price
    convention is human-readable denomination-token assets per one
    human-readable share token.

    :param price_d18:
        Raw Mellow oracle ``priceD18`` integer.

    :param share_token_decimals:
        ERC-20 decimals of the tokenised ShareManager.

    :param asset_decimals:
        ERC-20 decimals of the denomination asset in the report.

    :return:
        Human-readable asset amount per one human-readable share, or ``None``
        for a zero oracle price.
    """

    if price_d18 == 0:
        return None

    return Decimal(10) ** (share_token_decimals + 18 - asset_decimals) / Decimal(price_d18)


def convert_mellow_fee_d6_to_percent(fee_d6: int) -> float:
    """Convert Mellow D6 fee rate to fractional percent.

    Mellow stores fee rates as parts-per-million integers:
    ``10_000`` means ``1%``. The shared vault fee interface expects fractional
    values where ``0.01`` means ``1%``.

    :param fee_d6:
        Fee rate in D6 precision.

    :return:
        Fee rate as a fractional percentage.
    """

    return fee_d6 / 1_000_000


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
class MellowOracleReport:
    """Latest Mellow oracle report for an asset."""

    #: Raw Mellow ``priceD18`` value.
    price_d18: int

    #: Unix timestamp stored by the oracle.
    timestamp: int

    #: Whether the report is flagged suspicious by oracle validation.
    is_suspicious: bool


@dataclass(slots=True, frozen=True)
class MellowFeeConfiguration:
    """Mellow FeeManager configuration snapshot."""

    #: Address that receives fee shares.
    fee_recipient: HexAddress

    #: Deposit fee in D6 precision.
    deposit_fee_d6: int

    #: Redeem fee in D6 precision.
    redeem_fee_d6: int

    #: Performance fee in D6 precision.
    performance_fee_d6: int

    #: Annual time-based protocol fee in D6 precision.
    protocol_fee_d6: int

    #: Base asset configured for this vault.
    base_asset: HexAddress

    #: Last FeeManager update timestamp for this vault.
    timestamp: int

    #: Minimum price observed by FeeManager for performance fee accounting.
    min_price_d18: int


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

        return get_deployed_contract(self.web3, VAULT_ABI_FILENAME, self.address)

    @cached_property
    def share_manager_address(self) -> HexAddress:
        """Fetch the ShareManager address from the vault."""

        address = self.vault_contract.functions.shareManager().call(block_identifier=self._get_block_identifier())
        return HexAddress(Web3.to_checksum_address(address))

    @cached_property
    def share_manager_contract(self) -> Contract:
        """Tokenised ShareManager contract with ERC-20 ABI."""

        return get_deployed_contract(self.web3, ERC20_ABI_FILENAME, self.share_manager_address)

    @cached_property
    def oracle_address(self) -> HexAddress:
        """Fetch the Mellow oracle address from the vault."""

        address = self.vault_contract.functions.oracle().call(block_identifier=self._get_block_identifier())
        return HexAddress(Web3.to_checksum_address(address))

    @cached_property
    def oracle_contract(self) -> Contract:
        """Mellow oracle contract with minimal ABI."""

        return get_deployed_contract(self.web3, ORACLE_ABI_FILENAME, self.oracle_address)

    @cached_property
    def fee_manager_address(self) -> HexAddress:
        """Fetch the Mellow FeeManager address from the vault."""

        address = self.vault_contract.functions.feeManager().call(block_identifier=self._get_block_identifier())
        return HexAddress(Web3.to_checksum_address(address))

    @cached_property
    def fee_manager_contract(self) -> Contract:
        """Mellow FeeManager contract with minimal ABI."""

        return get_deployed_contract(self.web3, FEE_MANAGER_ABI_FILENAME, self.fee_manager_address)

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

    def fetch_oracle_report(
        self,
        asset: HexAddress | None = None,
        block_identifier: BlockIdentifier = "latest",
    ) -> MellowOracleReport | None:
        """Fetch the latest Mellow oracle report for an asset.

        :param asset:
            Asset address. Defaults to :py:attr:`denomination_token`.

        :param block_identifier:
            Block number or tag.

        :return:
            Oracle report, or ``None`` if the report cannot be read.
        """

        if asset is None:
            denomination_token = self.denomination_token
            if denomination_token is None:
                return None
            asset = denomination_token.address

        try:
            price_d18, timestamp, is_suspicious = self.oracle_contract.functions.getReport(Web3.to_checksum_address(asset)).call(block_identifier=block_identifier)
        except (BadFunctionCallOutput, ContractLogicError, ValueError):
            return None

        return MellowOracleReport(
            price_d18=int(price_d18),
            timestamp=int(timestamp),
            is_suspicious=bool(is_suspicious),
        )

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Fetch Mellow share price from the oracle report.

        Mellow reports ``priceD18`` as raw shares per raw asset:
        ``shares = assets * priceD18 / 1e18``. This method converts it to the
        shared vault pipeline convention, denomination-token assets per one
        human-readable share token.

        :param block_identifier:
            Block number or tag.

        :return:
            Denomination-token assets per share, or ``None`` if unavailable.
        """

        denomination_token = self.denomination_token
        if denomination_token is None:
            return None

        report = self.fetch_oracle_report(denomination_token.address, block_identifier)
        if report is None or report.is_suspicious:
            return None

        return convert_mellow_price_d18_to_share_price(
            price_d18=report.price_d18,
            share_token_decimals=self.share_token.decimals,
            asset_decimals=denomination_token.decimals,
        )

    def fetch_fee_configuration(self, block_identifier: BlockIdentifier = "latest") -> MellowFeeConfiguration | None:
        """Fetch Mellow FeeManager configuration.

        Mellow stores all configured rates in D6 precision. ``protocolFeeD6`` is
        the annual time-based fee; the adapter maps it to the shared
        management-fee field because the shared schema has no separate protocol
        fee column.

        :param block_identifier:
            Block number or tag.

        :return:
            FeeManager configuration, or ``None`` if the FeeManager cannot be
            read.
        """

        try:
            fee_recipient = self.fee_manager_contract.functions.feeRecipient().call(block_identifier=block_identifier)
            deposit_fee_d6 = self.fee_manager_contract.functions.depositFeeD6().call(block_identifier=block_identifier)
            redeem_fee_d6 = self.fee_manager_contract.functions.redeemFeeD6().call(block_identifier=block_identifier)
            performance_fee_d6 = self.fee_manager_contract.functions.performanceFeeD6().call(block_identifier=block_identifier)
            protocol_fee_d6 = self.fee_manager_contract.functions.protocolFeeD6().call(block_identifier=block_identifier)
            base_asset = self.fee_manager_contract.functions.baseAsset(Web3.to_checksum_address(self.address)).call(block_identifier=block_identifier)
            timestamp = self.fee_manager_contract.functions.timestamps(Web3.to_checksum_address(self.address)).call(block_identifier=block_identifier)
            min_price_d18 = self.fee_manager_contract.functions.minPriceD18(Web3.to_checksum_address(self.address)).call(block_identifier=block_identifier)
        except (BadFunctionCallOutput, ContractLogicError, ValueError):
            return None

        return MellowFeeConfiguration(
            fee_recipient=HexAddress(Web3.to_checksum_address(fee_recipient)),
            deposit_fee_d6=int(deposit_fee_d6),
            redeem_fee_d6=int(redeem_fee_d6),
            performance_fee_d6=int(performance_fee_d6),
            protocol_fee_d6=int(protocol_fee_d6),
            base_asset=HexAddress(Web3.to_checksum_address(base_asset)),
            timestamp=int(timestamp),
            min_price_d18=int(min_price_d18),
        )

    def get_fee_data(self) -> FeeData:
        """Return Mellow fee data.

        Mellow fees are configured through ``FeeManager`` as D6 rates and paid
        in vault shares. ``protocolFeeD6`` is an annual time-based fee, so it is
        mapped to the shared management-fee field. ``performanceFeeD6`` maps to
        the performance-fee field, while deposit and redeem D6 rates map to the
        shared deposit and withdraw fee fields.

        :return:
            Fee data, or ``BROKEN_FEE_DATA`` if the FeeManager cannot be read.
        """

        fee_configuration = self.fetch_fee_configuration(self._get_block_identifier())
        if fee_configuration is None:
            return BROKEN_FEE_DATA

        return FeeData(
            fee_mode=self.get_fee_mode(),
            management=convert_mellow_fee_d6_to_percent(fee_configuration.protocol_fee_d6),
            performance=convert_mellow_fee_d6_to_percent(fee_configuration.performance_fee_d6),
            deposit=convert_mellow_fee_d6_to_percent(fee_configuration.deposit_fee_d6),
            withdraw=convert_mellow_fee_d6_to_percent(fee_configuration.redeem_fee_d6),
        )

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Fetch Mellow-specific private scan row columns.

        ``_mellow_info`` preserves the component graph that the initial
        Mellow-only scan branch exposed before Mellow was moved to the shared
        vault scan path.

        :return:
            Mellow component graph metadata for raw scan rows.
        """

        return {"_mellow_info": self.fetch_info()}

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Fetch Mellow denomination-token TVL from on-chain share accounting.

        Mellow does not expose ERC-4626 ``totalAssets()`` on the canonical
        vault. For comparable scanner output we use the same on-chain
        accounting identity as the historical reader: oracle share price in the
        denomination token multiplied by tokenised ``ShareManager.totalSupply``.
        The public API USD TVL is not used here.

        :param block_identifier:
            Block number or tag.

        :return:
            Human-readable denomination-token TVL, or ``None`` if either price
            or supply is unavailable.
        """

        share_price = self.fetch_share_price(block_identifier)
        total_supply = self.fetch_total_supply(block_identifier)

        if share_price is None or total_supply is None:
            return None

        return share_price * total_supply

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Fetch Mellow NAV.

        ``fetch_nav()`` is kept as the :class:`VaultBase`-compatible alias for
        current scanner reads. It returns the same denomination-token value as
        :py:meth:`fetch_total_assets`, not the public API USD TVL.

        :param block_identifier:
            Block number or tag.

        :return:
            Human-readable denomination-token TVL, or ``None`` if unavailable.
        """

        return self.fetch_total_assets(block_identifier)

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

    def has_custom_fees(self) -> bool:
        """Whether Mellow has fees outside the shared fee model.

        Mellow FeeManager fees are all represented by :class:`FeeData`:
        ``protocolFeeD6`` is management-like, ``performanceFeeD6`` is
        performance-like, and ``depositFeeD6``/``redeemFeeD6`` map to the
        standard deposit/withdraw fields.

        :return:
            ``False`` because no Mellow FeeManager fee is outside the shared
            fee fields.
        """

        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fetch management-like fee.

        Mellow calls this value ``protocolFeeD6``: an annual time-based fee
        charged in vault shares. We expose it through the shared management fee
        column because the generic vault schema has no separate protocol-fee
        field.

        :param block_identifier:
            Block number or tag.

        :return:
            Fractional fee, e.g. ``0.01`` for ``1%``.
        """

        fee_configuration = self.fetch_fee_configuration(block_identifier)
        return convert_mellow_fee_d6_to_percent(fee_configuration.protocol_fee_d6) if fee_configuration else None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fetch performance fee.

        Mellow stores performance fees in D6 precision and charges them in
        vault shares when oracle reports update the FeeManager state.

        :param block_identifier:
            Block number or tag.

        :return:
            Fractional fee, e.g. ``0.15`` for ``15%``.
        """

        fee_configuration = self.fetch_fee_configuration(block_identifier)
        return convert_mellow_fee_d6_to_percent(fee_configuration.performance_fee_d6) if fee_configuration else None

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fetch deposit fee.

        Mellow stores deposit fees in D6 precision and charges them in vault
        shares during deposit queue report handling.

        :param block_identifier:
            Block number or tag.

        :return:
            Fractional fee, e.g. ``0.005`` for ``0.5%``.
        """

        fee_configuration = self.fetch_fee_configuration(block_identifier)
        return convert_mellow_fee_d6_to_percent(fee_configuration.deposit_fee_d6) if fee_configuration else None

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fetch redeem fee.

        Mellow names this value ``redeemFeeD6``. The shared vault interface uses
        the withdraw fee field for the same user-facing redemption charge.

        :param block_identifier:
            Block number or tag.

        :return:
            Fractional fee, e.g. ``0.003`` for ``0.3%``.
        """

        fee_configuration = self.fetch_fee_configuration(block_identifier)
        return convert_mellow_fee_d6_to_percent(fee_configuration.redeem_fee_d6) if fee_configuration else None

    def get_link(self, referral: str | None = None) -> str:
        """Get Mellow vault link.

        :param referral:
            Optional referral code, currently unused.

        :return:
            Mellow app link.
        """

        return f"https://app.mellow.finance/vaults/{self.chain_id}/{self.address}"
