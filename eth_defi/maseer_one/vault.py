"""Maseer One tokenised asset vault adapter.

Maseer One assets are ERC-20 tokens with compliance-gated ``mint()`` and
``redeem()`` functions. They are not ERC-4626 vaults: the purchase token is
exposed through ``gem()`` and NAV/share through ``navprice()``. This adapter
uses :class:`eth_defi.vault.base.VaultBase` so the shared scanner can track
historical price and TVL without presenting the bespoke flows as ERC-4626.
"""

# Adapter classes intentionally mirror :class:`VaultBase` method signatures.
# ruff: noqa: ARG002, FBT001, FBT002, PLR0904, PLR0917, PLR6301

from decimal import Decimal

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.maseer_one.historical import MaseerOneVaultHistoricalReader
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultBase, VaultDepositManager, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.lower_case_dict import LowercaseDict
from eth_defi.vault.price_source import PriceSource

MASEER_ONE_DOCUMENTATION = "https://docs.wstgbp.com/"
MASEER_ONE_NAV_SOURCE = "maseer_one_navprice"
MASEER_ONE_BESPOKE_FLOW_REASON = "Maseer One minting and redemption require compliance approval"
WAD = Decimal(10**18)

MASEER_ONE_ABI = [
    {
        "inputs": [],
        "name": "gem",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "navprice",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "mintcost",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "burncost",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "mintable",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "burnable",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "cooldown",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


class MaseerOneVaultInfo(VaultInfo, total=False):
    """Maseer One token metadata and operational state."""

    #: ERC-20 share token and Maseer One contract address.
    token: HexAddress

    #: Chain id.
    chain_id: int

    #: Purchase and redemption ERC-20 token exposed by ``gem()``.
    denomination_token: HexAddress

    #: NAV source label.
    nav_source: str

    #: Whether NAV is estimated.
    nav_estimated: bool

    #: Whether minting is open at the selected block.
    mintable: bool

    #: Whether redemption is open at the selected block.
    burnable: bool

    #: Required wait before a redemption can be exited, in seconds.
    cooldown: int


class MaseerOneVault(VaultBase):
    """Scan-only adapter for Maseer One tokenised asset contracts.

    The Maseer One contract itself is the share token. It reports a WAD-scaled
    NAV/share and the actual asset used for minting and redemption. The adapter
    reads these values, but leaves transaction construction unsupported because
    Maseer One validates every caller through its compliance module.
    """

    def __init__(
        self,
        web3: Web3,
        spec: VaultSpec,
        token_cache: dict | None = None,
        features: set[ERC4626Feature] | None = None,
        default_block_identifier: BlockIdentifier | None = None,
        require_denomination_token: bool = False,
    ):
        """Create a Maseer One vault adapter.

        :param web3:
            Web3 connection.
        :param spec:
            Chain and Maseer One token address.
        :param token_cache:
            Token metadata cache used by :func:`fetch_erc20_details`.
        :param features:
            Shared pipeline feature flags. Expected to contain
            :data:`ERC4626Feature.maseer_one_like`.
        :param default_block_identifier:
            Default block for metadata reads.
        :param require_denomination_token:
            Whether a failed ``gem()`` lookup should raise through
            :attr:`VaultBase.denomination_token`.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3 = web3
        self.spec = spec
        self.features = features or {ERC4626Feature.maseer_one_like}
        self.default_block_identifier = default_block_identifier

    @property
    def chain_id(self) -> int:
        """Return the EVM chain identifier.

        :return:
            Chain id associated with this deployment.
        """

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the Maseer One token and vault address.

        :return:
            Checksummed Maseer One contract address.
        """

        return HexAddress(Web3.to_checksum_address(self.spec.vault_address))

    @property
    def vault_address(self) -> HexAddress:
        """Return the scanner compatibility vault-address alias.

        :return:
            The Maseer One contract address.
        """

        return self.address

    @property
    def maseer_contract(self) -> Contract:
        """Return the Maseer One contract interface.

        :return:
            Contract bound to this asset's primary address.
        """

        return self.web3.eth.contract(address=self.address, abi=MASEER_ONE_ABI)

    @property
    def name(self) -> str:
        """Return the ERC-20 share token name.

        :return:
            Human-readable Maseer One product name.
        """

        return self.share_token.name

    @property
    def symbol(self) -> str:
        """Return the ERC-20 share token symbol.

        :return:
            Maseer One share-token symbol.
        """

        return self.share_token.symbol

    @property
    def description(self) -> str:
        """Return the product description.

        :return:
            Short explanation of the Maseer One product shape.
        """

        return "Maseer One compliance-gated tokenised asset with on-chain NAV/share"

    @property
    def short_description(self) -> str:
        """Return a listing-friendly product summary.

        :return:
            Short product description.
        """

        return "Compliance-gated tokenised asset with on-chain NAV"

    @property
    def manager_name(self) -> str:
        """Return the protocol display name.

        :return:
            Maseer One platform name.
        """

        return "Maseer One"

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the Maseer One share-token address.

        :param block_identifier:
            Accepted for shared scanner compatibility.
        :return:
            Maseer One contract address.
        """

        return self.address

    def fetch_share_token(self) -> TokenDetails:
        """Fetch Maseer One ERC-20 token metadata.

        :return:
            Share token details.
        """

        return fetch_erc20_details(
            self.web3,
            self.address,
            chain_id=self.chain_id,
            raise_on_error=False,
            cache=self.token_cache,
            cause_diagnostics_message=f"Maseer One share token for vault {self.address}",
        )

    def fetch_denomination_token_address(self) -> HexAddress:
        """Read the Maseer One purchase token through ``gem()``.

        :return:
            Checksummed ERC-20 token address used for minting and redemption.
        """

        address = self.maseer_contract.functions.gem().call(block_identifier=self.default_block_identifier or "latest")
        return HexAddress(Web3.to_checksum_address(address))

    def fetch_denomination_token(self) -> TokenDetails:
        """Fetch metadata for the Maseer One ``gem()`` token.

        :return:
            Purchase and redemption token details.
        """

        return fetch_erc20_details(
            self.web3,
            self.fetch_denomination_token_address(),
            chain_id=self.chain_id,
            raise_on_error=False,
            cache=self.token_cache,
            cause_diagnostics_message=f"Maseer One denomination token for vault {self.address}",
        )

    def get_share_price_source(self) -> PriceSource:
        """Return the Maseer One NAV source classification.

        The adapter reads ``navprice()`` from the product contract at the
        requested block.

        :return:
            Smart-contract state source.
        """

        return PriceSource.smart_contract_state

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read Maseer One NAV/share from ``navprice()``.

        :param block_identifier:
            Historical block identifier.
        :return:
            NAV of one share in the ``gem()`` denomination.
        """

        raw_price = self.maseer_contract.functions.navprice().call(block_identifier=block_identifier)
        return Decimal(raw_price) / WAD

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read the outstanding Maseer One share supply.

        :param block_identifier:
            Historical block identifier.
        :return:
            Human-readable share supply.
        """

        raw_supply = self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier)
        return self.share_token.convert_to_decimals(raw_supply)

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Calculate Maseer One TVL from supply and NAV/share.

        :param block_identifier:
            Historical block identifier.
        :return:
            TVL in the ``gem()`` denomination.
        """

        return self.fetch_total_supply(block_identifier) * self.fetch_share_price(block_identifier)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return Maseer One NAV.

        :param block_identifier:
            Historical block identifier.
        :return:
            TVL in the ``gem()`` denomination.
        """

        return self.fetch_total_assets(block_identifier)

    def fetch_mintable(self, block_identifier: BlockIdentifier = "latest") -> bool:
        """Read whether Maseer One minting is open.

        :param block_identifier:
            Historical block identifier.
        :return:
            ``True`` when the market gate currently permits minting.
        """

        return self.maseer_contract.functions.mintable().call(block_identifier=block_identifier)

    def fetch_burnable(self, block_identifier: BlockIdentifier = "latest") -> bool:
        """Read whether Maseer One redemption is open.

        :param block_identifier:
            Historical block identifier.
        :return:
            ``True`` when the market gate currently permits redemption.
        """

        return self.maseer_contract.functions.burnable().call(block_identifier=block_identifier)

    def fetch_info(self) -> MaseerOneVaultInfo:
        """Return Maseer One metadata and current market status.

        :return:
            On-chain denomination token, NAV-source and gate data.
        """

        block_identifier = self.default_block_identifier or "latest"
        return MaseerOneVaultInfo(
            token=self.address,
            chain_id=self.chain_id,
            denomination_token=self.fetch_denomination_token_address(),
            nav_source=MASEER_ONE_NAV_SOURCE,
            nav_estimated=False,
            mintable=self.fetch_mintable(block_identifier),
            burnable=self.fetch_burnable(block_identifier),
            cooldown=self.maseer_contract.functions.cooldown().call(block_identifier=block_identifier),
        )

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Return Maseer One-specific scanner diagnostics.

        :return:
            NAV source, market state and compliance-flow annotations.
        """

        info = self.fetch_info()
        return {
            "Denomination": self.denomination_token.symbol,
            "_notes": self.get_notes(),
            "_deposit_closed_reason": self.fetch_deposit_closed_reason(),
            "_redemption_closed_reason": self.fetch_redemption_closed_reason(),
            "_nav_source": MASEER_ONE_NAV_SOURCE,
            "_nav_estimated": False,
            "_maseer_one_gem": info["denomination_token"],
            "_maseer_one_mintable": info["mintable"],
            "_maseer_one_burnable": info["burnable"],
            "_maseer_one_cooldown": info["cooldown"],
        }

    def fetch_portfolio(
        self,
        universe: TradingUniverse,
        block_identifier: BlockIdentifier | None = None,
    ) -> VaultPortfolio:
        """Return an empty portfolio for the scan-only adapter.

        The Maseer One contract can route surplus ``gem`` liquidity to its
        conduit, so its ERC-20 balance is not a canonical representation of
        the tokenised asset's portfolio.

        :param universe:
            Ignored.
        :param block_identifier:
            Ignored.
        :return:
            Empty spot portfolio.
        """

        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Return whether standard vault-flow event reading is supported.

        :return:
            ``False`` because Maseer One uses bespoke lifecycle events.
        """

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return whether deposits distribute directly to positions.

        :return:
            ``False`` because the adapter does not model Maseer One flows.
        """

        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject generic Maseer One flow accounting.

        :raises NotImplementedError:
            Maseer One events are not currently mapped to generic flows.
        """

        message = "Maseer One flow accounting is not implemented"
        raise NotImplementedError(message)

    def get_deposit_manager(self) -> VaultDepositManager:
        """Reject generic Maseer One transaction construction.

        :raises NotImplementedError:
            The contract's mint/redeem functions require compliance approval.
        """

        message = "Maseer One active minting and redemption are not implemented"
        raise NotImplementedError(message)

    def fetch_deposit_closed_reason(self) -> str | None:
        """Return the generic-deposit availability status.

        :return:
            Compliance gating or a market-closed reason at the selected block.
        """

        block_identifier = self.default_block_identifier or "latest"
        if not self.fetch_mintable(block_identifier):
            return "Maseer One minting is currently disabled"
        return MASEER_ONE_BESPOKE_FLOW_REASON

    def fetch_redemption_closed_reason(self) -> str | None:
        """Return the generic-redemption availability status.

        :return:
            Compliance gating or a market-closed reason at the selected block.
        """

        block_identifier = self.default_block_identifier or "latest"
        if not self.fetch_burnable(block_identifier):
            return "Maseer One redemption is currently disabled"
        return MASEER_ONE_BESPOKE_FLOW_REASON

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Create a Maseer One historical reader.

        :param stateful:
            Whether to attach adaptive reader state.
        :return:
            Historical reader for supply and NAV/share.
        """

        return MaseerOneVaultHistoricalReader(self, stateful=stateful)

    def get_fee_data(self) -> FeeData:
        """Return current Maseer One mint and redemption spread fees.

        :return:
            Externalised entry and exit fees derived from on-chain prices.
        """

        block_identifier = self.default_block_identifier or "latest"
        return FeeData(
            fee_mode=VaultFeeMode.externalised,
            management=None,
            performance=None,
            deposit=self.get_deposit_fee(block_identifier),
            withdraw=self.get_withdraw_fee(block_identifier),
        )

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return the unknown annual management fee.

        :param block_identifier:
            Ignored because no annual fee is exposed by the token surface.
        :return:
            ``None``.
        """

        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return the unknown performance fee.

        :param block_identifier:
            Ignored because no performance fee is exposed by the token surface.
        :return:
            ``None``.
        """

        return None

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Calculate the mint spread above NAV/share.

        :param block_identifier:
            Historical block identifier.
        :return:
            Mint fee as a fraction, or ``None`` when NAV is zero.
        """

        raw_nav = self.maseer_contract.functions.navprice().call(block_identifier=block_identifier)
        if raw_nav == 0:
            return None
        raw_mint_cost = self.maseer_contract.functions.mintcost().call(block_identifier=block_identifier)
        return float(Decimal(raw_mint_cost - raw_nav) / Decimal(raw_nav))

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Calculate the redemption spread below NAV/share.

        :param block_identifier:
            Historical block identifier.
        :return:
            Redemption fee as a fraction, or ``None`` when NAV is zero.
        """

        raw_nav = self.maseer_contract.functions.navprice().call(block_identifier=block_identifier)
        if raw_nav == 0:
            return None
        raw_burn_cost = self.maseer_contract.functions.burncost().call(block_identifier=block_identifier)
        return float(Decimal(raw_nav - raw_burn_cost) / Decimal(raw_nav))

    def get_link(self, referral: str | None = None) -> str:
        """Return the Maseer One product documentation link.

        :param referral:
            Ignored because Maseer One links do not use referral parameters.
        :return:
            Public wstGBP documentation URL.
        """

        return MASEER_ONE_DOCUMENTATION
