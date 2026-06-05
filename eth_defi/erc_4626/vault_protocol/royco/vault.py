"""Royco Protocol WrappedVault support."""

import datetime
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from functools import cached_property

from eth_typing import BlockIdentifier
from web3.contract import Contract

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.erc_4626.vault_protocol.royco.offchain_metadata import (
    RoycoOffchainVaultMetadata,
    fetch_royco_vault_metadata,
)
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.token import TokenDetails
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

logger = logging.getLogger(__name__)

EVM_WORD_BYTES = 32
ASSET_CLAIMS_WORDS = 3
ASSET_CLAIMS_BYTES = EVM_WORD_BYTES * ASSET_CLAIMS_WORDS


@dataclass(slots=True, frozen=True)
class RoycoAssetClaims:
    """Royco tranche asset claims.

    Royco tranche vaults return this struct from ``totalAssets()``,
    ``convertToAssets(uint256)``, ``previewRedeem(uint256)`` and ``redeem(...)``.
    """

    #: Claim on senior tranche assets in tranche units.
    st_assets: int

    #: Claim on junior tranche assets in tranche units.
    jt_assets: int

    #: Net asset value in raw Royco NAV units.
    #:
    #: Use the vault share token's :py:class:`eth_defi.token.TokenDetails` to
    #: convert this to decimals.
    nav: int


def _parse_asset_claims(value: tuple[int, int, int] | list[int] | bytes) -> RoycoAssetClaims:
    """Parse Royco ``AssetClaims`` from Web3 or multicall output.

    :param value:
        Either a Web3.py tuple/list return value or raw ABI-encoded bytes from
        multicall.

    :return:
        Parsed claims.
    """
    if isinstance(value, bytes):
        if len(value) < ASSET_CLAIMS_BYTES:
            raise ValueError(f"Royco AssetClaims return payload too short: {len(value)} bytes")
        return RoycoAssetClaims(
            st_assets=convert_int256_bytes_to_int(value[0:EVM_WORD_BYTES]),
            jt_assets=convert_int256_bytes_to_int(value[EVM_WORD_BYTES : 2 * EVM_WORD_BYTES]),
            nav=convert_int256_bytes_to_int(value[2 * EVM_WORD_BYTES : ASSET_CLAIMS_BYTES]),
        )

    if len(value) != ASSET_CLAIMS_WORDS:
        raise ValueError(f"Expected Royco AssetClaims to have 3 items, got {len(value)}")

    return RoycoAssetClaims(
        st_assets=int(value[0]),
        jt_assets=int(value[1]),
        nav=int(value[2]),
    )


def _convert_nav_to_decimal(raw_nav: int, nav_unit_token: TokenDetails) -> Decimal:
    """Convert Royco raw NAV units to a decimal value.

    Royco's ABI exposes NAV values as ``NAV_UNIT`` instead of a plain ERC-20
    asset amount. For the currently deployed tranche contracts, this unit uses
    the tranche share token precision, while the denomination token may have a
    different number of decimals. Use :py:class:`eth_defi.token.TokenDetails`
    for the conversion so the reader does not hardcode token precision.

    :param raw_nav:
        Raw ``NAV_UNIT`` integer from ``AssetClaims.nav``.

    :param nav_unit_token:
        Token details that define the NAV unit decimal precision.

    :return:
        Decimal NAV value.
    """
    return nav_unit_token.convert_to_decimals(raw_nav)


class RoycoTrancheHistoricalReader(ERC4626HistoricalReader):
    """Read Royco tranche vault history with tuple-aware accounting.

    Royco senior/junior tranche vaults return ``AssetClaims`` from
    ``totalAssets()`` and ``convertToAssets(uint256)``. The generic ERC-4626
    reader silently decodes only the first word of the tuple, which is
    ``stAssets`` and not necessarily the vault NAV. This reader decodes the
    whole tuple and uses ``claims.nav`` for value and share price.
    """

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        yield from self.construct_core_erc_4626_multicall()

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        call_by_name = self.dictify_multicall_results(block_number, call_results)

        errors: list[str] = []
        share_token = self.vault.share_token

        total_supply_result = call_by_name.get("total_supply")
        if total_supply_result is None:
            errors.append("total_supply call missing")
            total_supply = None
        elif total_supply_result.success and share_token is not None:
            raw_total_supply = convert_int256_bytes_to_int(total_supply_result.result)
            total_supply = share_token.convert_to_decimals(raw_total_supply)
        else:
            errors.append("total_supply call failed")
            total_supply = None

        total_assets_result = call_by_name.get("total_assets")
        if total_assets_result is None:
            errors.append("total_assets call missing")
            total_assets = None
        elif total_assets_result.success and share_token is not None:
            total_claims = _parse_asset_claims(total_assets_result.result)
            total_assets = _convert_nav_to_decimal(total_claims.nav, share_token)
        else:
            errors.append("total_assets call failed")
            total_assets = None

        convert_to_assets_result = call_by_name.get("convertToAssets")
        if convert_to_assets_result is not None and convert_to_assets_result.success and share_token is not None:
            share_claims = _parse_asset_claims(convert_to_assets_result.result)
            share_price = _convert_nav_to_decimal(share_claims.nav, share_token)

            if convert_to_assets_result.state is not None:
                convert_to_assets_result.state.on_called(
                    convert_to_assets_result,
                    total_assets=total_assets,
                    share_price=share_price,
                )
        else:
            share_price = None

        max_deposit_result = call_by_name.get("maxDeposit")
        if max_deposit_result and max_deposit_result.success and self.vault.denomination_token is not None:
            raw_max_deposit = convert_int256_bytes_to_int(max_deposit_result.result)
            max_deposit = self.vault.denomination_token.convert_to_decimals(raw_max_deposit)
        else:
            max_deposit = None

        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            performance_fee=None,
            management_fee=None,
            errors=errors or None,
            max_deposit=max_deposit,
        )


class RoycoVault(ERC4626Vault):
    """Royco Protocol WrappedVault support.

    Royco is an Incentivised Action Market (IAM) Protocol that allows protocols
    to create incentivised ERC-4626 vault wrappers with integrated rewards systems.
    The WrappedVault contract wraps underlying vaults and adds reward distribution
    functionality, supporting multiple simultaneous reward programmes.

    - Homepage: https://royco.org/
    - Documentation: https://docs.royco.org/
    - Github: https://github.com/roycoprotocol/royco
    - Example vault: https://etherscan.io/address/0x887d57a509070a0843c6418eb5cffc090dcbbe95

    Contract addresses:
    - WrappedVaultFactory: 0x75e502644284edf34421f9c355d75db79e343bca
    - WrappedVault implementation: 0x3c44c20377e252567d283dc7746d1bea67eb3e66
    - VaultMarketHub: 0xa97eCc6Bfda40baf2fdd096dD33e88bd8e769280

    Audits:
    - Spearbit (October 2024)
    - Cantina Private Competition
    - Cantina Open Competition

    See: https://docs.royco.org/for-incentive-providers/audits
    """

    @cached_property
    def royco_metadata(self) -> RoycoOffchainVaultMetadata | None:
        """Offchain metadata from Royco's first-party API.

        This covers Royco ``vault/explore`` rows and classic Royco
        ``market/explore`` Vault Market rows.
        """
        return fetch_royco_vault_metadata(self.web3, self.spec.vault_address)

    @property
    def description(self) -> str | None:
        """Full vault description from Royco offchain metadata."""
        if self.royco_metadata:
            return self.royco_metadata.get("description")
        return None

    @property
    def short_description(self) -> str | None:
        """Short display name from Royco offchain metadata."""
        if self.royco_metadata:
            return self.royco_metadata.get("name")
        return None

    def has_custom_fees(self) -> bool:
        """Royco vaults wrap underlying vaults.

        Fees are handled by the underlying wrapped vault, not by the wrapper itself.
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fees are determined by the underlying wrapped vault."""
        del block_identifier
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fees are determined by the underlying wrapped vault."""
        del block_identifier
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Lock-up depends on the underlying vault."""
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Link to Royco homepage.

        Individual vault pages are not available on the Royco interface.
        """
        del referral
        return "https://royco.org/"

    def get_notes(self) -> str | None:
        """Get notes for this vault.

        Falls back to Royco's offchain description when manual vault notes are
        not available.
        """
        manual_notes = super().get_notes()
        if manual_notes:
            return manual_notes
        return self.description


class RoycoTrancheVault(RoycoVault):
    """Royco senior/junior tranche vault support.

    Royco tranche vaults are nearly ERC-4626, but use a custom accounting
    interface:

    - ``totalAssets()`` returns ``AssetClaims(stAssets, jtAssets, nav)``
    - ``convertToAssets(uint256)`` returns the same ``AssetClaims`` tuple
    - ``redeem(...)`` returns ``AssetClaims`` and emits ``Redeem`` instead of
      the standard ERC-4626 ``Withdraw`` event
    - ``TRANCHE_TYPE()`` returns ``0`` for senior and ``1`` for junior

    The canonical value for vault price history is ``AssetClaims.nav`` in Royco
    ``NAV_UNIT`` precision. The standard ERC-4626 reader only decodes the first
    tuple word, so this class provides tuple-aware current and historical
    readers and converts raw NAV values through the tranche token
    :py:class:`eth_defi.token.TokenDetails`.

    Examples:

    - `ROY-JT-eEARN <https://etherscan.io/address/0x059bc7aa5000a26aae2601cfbf060653adf8fd91>`__
    - `ROY-ST-eEARN <https://etherscan.io/address/0x1ba515a409dd702105415cdaae439059aa0b402a>`__
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment with Royco tranche ABI.

        Senior and junior tranche implementations expose the same external
        function/event surface for scanner purposes. We use the senior ABI as
        the shared runtime interface while storing both verified implementation
        ABIs under ``eth_defi/abi/royco``. See ``eth_defi/abi/royco/README.md``
        for provenance notes.
        """
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="royco/RoycoSeniorTranche.json",
        )

    def get_historical_reader(self, stateful) -> VaultHistoricalReader:
        return RoycoTrancheHistoricalReader(self, stateful=stateful)

    def fetch_tranche_type(self, block_identifier: BlockIdentifier = "latest") -> int:
        """Fetch Royco tranche type.

        :param block_identifier:
            Block to query.

        :return:
            ``0`` for senior tranche and ``1`` for junior tranche.
        """
        return self.vault_contract.functions.TRANCHE_TYPE().call(block_identifier=block_identifier)

    def fetch_asset_claims(self, block_identifier: BlockIdentifier = "latest") -> RoycoAssetClaims:
        """Fetch current Royco tranche ``AssetClaims``.

        :param block_identifier:
            Block to query.

        :return:
            Decoded asset claims from ``totalAssets()``.
        """
        raw_claims = self.vault_contract.functions.totalAssets().call(block_identifier=block_identifier)
        return _parse_asset_claims(raw_claims)

    def fetch_total_assets(self, block_identifier: BlockIdentifier) -> Decimal:
        """Fetch the tranche NAV from ``AssetClaims.nav``.

        Royco's ``totalAssets()`` does not return a single ERC-4626 asset
        amount. It returns ``AssetClaims`` where ``nav`` is the full net asset
        value in Royco ``NAV_UNIT`` precision.

        :param block_identifier:
            Block to query.

        :return:
            Vault NAV in Royco NAV units.
        """
        claims = self.fetch_asset_claims(block_identifier)
        return _convert_nav_to_decimal(claims.nav, self.share_token)

    def fetch_nav(self, block_identifier: BlockIdentifier | None = None) -> Decimal:
        """Fetch current tranche NAV from Royco ``AssetClaims.nav``.

        :param block_identifier:
            Block to query.

        :return:
            Vault NAV in Royco NAV units.
        """
        if block_identifier is None:
            block_identifier = "latest"
        return self.fetch_total_assets(block_identifier)

    def fetch_share_price(self, block_identifier: BlockIdentifier) -> Decimal:
        """Fetch share price from ``convertToAssets(1 share).nav``.

        :param block_identifier:
            Block to query.

        :return:
            Share price in Royco NAV units.
        """
        raw_claims = self.vault_contract.functions.convertToAssets(self.share_token.convert_to_raw(Decimal(1))).call(block_identifier=block_identifier)
        claims = _parse_asset_claims(raw_claims)
        return _convert_nav_to_decimal(claims.nav, self.share_token)
