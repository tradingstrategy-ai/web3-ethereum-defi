"""Direct :class:`VaultBase` adapter for Enzyme Blue VaultProxy funds.

Blue predates ERC-4626.  Its VaultProxy is the share token and delegates fund
valuation to the paired ComptrollerProxy.  This adapter exposes the pair as a
single scanner vault while keeping the canonical VaultProxy address as its
identity.

See https://docs.enzyme.finance/enzyme-blue-protocol/architecture/persistent.
"""

# ruff: noqa: FBT001, FBT002, PLR0904, PLR0917, PLR6301

from decimal import Decimal
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import ABIFunctionNotFound, BadFunctionCallOutput, ContractLogicError

from eth_defi.abi import ZERO_ADDRESS, get_deployed_contract
from eth_defi.enzyme.blue_discovery import ENZYME_BLUE_DEPLOYMENTS
from eth_defi.enzyme.blue_historical import EnzymeBlueVaultHistoricalReader
from eth_defi.enzyme.fee import combine_user_facing_management_fee
from eth_defi.enzyme.offchain_metadata import create_enzyme_vault_link, load_enzyme_blue_vault_metadata
from eth_defi.enzyme.onyx_flow import EnzymeVaultFlowManager
from eth_defi.enzyme.tags import get_strategy_tags as lookup_strategy_tags
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultBase, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositManager
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.lower_case_dict import LowercaseDict
from eth_defi.vault.strategy_tag import StrategyTag

MANAGEMENT_FEE_RATE_SCALE = Decimal(10**27)
FEE_BPS_DENOMINATOR = Decimal(10_000)
SECONDS_PER_YEAR = Decimal(365 * 24 * 60 * 60)

#: Reviewed policy identifiers that restrict who may deposit into Blue.
#:
#: Sulu uses ``ALLOWED_DEPOSIT_RECIPIENTS``. Deprecated Phoenix and Encore
#: releases used the equivalent investor/depositor whitelist and caller
#: whitelist policies. Enzyme's website exposes these as
#: ``DepositorWhitelist`` and ``BuySharesCallerWhitelist`` respectively.
ENZYME_BLUE_DEPOSIT_PERMISSION_POLICY_IDENTIFIERS = frozenset(
    {
        "ALLOWED_DEPOSIT_RECIPIENTS",
        "BUY_SHARES_CALLER_WHITELIST",
        "DEPOSITOR_WHITELIST",
        "INVESTOR_WHITELIST",
    }
)

#: Backwards-compatible public constant for the current Sulu identifier.
ALLOWED_DEPOSIT_RECIPIENTS_POLICY_IDENTIFIER = "ALLOWED_DEPOSIT_RECIPIENTS"

#: Deprecated Ethereum releases do not expose ``getPolicyManager()`` through
#: their Comptroller ABI. Resolve their reviewed manager from the current
#: FundDeployer recorded by the persistent Dispatcher. Source:
#: https://github.com/enzymefinance/sdk/blob/main/packages/environment/src/deployments/ethereum.ts
ENZYME_BLUE_LEGACY_POLICY_MANAGERS: dict[tuple[int, str], HexAddress] = {
    (1, "0x7e6d3b1161df9c9c7527f68d651b297d2fdb820b"): HexAddress("0x0bd9f0465d21d4c300c7b8d781a013bdc87a31e8"),
    (1, "0x9134c9975244b46692ad9a7da36dba8734ec6da3"): HexAddress("0x4c2c07b15b0b32bad989d9defaec775e2aa8a7ad"),
}


class EnzymeBlueVault(VaultBase):
    """Read an Enzyme Blue VaultProxy and its paired ComptrollerProxy."""

    def __init__(
        self,
        web3: Web3,
        spec: VaultSpec,
        token_cache: dict | None = None,
        features: set[ERC4626Feature] | None = None,
        default_block_identifier: BlockIdentifier | None = None,
        require_denomination_token: bool = False,
    ):
        """Create a scanner adapter for a canonical Blue VaultProxy.

        :param web3: Web3 connection for the vault's chain.
        :param spec: Chain and VaultProxy address.
        :param token_cache: Shared ERC-20 metadata cache.
        :param features: Scanner feature flags.
        :param default_block_identifier: Optional point-in-time metadata block.
        :param require_denomination_token: Base-class compatibility option.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3 = web3
        self.spec = spec
        self.default_block_identifier = default_block_identifier
        self.api_metadata = load_enzyme_blue_vault_metadata(spec.chain_id, spec.vault_address)
        del features

    def _get_block_identifier(self) -> BlockIdentifier:
        """Return the configured metadata block or latest."""

        return self.default_block_identifier or "latest"

    @property
    def chain_id(self) -> int:
        """Return this vault's EVM chain id."""

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the canonical VaultProxy/share-token address."""

        return HexAddress(Web3.to_checksum_address(self.spec.vault_address))

    @property
    def vault_address(self) -> HexAddress:
        """Return scanner-compatible alias for :py:attr:`address`."""

        return self.address

    @cached_property
    def vault_contract(self) -> Contract:
        """Load VaultLib ABI at the proxy address."""

        return get_deployed_contract(self.web3, "enzyme/VaultLib.json", self.address)

    @cached_property
    def comptroller_contract(self) -> Contract:
        """Resolve and load the currently paired ComptrollerProxy."""

        accessor = self.vault_contract.functions.getAccessor().call(block_identifier=self._get_block_identifier())
        return get_deployed_contract(self.web3, "enzyme/ComptrollerLib.json", accessor)

    @cached_property
    def denomination_token(self) -> TokenDetails:
        """Fetch the current denominator ERC-20 token details."""

        token = fetch_erc20_details(
            self.web3,
            self.fetch_denomination_token_address(),
            chain_id=self.chain_id,
            raise_on_error=True,
            cache=self.token_cache,
            cause_diagnostics_message=f"Enzyme Blue vault {self.address}",
        )
        assert token is not None
        return token

    @property
    def name(self) -> str:
        """Return the cached VaultProxy ERC-20 name."""

        return self.share_token.name or ""

    @property
    def symbol(self) -> str:
        """Return the cached VaultProxy ERC-20 symbol."""

        return self.share_token.symbol or ""

    @property
    def description(self) -> str | None:
        """Return optional official offchain listing copy for this Blue vault."""

        return self.api_metadata.description if self.api_metadata else None

    @property
    def short_description(self) -> str | None:
        """Return optional official offchain table copy for this Blue vault."""

        return self.api_metadata.short_description if self.api_metadata else None

    def get_strategy_tags(self) -> set[StrategyTag] | None:
        """Return documented strategy tags for this Blue vault.

        The shared Enzyme mapping keys classifications by canonical share-token
        address. Unmapped addresses intentionally return ``None`` because the
        protocol's generic metadata does not establish an investment strategy.

        :return:
            A mutable tag set for a researched vault, or ``None`` when its
            strategy remains undocumented.
        """

        return lookup_strategy_tags(self.address)

    @property
    def manager_name(self) -> str | None:
        """Return optional official manager name."""

        return self.api_metadata.manager_name if self.api_metadata else None

    def fetch_share_token(self) -> TokenDetails:
        """Fetch the VaultProxy ERC-20 share token."""

        token = fetch_erc20_details(self.web3, self.address, chain_id=self.chain_id, raise_on_error=True, cache=self.token_cache)
        assert token is not None
        return token

    @cached_property
    def share_token(self) -> TokenDetails:
        """Return cached VaultProxy share-token metadata."""

        return self.fetch_share_token()

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the immutable VaultProxy share-token address."""

        del block_identifier
        return self.address

    def fetch_denomination_token_address(self) -> HexAddress:
        """Read the current ComptrollerProxy denomination token."""

        return HexAddress(self.comptroller_contract.functions.getDenominationAsset().call(block_identifier=self._get_block_identifier()))

    def fetch_denomination_token(self) -> TokenDetails:
        """Return the current Blue denominator token."""

        return self.denomination_token

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read human-readable VaultProxy share supply."""

        raw_supply = self.vault_contract.functions.totalSupply().call(block_identifier=block_identifier)
        return Decimal(raw_supply) / Decimal(10**self.share_token.decimals)

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read GAV expressed in human-readable denomination-token units."""

        raw_gav = self.comptroller_contract.functions.calcGav().call(block_identifier=block_identifier)
        return Decimal(raw_gav) / Decimal(10**self.denomination_token.decimals)

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Calculate GAV per human-readable share."""

        supply = self.fetch_total_supply(block_identifier)
        return self.fetch_total_assets(block_identifier) / supply if supply else Decimal(0)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return Blue GAV as scanner TVL."""

        return self.fetch_total_assets(block_identifier)

    def fetch_info(self) -> VaultInfo:
        """Return the paired current Blue core contract addresses."""

        return {"vault": self.address, "comptroller": self.comptroller_contract.address, "denomination_asset": self.denomination_token.address}

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return empty positions until Blue portfolio accounting is integrated."""

        del universe, block_identifier
        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Return false until Blue flow events are mapped to VaultProxy."""

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return false; Blue allocations do not make this guarantee."""

        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Return the explicit unsupported Enzyme flow reader."""

        return EnzymeVaultFlowManager()

    def get_deposit_manager(self) -> VaultDepositManager:
        """Reject unimplemented Blue transaction flows."""

        message = "Enzyme Blue deposit and redemption flows are not implemented"
        raise RuntimeError(message)

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Return the GAV and supply historical reader.

        Historical deposit and redemption availability remains unsupported;
        see :class:`EnzymeBlueVaultHistoricalReader` for the reason.

        :param stateful: Whether the shared reader should retain adaptive scan
            state between runs.
        :return: Historical GAV and share-supply reader.
        """

        return EnzymeBlueVaultHistoricalReader(self, stateful)

    def get_protocol_name(self) -> str:
        """Return shared Enzyme display name."""

        return "Enzyme"

    def is_whitelisted_deposit(self) -> bool:
        """Determine whether Blue limits investors through its recipient policy.

        Enzyme Blue's `Allowed Deposit Recipients policy
        <https://docs.enzyme.finance/user-documentation/blue-enzyme-vaults/markdown/seeding>`__
        is the reviewed vault-level allowlist for ``buyShares``. The policy
        manager enumerates enabled policy contracts for the paired
        ComptrollerProxy. Every policy implements ``identifier()``, so reading
        its stable identifier avoids hardcoding policy deployment addresses.
        Deprecated Phoenix and Encore Comptrollers cannot return their policy
        manager directly; for these releases the persistent Dispatcher gives
        the current FundDeployer, which is mapped to Enzyme's reviewed release
        deployment metadata. The adapter does not treat asset, adapter, risk,
        redemption, or transfer policies as investor allowlists.

        This is a current-state classification only. A result of ``False``
        means no recipient allowlist policy is active; it does not promise a
        successful deposit because Blue can apply other policies, balances,
        approvals or fund-specific conditions.

        :return: ``True`` when ``ALLOWED_DEPOSIT_RECIPIENTS`` is enabled for
            this fund, otherwise ``False``.
        """

        block_identifier = self._get_block_identifier()
        try:
            policy_manager_address = self.comptroller_contract.functions.getPolicyManager().call(block_identifier=block_identifier)
        except (BadFunctionCallOutput, ContractLogicError, ValueError):
            deployment = ENZYME_BLUE_DEPLOYMENTS.get(self.chain_id)
            if deployment is None:
                raise
            dispatcher = get_deployed_contract(self.web3, "enzyme/Dispatcher.json", deployment.dispatcher)
            fund_deployer = dispatcher.functions.getFundDeployerForVaultProxy(self.address).call(block_identifier=block_identifier)
            policy_manager_address = ENZYME_BLUE_LEGACY_POLICY_MANAGERS.get((self.chain_id, fund_deployer.lower()))
            if policy_manager_address is None:
                raise
        policy_manager = get_deployed_contract(self.web3, "enzyme/PolicyManager.json", policy_manager_address)
        policy_addresses = policy_manager.functions.getEnabledPoliciesForFund(self.comptroller_contract.address).call(block_identifier=block_identifier)
        for policy_address in policy_addresses:
            policy = get_deployed_contract(self.web3, "enzyme/IPolicy.json", policy_address)
            identifier = policy.functions.identifier().call(block_identifier=block_identifier)
            if identifier in ENZYME_BLUE_DEPOSIT_PERMISSION_POLICY_IDENTIFIERS:
                self.whitelist_notes = f"Enzyme {identifier} policy restricts investor addresses; the policy does not establish KYC."
                return True
        self.whitelist_notes = None
        return False

    def get_fee_mode(self) -> VaultFeeMode:
        """Return Blue's share-minting dilution fee mechanism."""

        return VaultFeeMode.internalised_minting

    def _try_fee_call(self, contract: Contract, function_name: str, *args, block_identifier: BlockIdentifier) -> object | None:
        """Call an optional Blue fee function without hiding transport errors."""

        try:
            return getattr(contract.functions, function_name)(*args).call(block_identifier=block_identifier)
        except ExtraValueError:
            # A provider response is not proof that a reviewed fee is absent.
            raise
        except (ABIFunctionNotFound, BadFunctionCallOutput, ContractLogicError, ValueError):
            return None

    def _fetch_enabled_fee_contracts(self, block_identifier: BlockIdentifier) -> list[Contract]:
        """Resolve configured FeeManager plugins for this fund once."""

        fee_manager_address = self.comptroller_contract.functions.getFeeManager().call(block_identifier=block_identifier)
        fee_manager = get_deployed_contract(self.web3, "enzyme/FeeManager.json", fee_manager_address)
        addresses = fee_manager.functions.getEnabledFeesForFund(self.comptroller_contract.address).call(block_identifier=block_identifier)
        return [get_deployed_contract(self.web3, "enzyme/EnzymeBlueFee.json", address) for address in addresses]

    def _fetch_protocol_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Read the configured current Blue protocol-access rate.

        A reviewed Blue deployment with no ProtocolFeeTracker has explicitly
        disabled this optional charge, so it is a confirmed zero rather than
        unavailable fee information.

        :param block_identifier: Block number or tag for the current-state
            configuration read.
        :return: Current protocol-access rate, zero when disabled, or
            ``None`` for an unsupported deployment.
        """

        deployment = ENZYME_BLUE_DEPLOYMENTS.get(self.chain_id)
        if deployment is None:
            return None
        dispatcher = get_deployed_contract(self.web3, "enzyme/Dispatcher.json", deployment.dispatcher)
        fund_deployer_address = dispatcher.functions.getFundDeployerForVaultProxy(self.address).call(block_identifier=block_identifier)
        fund_deployer = get_deployed_contract(self.web3, "enzyme/FundDeployer.json", fund_deployer_address)
        try:
            tracker_address = fund_deployer.functions.getProtocolFeeTracker().call(block_identifier=block_identifier)
        except ExtraValueError:
            # Do not publish a zero protocol fee when the provider failed.
            raise
        except (ABIFunctionNotFound, BadFunctionCallOutput, ContractLogicError, ValueError):
            # Pre-ProtocolFeeTracker Blue releases have no protocol access fee.
            return Percent(0)
        if tracker_address.lower() == ZERO_ADDRESS.lower():
            return Percent(0)
        tracker = get_deployed_contract(self.web3, "enzyme/ProtocolFeeTracker.json", tracker_address)
        fee_bps = tracker.functions.getFeeBpsForVault(self.address).call(block_identifier=block_identifier)
        return Percent(float(Decimal(fee_bps) / FEE_BPS_DENOMINATOR))

    def get_fee_data(self) -> FeeData:
        """Read current Blue investor fee rates.

        Blue's protocol fee is distinct from a fund's optional ManagementFee
        plugin, but it is included in the exported management fee rather than
        exposed as a separate fee category. Enzyme's canonical `Protocol Fees
        documentation <https://docs.enzyme.finance/user-documentation/blue-general-info/protocol-fees>`__
        defines it as a charge on Assets Under Technology applied through share
        inflation; unlike the PerformanceFee it is not conditional on growth
        above a high-water mark. It is therefore a management fee for the
        public vault export.

        Performance, entrance, and exit contracts store their rates in basis
        points. Enzyme's canonical `ManagementFee
        <https://docs.enzyme.finance/enzyme-blue-protocol/fee-formulas/managementfee>`__
        and `Performance Fee
        <https://docs.enzyme.finance/enzyme-blue-protocol/fee-formulas/performance-fee>`__
        documentation describes ManagementFee's Ray-scaled per-second
        compounding factor and PerformanceFee's high-water-mark calculation.
        Historical fee configuration is TODO because Blue fee plugins,
        releases, and protocol-fee trackers can be replaced.

        :return:
            Current investor-facing fee settings. ``management`` includes
            protocol access. ``protocol`` is retained as a breakdown, so the
            manager-only rate is ``management - protocol``.
        """

        block_identifier = self._get_block_identifier()
        management = performance = deposit = withdraw = None
        for fee in self._fetch_enabled_fee_contracts(block_identifier):
            entrance_rate = self._try_fee_call(fee, "getRateForFund", self.comptroller_contract.address, block_identifier=block_identifier)
            if entrance_rate is not None:
                deposit = Percent(float(Decimal(entrance_rate) / FEE_BPS_DENOMINATOR))
                continue
            exit_rate = self._try_fee_call(fee, "getInKindRateForFund", self.comptroller_contract.address, block_identifier=block_identifier)
            if exit_rate is not None:
                withdraw = Percent(float(Decimal(exit_rate) / FEE_BPS_DENOMINATOR))
                continue
            fee_info = self._try_fee_call(fee, "getFeeInfoForFund", self.comptroller_contract.address, block_identifier=block_identifier)
            if fee_info is None:
                # The reviewed Blue plugins expose the canonical getters above.
                # Do not turn a missing optional component into ``unknown``.
                continue
            rate = Decimal(fee_info[0])
            # ManagementFee stores a 1e27-scaled per-second compounding factor,
            # whereas PerformanceFee stores a direct basis-point rate. The former
            # is always at least 1e27 (including a zero annual rate), making
            # the ABI-identical FeeInfo structs safely distinguishable.
            if rate >= MANAGEMENT_FEE_RATE_SCALE:
                annual_effective_rate = (rate / MANAGEMENT_FEE_RATE_SCALE) ** int(SECONDS_PER_YEAR)
                management = Percent(float(1 - 1 / annual_effective_rate))
            else:
                performance = Percent(float(rate / FEE_BPS_DENOMINATOR))

        protocol_fee = self._fetch_protocol_fee(block_identifier)
        # Missing reviewed fee plugins are zero, not unavailable metadata.
        management = management if management is not None else Percent(0)
        performance = performance if performance is not None else Percent(0)
        deposit = deposit if deposit is not None else Percent(0)
        withdraw = withdraw if withdraw is not None else Percent(0)
        # Protocol access is included in management; see this method's docstring.
        management = combine_user_facing_management_fee(management, protocol_fee) if protocol_fee is not None else None
        return FeeData(fee_mode=self.get_fee_mode(), management=management, performance=performance, deposit=deposit, withdraw=withdraw, protocol=protocol_fee)

    def get_link(self, referral: str | None = None) -> str:
        """Return the address-specific Enzyme Blue application page.

        Enzyme selects the deployment using a lower-case network query value.
        Linking the canonical VaultProxy directly avoids sending an investor to
        the generic discovery catalogue where they would need to locate the
        vault again.

        :param referral: Accepted for the shared vault interface; Enzyme does
            not expose a reviewed referral query parameter.
        :return: Direct Enzyme application URL for this Blue VaultProxy.
        """

        del referral
        return create_enzyme_vault_link(self.chain_id, self.address)
