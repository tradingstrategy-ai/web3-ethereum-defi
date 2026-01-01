"""Umami gmUSDC vault support."""

import datetime
from decimal import Decimal
from functools import cached_property
import logging

from web3.contract import Contract

from eth_typing import BlockIdentifier
from eth_typing import HexAddress

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager, ERC4626DepositRequest
from eth_defi.erc_4626.flow import deposit_4626
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.vault.base import VaultTechnicalRisk

logger = logging.getLogger(__name__)


class UmamiVault(ERC4626Vault):
    """Umami vaults.

    - GMUSDC, etc: https://umami.finance/vaults/arbitrum/gm/gmusdc

    Umami vaults do not have open source Github repository, developer documentation or easy developer access for integrations,
    making it not recommended to deal with them.

    - Vault smart contract code: https://arbiscan.io/address/0x959f3807f0aa7921e18c78b00b2819ba91e52fef#code
    """

    def get_risk(self) -> VaultTechnicalRisk | None:
        return VaultTechnicalRisk.elevated

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="umami/AssetVault.json",
        )

    def fetch_aggregate_vault(self) -> Contract:
        addr = self.vault_contract.functions.aggregateVault().call()
        return get_deployed_erc_4626_contract(
            self.web3,
            addr,
            abi_fname="umami/AggregateVault.json",
        )

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees."""
        return True

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Umami fees hardcoded because no transparent development/onchain accessors.

        https://umami.finance/vaults/arbitrum/gm/gmusdc
        """
        return 0.02

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Umami fees hardcoded because no transparent development/onchain accessors.

        https://umami.finance/vaults/arbitrum/gm/gmusdc
        """
        return 0.20

    def get_estimated_lock_up(self) -> datetime.timedelta:
        return datetime.timedelta(days=3)

    def get_deposit_manager(self) -> "eth_defi.umami.vault.UmamiDepositManager":
        return UmamiDepositManager(self)


class UmamiDepositManager(ERC4626DepositManager):
    """Umami deposit manager with custom logic."""

    def create_deposit_request(
        self,
        owner: HexAddress,
        to: HexAddress = None,
        amount: Decimal = None,
        raw_amount: int = None,
        check_max_deposit=True,
        check_enough_token=True,
        max_slippage=0.01,
        gas=30_000_000,
    ) -> ERC4626DepositRequest:
        """Umami has a slippage tolerance on deposits.

        - Umami has a 0.15% deposit fee taken from the shares minted.
        - Umami deposit is gas hungry
        - Umami deposit must have ETH attached to the transaction as something spends it there

        .. code-block:: solidity

            // DEPOSIT & WITHDRAW
            // ------------------------------------------------------------------------------------------

            /**
             * @notice Deposit a specified amount of assets and mint corresponding shares to the receiver
             * @param assets The amount of assets to deposit
             * @param minOutAfterFees Minimum amount out after fees
             * @param receiver The address to receive the minted shares
             * @return shares The estimate amount of shares minted for the deposited assets
             */
            function deposit(uint256 assets, uint256 minOutAfterFees, address receiver)
                public
                payable
                override
                whenDepositNotPaused
                nonReentrant
                returns (uint256 shares)
            {
                // Check for rounding error since we round down in previewDeposit.
                require((shares = previewDeposit(assets)) != 0, "ZERO_SHARES");
                require(
                    totalAssets() + assets <= previewVaultCap() + asset.balanceOf(address(this)), "AssetVault: over vault cap"
                );
                // Transfer assets to aggregate vault, transfer before minting or ERC777s could reenter.
                asset.safeTransferFrom(msg.sender, address(this), assets);
                aggregateVault.handleDeposit{ value: msg.value }(assets, minOutAfterFees, receiver, msg.sender, address(0));

                emit Deposit(msg.sender, receiver, assets, shares);
            }

        ETH spend:

        .. code-block:: solidity

            /**
             * @notice Handles a deposit of a specified amount of an ERC20 asset into the AggregateVault from an account, with a deposit fee deducted.
             * @param assets The amount of the asset to be deposited.
             * @param account The address of the account from which the deposit will be made.
             */
            function handleDeposit(uint256 assets, uint256 minOutAfterFees, address account, address sender, address callback)
                external
                payable
                onlyAssetVault
            {
                if (assets == 0) revert AmountEqualsZero();
                if (account == address(0)) revert ZeroAddress();
                AVStorage storage stg = _getStorage();
                uint256 gas = _gasRequirement(callback != address(0));
                if (msg.value < gas * tx.gasprice) revert MinGasRequirement();

                // store request data
                uint256 key = _saveRequest(sender, account, msg.sender, callback, true, assets, minOutAfterFees);

                // send execution gas cost
                TransferUtils.transferNativeAsset(stg.rebalanceKeeper, msg.value);

                _executeHook(HookType.DEPOSIT_HOOK, msg.data[4:]);

                // emit request event
                Emitter(stg.emitter).emitDepositRequest(key, account, msg.sender);
            }
        """

        if not raw_amount:
            raw_amount = self.vault.denomination_token.convert_to_raw(amount)

        vault = self.vault
        from_ = owner
        receiver = owner

        logger.info(
            "Depositing to vault %s, amount %s, raw amount %s, from %s",
            vault.address,
            amount,
            raw_amount,
            from_,
        )

        preview_amount = vault.vault_contract.functions.previewDeposit(raw_amount).call()
        estimated_shares = vault.share_token.convert_to_decimals(preview_amount)

        min_shares = int(preview_amount * (1 - max_slippage))

        logger.info("Estimated %s shares before slippage: %s, slippage set to %s, min amount out %s", vault.share_token.symbol, estimated_shares, max_slippage, min_shares)

        contract = vault.vault_contract

        if not raw_amount:
            assert isinstance(amount, Decimal)
            assert amount > 0
            raw_amount = vault.denomination_token.convert_to_raw(amount)

        if check_enough_token:
            actual_balance_raw = vault.denomination_token.fetch_raw_balance_of(from_)
            assert actual_balance_raw >= raw_amount, f"Not enough token in {from_} to deposit {amount} to {vault.address}, has {actual_balance_raw}, tries to deposit {raw_amount}"

        if check_max_deposit:
            max_deposit = contract.functions.maxDeposit(receiver).call()
            if max_deposit != 0:
                assert raw_amount <= max_deposit, f"Max deposit {max_deposit} is less than {raw_amount}"

        call = contract.functions.deposit(raw_amount, min_shares, receiver)

        return ERC4626DepositRequest(
            vault=self.vault,
            owner=owner,
            to=owner,
            funcs=[call],
            amount=amount,
            raw_amount=raw_amount,
            gas=gas,
            value=Decimal(0.1),
        )
