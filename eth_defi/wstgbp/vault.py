"""Wren Staked tGBP vault adapter.

wstGBP is an ERC-20 token with permissionless ``mint()`` and ``redeem()``
functions. It is not an ERC-4626 vault: the purchase token is
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
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultBase, VaultDepositManager, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.lower_case_dict import LowercaseDict
from eth_defi.wstgbp.historical import WSTGBPVaultHistoricalReader

WSTGBP_DOCUMENTATION = "https://docs.wstgbp.com/"
WSTGBP_NAV_SOURCE = "wstgbp_navprice"
WSTGBP_NOTE = "wstGBP (Wren Staked tGBP) is a non-custodial, non-rebasing ERC-20 wrapper around tGBP, a pound sterling stablecoin issued by BCP Technologies, an FCA-registered cryptoasset firm, backed 1:1 by sterling reserves. Users mint and redeem permissionlessly on Ethereum at the onchain exchange rate. Balances stay fixed and rewards, when applied, are reflected in the wstGBP to tGBP exchange rate through periodic NAV updates. Minting is free and instant redemption carries a 25 bps fee, with no cooldown."
WAD = Decimal(10**18)

WSTGBP_ABI = [
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
]


class WSTGBPVaultInfo(VaultInfo, total=False):
    """Wren Staked tGBP token metadata and operational state."""

    #: ERC-20 share token and wstGBP contract address.
    token: HexAddress

    #: Chain id.
    chain_id: int

    #: Purchase and redemption ERC-20 token exposed by ``gem()``.
    denomination_token: HexAddress

    #: NAV source label.
    nav_source: str

    #: Whether NAV is estimated.
    nav_estimated: bool


class WSTGBPVault(VaultBase):
    """Scan-only adapter for Wren Staked tGBP contracts.

    The wstGBP contract itself is the share token. It reports a WAD-scaled
    NAV/share and the actual asset used for minting and redemption. The adapter
    reads these values, but leaves generic transaction construction unsupported
    because the contract uses bespoke flows.
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
        """Create a Wren Staked tGBP vault adapter.

        :param web3:
            Web3 connection.
        :param spec:
            Chain and wstGBP token address.
        :param token_cache:
            Token metadata cache used by :func:`fetch_erc20_details`.
        :param features:
            Shared pipeline feature flags. Expected to contain
            :data:`ERC4626Feature.wstgbp_like`.
        :param default_block_identifier:
            Default block for metadata reads.
        :param require_denomination_token:
            Whether a failed ``gem()`` lookup should raise through
            :attr:`VaultBase.denomination_token`.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3 = web3
        self.spec = spec
        self.features = features or {ERC4626Feature.wstgbp_like}
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
        """Return the Wren Staked tGBP token and vault address.

        :return:
            Checksummed wstGBP contract address.
        """

        return HexAddress(Web3.to_checksum_address(self.spec.vault_address))

    @property
    def vault_address(self) -> HexAddress:
        """Return the scanner compatibility vault-address alias.

        :return:
            The wstGBP contract address.
        """

        return self.address

    @property
    def wstgbp_contract(self) -> Contract:
        """Return the wstGBP contract interface.

        :return:
            Contract bound to this asset's primary address.
        """

        return self.web3.eth.contract(address=self.address, abi=WSTGBP_ABI)

    @property
    def name(self) -> str:
        """Return the ERC-20 share token name.

        :return:
            Human-readable Wren Staked tGBP product name.
        """

        return self.share_token.name

    @property
    def symbol(self) -> str:
        """Return the ERC-20 share token symbol.

        :return:
            Wren Staked tGBP share-token symbol.
        """

        return self.share_token.symbol

    @property
    def description(self) -> str:
        """Return the product description.

        :return:
            Short explanation of the Wren Staked tGBP product shape.
        """

        return "Non-custodial tokenised sterling wrapper with onchain NAV/share"

    @property
    def short_description(self) -> str:
        """Return a listing-friendly product summary.

        :return:
            Short product description.
        """

        return "Non-custodial tokenised sterling wrapper with onchain NAV"

    def get_notes(self) -> str:
        """Return the wstGBP product description for vault exports.

        Unlike manual vault notes, this description applies to the sole
        Wren Staked tGBP deployment and is part of its native metadata.

        :return:
            Human-readable wstGBP product and redemption information.
        """

        return WSTGBP_NOTE

    @property
    def manager_name(self) -> str:
        """Return the protocol display name.

        :return:
            Wren Staked tGBP protocol name.
        """

        return "wstGBP"

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the Wren Staked tGBP share-token address.

        :param block_identifier:
            Accepted for shared scanner compatibility.
        :return:
            wstGBP contract address.
        """

        return self.address

    def fetch_share_token(self) -> TokenDetails:
        """Fetch Wren Staked tGBP ERC-20 token metadata.

        :return:
            Share token details.
        """

        return fetch_erc20_details(
            self.web3,
            self.address,
            chain_id=self.chain_id,
            raise_on_error=False,
            cache=self.token_cache,
            cause_diagnostics_message=f"Wren Staked tGBP share token for vault {self.address}",
        )

    def fetch_denomination_token_address(self) -> HexAddress:
        """Read the wstGBP purchase token through ``gem()``.

        :return:
            Checksummed ERC-20 token address used for minting and redemption.
        """

        address = self.wstgbp_contract.functions.gem().call(block_identifier=self.default_block_identifier or "latest")
        return HexAddress(Web3.to_checksum_address(address))

    def fetch_denomination_token(self) -> TokenDetails:
        """Fetch metadata for the wstGBP ``gem()`` token.

        :return:
            Purchase and redemption token details.
        """

        return fetch_erc20_details(
            self.web3,
            self.fetch_denomination_token_address(),
            chain_id=self.chain_id,
            raise_on_error=False,
            cache=self.token_cache,
            cause_diagnostics_message=f"Wren Staked tGBP denomination token for vault {self.address}",
        )

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read Wren Staked tGBP NAV/share from ``navprice()``.

        :param block_identifier:
            Historical block identifier.
        :return:
            NAV of one share in the ``gem()`` denomination.
        """

        raw_price = self.wstgbp_contract.functions.navprice().call(block_identifier=block_identifier)
        return Decimal(raw_price) / WAD

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read the outstanding Wren Staked tGBP share supply.

        :param block_identifier:
            Historical block identifier.
        :return:
            Human-readable share supply.
        """

        raw_supply = self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier)
        return self.share_token.convert_to_decimals(raw_supply)

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Calculate Wren Staked tGBP TVL from supply and NAV/share.

        :param block_identifier:
            Historical block identifier.
        :return:
            TVL in the ``gem()`` denomination.
        """

        return self.fetch_total_supply(block_identifier) * self.fetch_share_price(block_identifier)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return Wren Staked tGBP NAV.

        :param block_identifier:
            Historical block identifier.
        :return:
            TVL in the ``gem()`` denomination.
        """

        return self.fetch_total_assets(block_identifier)

    def fetch_info(self) -> WSTGBPVaultInfo:
        """Return Wren Staked tGBP metadata and NAV source.

        :return:
            Onchain denomination token and NAV-source data.
        """

        return WSTGBPVaultInfo(
            token=self.address,
            chain_id=self.chain_id,
            denomination_token=self.fetch_denomination_token_address(),
            nav_source=WSTGBP_NAV_SOURCE,
            nav_estimated=False,
        )

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Return Wren Staked tGBP-specific scanner diagnostics.

        :return:
            NAV source and mint/redemption information.
        """

        info = self.fetch_info()
        return {
            "Denomination": self.denomination_token.symbol,
            "_notes": self.get_notes(),
            "_nav_source": WSTGBP_NAV_SOURCE,
            "_nav_estimated": False,
            "_wstgbp_gem": info["denomination_token"],
        }

    def fetch_portfolio(
        self,
        universe: TradingUniverse,
        block_identifier: BlockIdentifier | None = None,
    ) -> VaultPortfolio:
        """Return an empty portfolio for the scan-only adapter.

        The Wren Staked tGBP contract can route surplus ``gem`` liquidity to its
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
            ``False`` because Wren Staked tGBP uses bespoke lifecycle events.
        """

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return whether deposits distribute directly to positions.

        :return:
            ``False`` because the adapter does not model Wren Staked tGBP flows.
        """

        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject generic Wren Staked tGBP flow accounting.

        :raises NotImplementedError:
            Wren Staked tGBP events are not currently mapped to generic flows.
        """

        message = "Wren Staked tGBP flow accounting is not implemented"
        raise NotImplementedError(message)

    def get_deposit_manager(self) -> VaultDepositManager:
        """Reject generic Wren Staked tGBP transaction construction.

        :raises NotImplementedError:
            The contract's mint/redeem functions use bespoke transaction methods.
        """

        message = "Wren Staked tGBP active minting and redemption are not implemented"
        raise NotImplementedError(message)

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Create a Wren Staked tGBP historical reader.

        :param stateful:
            Whether to attach adaptive reader state.
        :return:
            Historical reader for supply and NAV/share.
        """

        return WSTGBPVaultHistoricalReader(self, stateful=stateful)

    def get_fee_data(self) -> FeeData:
        """Return current Wren Staked tGBP mint and redemption spread fees.

        :return:
            Externalised entry and exit fees derived from onchain prices.
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

        raw_nav = self.wstgbp_contract.functions.navprice().call(block_identifier=block_identifier)
        if raw_nav == 0:
            return None
        raw_mint_cost = self.wstgbp_contract.functions.mintcost().call(block_identifier=block_identifier)
        return float(Decimal(raw_mint_cost - raw_nav) / Decimal(raw_nav))

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Calculate the redemption spread below NAV/share.

        :param block_identifier:
            Historical block identifier.
        :return:
            Redemption fee as a fraction, or ``None`` when NAV is zero.
        """

        raw_nav = self.wstgbp_contract.functions.navprice().call(block_identifier=block_identifier)
        if raw_nav == 0:
            return None
        raw_burn_cost = self.wstgbp_contract.functions.burncost().call(block_identifier=block_identifier)
        return float(Decimal(raw_nav - raw_burn_cost) / Decimal(raw_nav))

    def get_link(self, referral: str | None = None) -> str:
        """Return the Wren Staked tGBP product documentation link.

        :param referral:
            Ignored because Wren Staked tGBP links do not use referral parameters.
        :return:
            Public wstGBP documentation URL.
        """

        return WSTGBP_DOCUMENTATION
