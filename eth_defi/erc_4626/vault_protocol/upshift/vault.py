"""Upshift vault support."""

import datetime
import logging
from collections.abc import Iterable
from decimal import Decimal
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.core import ERC4626Feature, get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault, VaultReaderState
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

logger = logging.getLogger(__name__)


class UpshiftMultiAssetHistoricalReader(VaultHistoricalReader):
    """Read Upshift multi-asset vault accounting history.

    Upshift multi-asset vaults are not plain ERC-4626 share-token contracts.
    The vault proxy exposes accounting methods like ``getSharePrice()`` and
    ``getTotalAssets()``, while ``lpTokenAddress()`` points to the ERC-20 share
    token used for name, symbol, decimals and total supply.

    Relevant verified implementation:
    `Upshift multiAssetVault <https://etherscan.io/address/0xEB5f80aCEa6060764E91c185bE93752Ab40F01c2#code>`__.
    """

    def __init__(self, vault: "UpshiftVault", stateful: bool):  # noqa: FBT001
        """Create a historical reader for Upshift multi-asset vaults.

        :param vault:
            Upshift vault adapter.

        :param stateful:
            Whether to attach adaptive reader state used by the shared
            historical multicaller.
        """

        super().__init__(vault)
        if stateful:
            self.reader_state = VaultReaderState(vault)
        else:
            self.reader_state = None

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Construct historical multicalls for Upshift multi-asset vaults.

        :return:
            Calls for vault share price, NAV, LP token supply, pause flags and
            configured maximum deposit/withdrawal amounts.
        """

        upshift = self.vault.upshift_contract.functions
        calls = {
            "getSharePrice": upshift.getSharePrice(),
            "getTotalAssets": upshift.getTotalAssets(),
            "lpTokenTotalSupply": self.vault.share_token.contract.functions.totalSupply(),
            "depositsPaused": upshift.depositsPaused(),
            "withdrawalsPaused": upshift.withdrawalsPaused(),
            "maxDepositAmount": upshift.maxDepositAmount(),
            "maxWithdrawalAmount": upshift.maxWithdrawalAmount(),
        }

        for function_name, contract_call in calls.items():
            yield EncodedCall.from_contract_call(
                contract_call,
                extra_data={
                    "function": function_name,
                    "vault": self.vault.address,
                },
                first_block_number=self.first_block,
            )

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        """Convert Upshift multi-asset multicalls to a vault price row.

        :param block_number:
            Historical block number.

        :param timestamp:
            Naive UTC block timestamp.

        :param call_results:
            Multicall results created by :py:meth:`construct_multicalls`.

        :return:
            :py:class:`VaultHistoricalRead` with Upshift share price, NAV and
            LP token supply.
        """

        call_by_name = {r.call.extra_data["function"]: r for r in call_results}
        denomination_token = self.vault.denomination_token
        share_token = self.vault.share_token

        share_price = None
        total_assets = None
        total_supply = None
        deposits_open = None
        redemption_open = None
        max_deposit = None
        available_liquidity = None
        errors = []

        share_price_result = call_by_name.get("getSharePrice")
        if denomination_token is not None and share_price_result is not None and share_price_result.success:
            share_price = denomination_token.convert_to_decimals(convert_int256_bytes_to_int(share_price_result.result))
        else:
            errors.append("getSharePrice call failed")

        total_assets_result = call_by_name.get("getTotalAssets")
        if denomination_token is not None and total_assets_result is not None and total_assets_result.success:
            total_assets = denomination_token.convert_to_decimals(convert_int256_bytes_to_int(total_assets_result.result))
        else:
            errors.append("getTotalAssets call failed")

        total_supply_result = call_by_name.get("lpTokenTotalSupply")
        if total_supply_result is not None and total_supply_result.success:
            total_supply = share_token.convert_to_decimals(convert_int256_bytes_to_int(total_supply_result.result))
        else:
            errors.append("lpTokenTotalSupply call failed")

        if total_assets == 0:
            errors.append(f"getTotalAssets returned zero: {total_assets_result}")

        if total_supply == 0:
            errors.append(f"lpTokenTotalSupply returned zero: {total_supply_result}")

        deposits_paused_result = call_by_name.get("depositsPaused")
        if deposits_paused_result is not None and deposits_paused_result.success:
            deposits_open = not bool(convert_int256_bytes_to_int(deposits_paused_result.result))

        withdrawals_paused_result = call_by_name.get("withdrawalsPaused")
        if withdrawals_paused_result is not None and withdrawals_paused_result.success:
            redemption_open = not bool(convert_int256_bytes_to_int(withdrawals_paused_result.result))

        max_deposit_result = call_by_name.get("maxDepositAmount")
        if denomination_token is not None and max_deposit_result is not None and max_deposit_result.success:
            max_deposit = denomination_token.convert_to_decimals(convert_int256_bytes_to_int(max_deposit_result.result))

        max_withdrawal_result = call_by_name.get("maxWithdrawalAmount")
        if denomination_token is not None and max_withdrawal_result is not None and max_withdrawal_result.success:
            # Upshift names this value as a withdrawal amount, but it is
            # denomination-token liquidity, not share-token ``maxRedeem``.
            available_liquidity = denomination_token.convert_to_decimals(convert_int256_bytes_to_int(max_withdrawal_result.result))

        if share_price_result is not None and share_price_result.state is not None and share_price is not None:
            share_price_result.state.on_called(
                share_price_result,
                total_assets=total_assets,
                share_price=share_price,
            )

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
            deposits_open=deposits_open,
            redemption_open=redemption_open,
            available_liquidity=available_liquidity,
        )


class UpshiftVault(ERC4626Vault):
    """Upshift protocol vaults.

    Upshift democratises institutional-grade DeFi yield strategies through non-custodial vaults
    built on August infrastructure.

    The adapter supports two observed contract families:

    - TokenizedAccount ERC-4626 vaults, such as Upshift AZT.
    - Upshift ``multiAssetVault`` proxies, such as RockawayX's Tori Ecosystem
      Vault and Earn ctUSD vault. These expose accounting on the vault proxy
      and share-token metadata through ``lpTokenAddress()``.

    Links:

    - `Homepage <https://www.upshift.finance/>`__
    - `Documentation <https://docs.upshift.finance/>`__
    - `Example vault on Etherscan <https://etherscan.io/address/0x69fc3f84fd837217377d9dae0212068ceb65818e>`__
    - `Implementation contract on Etherscan <https://etherscan.io/address/0x83AF2736AD2f59BA60F2da1493DE95730Bc0649d#code>`__
    - `Multi-asset implementation on Etherscan <https://etherscan.io/address/0xEB5f80aCEa6060764E91c185bE93752Ab40F01c2#code>`__
    - `Tori Ecosystem Vault <https://etherscan.io/address/0xcd69123b3FBBfC666E1f6a501da27B564C00De54>`__
    - `Earn ctUSD <https://etherscan.io/address/0xc87DBBB8C67e4F19fCD2E297c05937567b2572Ce>`__
    - `Twitter <https://x.com/upshift_fi>`__

    Fee mechanism:

    Upshift vaults have multiple fee types that are configured per-vault and managed by the vault operator.
    The fee functions in the smart contract include:

    - ``withdrawalFee()``: Fee charged on standard withdrawals
    - ``instantRedemptionFee()``: Higher fee for immediate redemptions bypassing the claim queue
    - Management fees are charged periodically via ``chargeManagementFee()``

    See the `TokenizedAccount implementation <https://etherscan.io/address/0x83AF2736AD2f59BA60F2da1493DE95730Bc0649d#code>`__
    for the fee collection logic.
    """

    @cached_property
    def multi_asset_like(self) -> bool:
        """Is this an Upshift multi-asset vault.

        :return:
            True when the feature detector saw ``assetsWhitelistAddress()`` on
            the vault proxy.
        """

        return bool(self.features and ERC4626Feature.upshift_multi_asset_like in self.features)

    @cached_property
    def vault_contract(self) -> Contract:
        """Get the vault deployment.

        Multi-asset vaults need a dedicated ABI because their accounting
        methods are not part of the generic ERC-4626 ABI.

        :return:
            Web3 contract proxy for the vault address.
        """

        if self.multi_asset_like:
            return get_deployed_contract(
                self.web3,
                "upshift/MultiAssetVault.json",
                self.spec.vault_address,
            )

        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
        )

    @property
    def upshift_contract(self) -> Contract:
        """Alias for the Upshift implementation-specific vault contract."""

        return self.vault_contract

    @cached_property
    def assets_whitelist_contract(self) -> Contract:
        """Get the multi-asset denomination-token whitelist contract.

        :return:
            The configured whitelist contract.
        :raise ValueError:
            If this is not an Upshift multi-asset vault.
        """
        if not self.multi_asset_like:
            raise ValueError("Only Upshift multi-asset vaults have an assets whitelist")
        address = self.upshift_contract.functions.assetsWhitelistAddress().call()
        return get_deployed_contract(
            self.web3,
            "upshift/EnableOnlyAssetsWhitelist.json",
            address,
        )

    def fetch_all_denomination_tokens(self) -> tuple[TokenDetails, ...]:
        """Fetch every configured multi-asset denomination token.

        The returned tuple preserves the onchain whitelist ordering. That
        ordering determines the primary token returned by
        :meth:`fetch_denomination_token`.

        :return:
            Whitelisted denomination tokens in protocol order. Standard
            single-asset Upshift vaults return their normal denomination token.
        :raise ValueError:
            If a configured token cannot be read as an ERC-20.
        """
        if not self.multi_asset_like:
            token = super().fetch_denomination_token()
            return (token,) if token is not None else ()

        addresses = self.assets_whitelist_contract.functions.getWhitelistedAssets().call()
        if not addresses:
            # Older multi-asset proxies can expose a whitelist contract before
            # it has any configured assets. Their ERC-4626 asset remains the
            # only observable primary denomination token.
            token = super().fetch_denomination_token()
            return (token,) if token is not None else ()
        tokens = []
        for address in addresses:
            token = fetch_erc20_details(
                self.web3,
                address,
                chain_id=self.spec.chain_id,
                raise_on_error=False,
                cause_diagnostics_message=f"Upshift vault {self.address} denomination token lookup",
                cache=self.token_cache,
            )
            if token is None:
                raise ValueError(f"Could not fetch Upshift denomination token {address} for vault {self.address}")
            tokens.append(token)
        return tuple(tokens)

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Fetch Upshift's primary denomination token.

        **Supported simulation path**

        Multi-asset vaults use the first token in the onchain whitelist as
        their primary denomination token. The representative integration path
        uses a vault whose first token is USDC.

        **Known limitations**

        Only the first token is selected as the primary denomination token.
        Remaining whitelisted tokens are discovered by
        :meth:`fetch_all_denomination_tokens` but are not supported by the
        deposit manager yet.

        :return:
            First whitelisted token for a multi-asset vault, or the normal
            ERC-4626 denomination token for a standard Upshift vault.
        """
        tokens = self.fetch_all_denomination_tokens()
        return tokens[0] if tokens else None

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Get the share token address.

        Upshift multi-asset vault proxies keep ERC-20 share metadata on a
        separate LP token returned by ``lpTokenAddress()``. TokenizedAccount
        vaults remain standard ERC-4626 share-token contracts.

        :param block_identifier:
            Block number or ``"latest"``.

        :return:
            Share token address.
        """

        if self.multi_asset_like:
            return self.upshift_contract.functions.lpTokenAddress().call(block_identifier=block_identifier)

        return super().fetch_share_token_address(block_identifier)

    def fetch_total_supply(self, block_identifier: BlockIdentifier) -> Decimal:
        """Fetch current outstanding share supply.

        For Upshift multi-asset vaults, :py:meth:`fetch_share_token_address`
        remaps the share token to the LP token returned by ``lpTokenAddress()``.
        Keeping this method explicit documents that all inherited callers of
        ``fetch_total_supply()`` use LP token supply, not the vault proxy address.

        :param block_identifier:
            Block number or ``"latest"``.

        :return:
            LP token supply in share token units.
        """

        return super().fetch_total_supply(block_identifier)

    def has_custom_fees(self) -> bool:
        """Upshift has withdrawal and instant redemption fees."""
        return True

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get management fee.

        The supported Upshift ABIs expose fee charging hooks, but not a single
        protocol-wide management-fee convention that maps cleanly to the shared
        historical schema.

        :param block_identifier:
            Block number or ``"latest"``.

        :return:
            ``None`` because fee extraction is not yet implemented.
        """

        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get performance fee.

        :param block_identifier:
            Block number or ``"latest"``.

        :return:
            ``None`` because fee extraction is not yet implemented.
        """

        return None

    def fetch_total_assets(self, block_identifier: BlockIdentifier) -> Decimal | None:
        """Fetch vault NAV in denomination token units.

        Multi-asset vaults use ``getTotalAssets()`` instead of the ERC-4626
        ``totalAssets()`` function.

        :param block_identifier:
            Block number or ``"latest"``.

        :return:
            NAV in denomination token units, or ``None`` if the denomination
            token is unavailable.
        """

        if self.multi_asset_like:
            raw_amount = self.upshift_contract.functions.getTotalAssets().call(block_identifier=block_identifier)
            if self.underlying_token is not None:
                return self.underlying_token.convert_to_decimals(raw_amount)
            return None

        return super().fetch_total_assets(block_identifier)

    def fetch_share_price(self, block_identifier: BlockIdentifier) -> Decimal:
        """Fetch the current share price.

        Multi-asset vaults expose the canonical price through
        ``getSharePrice()``.

        :param block_identifier:
            Block number or ``"latest"``.

        :return:
            Share price in denomination token units.
        """

        if self.multi_asset_like:
            token = self.denomination_token
            raw_amount = self.upshift_contract.functions.getSharePrice().call(block_identifier=block_identifier)
            return token.convert_to_decimals(raw_amount)

        return super().fetch_share_price(block_identifier)

    def fetch_nav(self, block_identifier=None) -> Decimal | None:
        """Fetch the most recent onchain NAV value.

        :param block_identifier:
            Block number or ``"latest"``.

        :return:
            NAV in denomination token units.
        """

        if self.multi_asset_like:
            return self.fetch_total_assets(block_identifier)

        return super().fetch_nav(block_identifier)

    def get_deposit_manager(self) -> "eth_defi.erc_4626.deposit_redeem.ERC4626DepositManager":
        """Get deposit/redeem manager.

        The generic ERC-4626 deposit manager calls ERC-4626 deposit/redeem
        functions on the vault address. Upshift multi-asset vaults are proxy
        accounting contracts and use protocol-specific deposit flows through the
        Upshift app, so exposing the generic manager would be misleading.
        """

        if self.multi_asset_like:
            raise NotImplementedError("Upshift multi-asset vault deposits are not supported by the generic ERC-4626 deposit manager")

        return super().get_deposit_manager()

    def get_deposit_manager_capability(self) -> "VaultDepositManagerCapability | None":
        """Declare only Upshift's normal ERC-4626 vault shape.

        Multi-asset accounting vaults use a separate application flow and must
        never be represented as generic deposit-manager support.

        :return:
            Synchronous two-way capability for the normal shape, or ``None``
            for multi-asset vaults.
        """
        if self.multi_asset_like:
            return None

        from eth_defi.vault.deposit_redeem import VaultDepositManagerCapability

        return VaultDepositManagerCapability(
            can_deposit=True,
            can_redeem=True,
            deposit_flow="synchronous",
            redemption_flow="synchronous",
        )

    def can_check_deposit(self) -> bool:
        """Can the generic ERC-4626 ``maxDeposit(address(0))`` probe be used."""

        if self.multi_asset_like:
            return False

        return super().can_check_deposit()

    def fetch_deposit_closed_reason(self) -> str | None:
        """Fetch live deposit closure reason.

        Upshift multi-asset vaults expose deposit availability with
        ``depositsPaused()`` and ``maxDepositAmount()`` instead of ERC-4626
        ``maxDeposit(address)``.
        """

        if not self.multi_asset_like:
            return super().fetch_deposit_closed_reason()

        if self.upshift_contract.functions.depositsPaused().call():
            return "Upshift depositsPaused() is true"

        raw_max_deposit = self.upshift_contract.functions.maxDepositAmount().call()
        if raw_max_deposit == 0:
            return "Upshift maxDepositAmount() is zero"

        return None

    def fetch_redemption_closed_reason(self) -> str | None:
        """Fetch live redemption closure reason.

        Upshift multi-asset vaults expose redemption availability with
        ``withdrawalsPaused()`` and ``maxWithdrawalAmount()`` instead of
        ERC-4626 ``maxRedeem(address)``.
        """

        if not self.multi_asset_like:
            return super().fetch_redemption_closed_reason()

        if self.upshift_contract.functions.withdrawalsPaused().call():
            return "Upshift withdrawalsPaused() is true"

        raw_max_withdrawal = self.upshift_contract.functions.maxWithdrawalAmount().call()
        if raw_max_withdrawal == 0:
            return "Upshift maxWithdrawalAmount() is zero"

        return None

    def fetch_available_liquidity(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Fetch immediately available withdrawal liquidity.

        For Upshift multi-asset vaults, ``maxWithdrawalAmount()`` is denominated
        in the vault denomination token and mirrors the value exported by the
        historical reader as ``available_liquidity``.

        :param block_identifier:
            Block number or ``"latest"``.

        :return:
            Available withdrawal liquidity in denomination token units.
        """

        if not self.multi_asset_like:
            return super().fetch_available_liquidity(block_identifier)

        token: TokenDetails | None = self.denomination_token
        if token is None:
            return None

        raw_amount = self.upshift_contract.functions.maxWithdrawalAmount().call(block_identifier=block_identifier)
        return token.convert_to_decimals(raw_amount)

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Get the historical reader for this Upshift vault.

        :param stateful:
            Whether to use adaptive reader state.

        :return:
            Upshift multi-asset reader for multi-asset vaults, otherwise the
            generic ERC-4626 reader.
        """

        if self.multi_asset_like:
            return UpshiftMultiAssetHistoricalReader(self, stateful=stateful)

        return super().get_historical_reader(stateful)

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Upshift vaults use a daily claim processing system.

        Withdrawals are processed through a request-claim system where users
        request redemption and then claim on designated days. Some curated
        pre-deposit vaults can have longer strategy-specific lock-ups; these
        are not exposed through the generic adapter.
        """
        return datetime.timedelta(days=1)

    def get_link(self, referral: str | None = None) -> str:
        """Get link to the vault on Upshift app.

        URL format: https://app.upshift.finance/pools/{chain_id}/{checksummed_address}
        """
        chain_id = self.chain_id
        checksummed_address = Web3.to_checksum_address(self.vault_address)
        return f"https://app.upshift.finance/pools/{chain_id}/{checksummed_address}"
