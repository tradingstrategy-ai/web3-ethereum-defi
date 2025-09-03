"""Morpho vault reading implementation."""

import datetime
import logging
from dataclasses import dataclass

from decimal import Decimal
from functools import cached_property

from web3 import Web3
from web3.contract.contract import Contract, ContractFunction
from web3.exceptions import BadFunctionCallOutput
from web3._utils.events import EventLogErrorFlags

from eth_defi.abi import get_deployed_contract
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.event_reader.conversion import convert_bytes32_to_address, convert_string_to_bytes32
from eth_defi.event_reader.multicall_batcher import EncodedCall
from eth_defi.utils import from_unix_timestamp
from eth_typing import BlockIdentifier, HexAddress
from hexbytes import HexBytes


from eth_defi.vault.base import RedemptionRequest, RedemptionTicket, CannotParseRedemptionTransaction

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GainsRedemptionTicket(RedemptionTicket):
    """Gains redemption ticket details."""
    current_epoch: int
    unlock_epoch: int


class GainsRedemptionRequest(RedemptionRequest):
    """Wrap Gains makeWithdrawRequest() call.

    - Revert reason: execution reverted: `custom error 0xa73449b9`: `EndOfEpoch`

    See errors at:

    - https://www.codeslaw.app/contracts/arbitrum/0xeb754588eff264793bb80be65866d11bc8d6cbdd?tab=abi
    """

    def parse_redeem_transaction(self, tx_hashes: list[HexBytes]) -> GainsRedemptionTicket:
        """Parse the transaction receipt to get the actual shares redeemed.

        - Assumes only one redemption request per vault per transaction
        """

        # Ignore epoch request tx
        tx_hash = tx_hashes[-1]

        assert isinstance(tx_hash, HexBytes)

        receipt = self.vault.web3.eth.get_transaction_receipt(tx_hash)
        assert receipt is not None, f"Transaction is not yet mined: {tx_hash.hex()}"

        logs = self.vault.vault_contract.events.WithdrawRequested().process_receipt(receipt, errors=EventLogErrorFlags.Ignore)
        if len(logs) != 1:
            raise CannotParseRedemptionTransaction(f"Expected exactly one WithdrawRequested event, got logs: {logs} at {tx_hash.hex()}")

        log = logs[0]

        return GainsRedemptionTicket(
            vault_address=self.vault.address,
            owner=self.owner,
            to=self.to,
            raw_shares=log["args"]["shares"],
            tx_hash=tx_hash,
            current_epoch=log["args"]["currEpoch"],
            unlock_epoch=log["args"]["unlockEpoch"],
        )


class GainsVault(ERC4626Vault):
    """Gains-like vault support.

    - GToken vaults
    - Gains (gTrade) and Ostium LPs
    - gUSDC vault implementation contract https://arbiscan.io/address/0xeb754588eff264793bb80be65866d1

    Deposit/redeem

    - Gains supports ERC-4626 deposit interface
    - `Gains has custom withdrawal and PnL mechanism to smooth out losses, based on epochs <https://medium.com/gains-network/introducing-gtoken-vaults-ea98f10a49d5>`__
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
        - See `OstiumOpenPnl.sol`
        """

        gains_open_trades_pnl_feed = self.gains_open_trades_pnl_feed
        if gains_open_trades_pnl_feed is not None:
            return gains_open_trades_pnl_feed

        raise NotImplementedError(f"Does not know this Gains-like vault structure")

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """No management fee"""
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """No performance fee"""
        return 0.0

    def fetch_current_epoch(self) -> int:
        """Get the current epoch number."""
        return self.vault_contract.functions.currentEpoch().call()

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

    def estimate_withdraw_timeout(self, now_: datetime.datetime = None) -> datetime.datetime | None:
        """How long we need to wait for withdraw if we start now."""
        epochs = self.fetch_withdraw_epochs_time_lock()
        if now_ is None:
            now_ = native_datetime_utc_now()
        # How much the current epoch has passed
        gone = now_ - self.fetch_current_epoch_start()
        assert gone > datetime.timedelta(0), f"Epoch start is in the future: {gone}"

        need_to_wait = epochs * self.fetch_epoch_duration()

        if need_to_wait < gone:
            # Wait is over
            return None

        return now_ + (epochs * self.fetch_epoch_duration()) - gone

    def create_redemption_request(
        self,
        owner: HexAddress,
        shares: Decimal = None,
        raw_shares: int = None,
    ) -> GainsRedemptionRequest:
        """Build a redeem transction.

        .. note::

            Withdrawal requests can only be executed in the first 2 days of each epoch.

        `Notes on Gains / Ostium withdrawals <https://medium.com/gains-network/introducing-gtoken-vaults-ea98f10a49d5>`__.

        :param owner:
            Deposit owner.

        :param shares:
            Share amount in decimal.

            Will be converted to `raw_shares` using `share_token` decimals.

        :param raw_shares:
            Raw amount in share token
        """

        assert raw_shares or shares

        if not raw_shares:
            raw_amount = self.share_token.convert_to_raw(shares)
        else:
            raw_amount = raw_shares

        assert type(raw_amount) == int, f"Got {raw_amount} {type(raw_amount)}"
        shares = self.share_token
        block_number = self.web3.eth.block_number

        # Check we have shares
        owned_raw_amount = shares.fetch_raw_balance_of(owner, block_number)
        assert owned_raw_amount >= raw_amount, f"Cannot redeem, has only {owned_raw_amount} shares when {raw_amount} needed"

        human_amount = shares.convert_to_decimals(raw_amount)
        total_shares = self.fetch_total_supply(block_number)
        logger.info("Setting up redemption for %s %s shares out of %s, for %s", human_amount, shares.symbol, total_shares, owner)

        # This is the underlying withdrawal request.
        # It will revert unless there is an epoch in progress
        func_1 = self.vault_contract.functions.makeWithdrawRequest(
            raw_amount,
            owner,
        )
        return GainsRedemptionRequest(
            vault=self,
            owner=owner,
            to=owner,
            shares=human_amount,
            raw_shares=raw_amount,
            funcs=[func_1],
        )

    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        """Gains allows request redemptioon only two first dayas of three days epoch.

        :return:
            True if can create a redemption request now
        """
        return self.open_pnl_contract.functions.nextEpochValuesRequestCount().call() == 0

    def can_redeem(
        self,
        redemption_ticket: GainsRedemptionTicket,
    ):
        """Check if the redemption request can be redeemed now.

        - Phase 2 of redemption, after settlement

        :param redemption_request_ticket:
            Redemption request ticket from `create_redemption_request()`

        :return:
            True if can be redeemed now
        """
        assert isinstance(redemption_ticket, GainsRedemptionTicket)
        current_epoch = self.fetch_current_epoch()
        return current_epoch >= redemption_ticket.unlock_epoch

    def settle_redemption(
        self,
        redemption_ticket: GainsRedemptionTicket,
    ) -> ContractFunction:
        assert redemption_ticket.owner is not None
        assert redemption_ticket.to is not None
        return self.vault_contract.functions.redeem(
            redemption_ticket.raw_shares,
            redemption_ticket.owner,
            redemption_ticket.to,
        )

    def get_max_discount_percent(self) -> float:
        """Get max discount percent.

        Gains and Ostium allows you to lock LP for a discount:

            Staking DAI to the new vault and receiving gDAI can be done at any time during an epoch.

            You can also optionally receive a “discount” on your gDAI when staking DAI in the vault by choosing to lock your deposit for a certain period of time. The discount has two components: a time-based incentive and a collateralization-based incentive.

            Lock up your gDAI tokens when staking (from 2 weeks to 1 year).

            Mint gDAI anytime the collateralization ratio is below 150%. The discount is proportional to the collateralization level, with a maximum discount of 5%. At a collateralization ratio below 100%, the discount is 5%. Between 100%-150%, the discount linearly decreases from 5% to 0%.

        :return:
            0.05 for 5% discount
        """
        return self.vault_contract.functions.maxDiscountP().call() / 10**18 / 100


class OstiumVault(GainsVault):
    """Ostium vault is a Gains-like vault.

    - OstiumVault.sol https://github.com/0xOstium/smart-contracts-public/blob/da3b944623bef814285b7f418d43e6a95f4ad4b1/src/OstiumVault.sol#L243
    - OstiumVault on Arbitrum
    - OstiumOpenPnl https://arbiscan.io/address/0xe607ac9ff58697c5978afa1fc1c5c437a6d1858c

    What Ostium says:

        This repository is adapted from the Gains v5 open-source codebase. We credit the Gains Network contributors for their work, which served as a starting point for this implementation. Significant modifications and new functionality have been introduced to align with Ostium’s protocol architecture and design objectives. This includes integrations and components specific to our system. For reference, please consult the original Gains v5 repository for upstream logic and licensing information.
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
