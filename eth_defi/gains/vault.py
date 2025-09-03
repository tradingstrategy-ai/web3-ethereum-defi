"""Morpho vault reading implementation."""

import datetime
from functools import cached_property

from eth_typing import BlockIdentifier
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.event_reader.conversion import convert_bytes32_to_address, convert_string_to_bytes32
from eth_defi.event_reader.multicall_batcher import EncodedCall
from eth_defi.utils import from_unix_timestamp


from web3.exceptions import BadFunctionCallOutput


class GainsVault(ERC4626Vault):
    """Gains-like vault support.

    - GToken vaults
    - Gains (gTrade) and Ostium LPs
    - gUSDC vault implementation contract https://arbiscan.io/address/0xeb754588eff264793bb80be65866d1
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="gains/GToken.json",
        )

    @cached_property
    def gains_open_trades_pnl_feed(self) -> Contract | None:
        """Get Gains PnL feed contract."""

        # OstiumVault has `registry()` call to get the registry address
        openTradesPnlFeed_call = EncodedCall.from_keccak_signature(
            address=self.address,
            signature=Web3.keccak(text="openTradesPnlFeed()")[0:4],
            function="openTradesPnlFeed",
            data=b"",
            extra_data=None,
        )

        try:
            result = openTradesPnlFeed_call.call(
                web3=self.web3,
                block_identifier="latest",
            )
            addr = convert_bytes32_to_address(result)
            return get_deployed_contract(
                self.web3,
                "gains/OstiumOpenPnl.json",
                addr,
            )
        except(ValueError, BadFunctionCallOutput):
            pass

        return None

    @cached_property
    def open_pnl_contract(self) -> Contract:
        """Get OpenPNL contract.

        - Needed for epoch calls
        """

        gains_open_trades_pnl_feed = self.gains_open_trades_pnl_feed
        if gains_open_trades_pnl_feed is not None:
            return gains_open_trades_pnl_feed

        ostium_registry = self.ostium_registry
        if ostium_registry is not None:
            open_pnl_address = ostium_registry.functions.getContractAddress("openPnl").call(
                web3=self.web3,
                block_identifier="latest",
            )
            return get_deployed_contract(
                self.web3,
                "gains/OstiumOpenPnl.json",
                open_pnl_address,
            )

        raise NotImplementedError(f"Does not know this Gains-like vault structure")

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """No management fee"""
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """No performance fee"""
        return 0.0

    def fetch_epoch_duration(self) -> datetime.timedelta:
        """How long are epochs for this vault."""
        # https://github.com/0xOstium/smart-contracts-public/blob/da3b944623bef814285b7f418d43e6a95f4ad4b1/src/OstiumOpenPnl.sol
        open_pnl = self.open_pnl_contract
        # 21600 uint256 on Gains
        request_every = open_pnl.functions.requestsEvery().call()
        return datetime.timedelta(seconds=request_every)

    def fetch_withdraw_epochs_time_lock(self) -> int:
        """Fetch withdraw time lock in epochs.

        - The currently available epochs
        - This depends on how overcollateralised the vault is currently
        - https://medium.com/gains-network/introducing-gtoken-vaults-ea98f10a49d5

        Epoch is set in function `updateAccPnlPerTokenUsed()` called by
        `openTradesPnlFeed` (Gains) or `registry.getContractAddress('openPnl')` (Ostium).

        :return:
            Number of epochs
        """
        return self.vault_contract.functions.withdrawEpochsTimelock().call()

    def fetch_current_epoch_start(self) -> datetime.datetime:
        """When the current epoch started."""
        unix_timestamp = self.vault_contract.functions.currentEpochStart().call()
        return from_unix_timestamp(unix_timestamp)

    def estimate_withdraw_timeout(self) -> datetime.datetime:
        """How long we need to wait for withdraw if we start now."""
        epochs = self.fetch_withdraw_epochs_time_lock()
        now_ = native_datetime_utc_now()
        # How much the current epoch has passed
        gone = now_ - self.fetch_current_epoch_start()
        return now_ + (epochs * self.fetch_epoch_duration()) - gone


class OstiumVault(GainsVault):
    """Ostium vault is a Gains-like vault.

    - OstiumVault.sol https://github.com/0xOstium/smart-contracts-public/blob/da3b944623bef814285b7f418d43e6a95f4ad4b1/src/OstiumVault.sol#L243
    - OstiumVault on Arbitrum
    - OstiumOpenPnl https://arbiscan.io/address/0xe607ac9ff58697c5978afa1fc1c5c437a6d1858c

    """

    @cached_property
    def ostium_registry(self) -> Contract | None:
        """Get Ostium registry contract.

        - https://github.com/0xOstium/smart-contracts-public/blob/da3b944623bef814285b7f418d43e6a95f4ad4b1/src/OstiumRegistry.sol
        -
        """

        # OstiumVault has `registry()` call to get the registry address
        registry_call = EncodedCall.from_keccak_signature(
            address=self.vault_contract.address,
            signature=Web3.keccak(text="registry()")[0:4],
            function="registry",
            data=b"",
            extra_data=None,
        )

        try:
            result = registry_call.call(
                web3=self.web3,
                block_identifier="latest",
            )
            registry_address = convert_bytes32_to_address(result)
            return get_deployed_contract(
                self.web3,
                "gains/OstiumRegistry.json",
                registry_address,
            )
        except(ValueError, BadFunctionCallOutput):
            pass

        return None

    @cached_property
    def open_pnl_contract(self) -> Contract:
        """Get OpenPNL contract.

        - Needed for epoch calls
        """

        ostium_registry = self.ostium_registry
        if ostium_registry is not None:
            marker = convert_string_to_bytes32("openPnl")
            open_pnl_address = ostium_registry.functions.getContractAddress(marker).call()
            return get_deployed_contract(
                self.web3,
                "gains/OstiumOpenPnl.json",
                open_pnl_address,
            )

        raise NotImplementedError(f"Does not know this Gains-like vault structure")
