"""Gains/Ostium vault adapter.

For usage example ande details, see :py:class:`GainsVault`.

Notes:

- About epochs, fee and withdraw delays in gToken vaults https://medium.com/gains-network/introducing-gtoken-vaults-ea98f10a49d5

- gTrade contracts v6.1 https://github.com/GainsNetwork/gTrade-v6.1

- gUSDC vault implementation contract https://arbiscan.io/address/0xeb754588eff264793bb80be65866d11bc8d6cbdd#code

- gUSDC vault proxy contract https://arbiscan.io/address/0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0

- OstiumVault.sol https://github.com/0xOstium/smart-contracts-public/blob/da3b944623bef814285b7f418d43e6a95f4ad4b1/src/OstiumVault.sol#L243

- Ostium vault on Arbitrum https://arbiscan.io/address/0x20d419a8e12c45f88fda7c5760bb6923cee27f98

- Ostium implementation on Arbitrum https://arbiscan.io/address/0x738873f37b4b4bebe3545a277a27cdac77db99cd#code
"""

import datetime
import logging
from functools import cached_property
from typing import Iterable

from web3 import Web3
from web3.contract.contract import Contract
from web3.exceptions import BadFunctionCallOutput

from eth_defi.abi import get_deployed_contract
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.event_reader.conversion import convert_bytes32_to_address, convert_int256_bytes_to_int, convert_string_to_bytes32
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.utils import from_unix_timestamp
from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.vault.base import (
    DEPOSIT_CLOSED_CAP_REACHED,
    REDEMPTION_CLOSED_EPOCH_WINDOW,
    VaultHistoricalRead,
    VaultHistoricalReader,
)
from eth_typing import BlockIdentifier

from eth_defi.vault.risk import VaultTechnicalRisk

logger = logging.getLogger(__name__)


class GainsHistoricalReader(ERC4626HistoricalReader):
    """Read Gains/Ostium vault core data + epoch-based deposit/redemption state.

    - Deposits are always open for Gains/Ostium vaults
    - Redemptions are open only when ``nextEpochValuesRequestCount() == 0``
      on the open PnL contract (first 2 days of 3-day epoch)
    - Trading state is not tracked (always ``None``)
    """

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        yield from self.construct_core_erc_4626_multicall()
        yield from self.construct_gains_epoch_calls()

    def construct_gains_epoch_calls(self) -> Iterable[EncodedCall]:
        """Add epoch state calls for deposit/redemption window detection.

        - Uses ``accPnlPerTokenUsed()`` and ``currentMaxSupply()`` for deposit state
        - Uses ``nextEpochValuesRequestCount()`` for redemption state
        - Works for both Gains (via ``openTradesPnlFeed()``) and Ostium (via registry)
        """
        from eth_defi.vault.risk import BROKEN_VAULT_CONTRACTS

        # Add accPnlPerTokenUsed call for deposit state
        acc_pnl_call = EncodedCall.from_contract_call(
            self.vault.vault_contract.functions.accPnlPerTokenUsed(),
            extra_data={
                "function": "accPnlPerTokenUsed",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield acc_pnl_call

        # Add currentMaxSupply call for deposit cap check
        max_supply_call = EncodedCall.from_contract_call(
            self.vault.vault_contract.functions.currentMaxSupply(),
            extra_data={
                "function": "currentMaxSupply",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield max_supply_call

        try:
            open_pnl = self.vault.open_pnl_contract
        except (NotImplementedError, Exception):
            # Some Gains-like vaults may not have an open PnL contract
            return

        if open_pnl.address.lower() in BROKEN_VAULT_CONTRACTS:
            logger.warning("Skipping broken open PnL contract %s for vault %s", open_pnl.address, self.vault.address)
            return

        next_epoch_values_request_count = EncodedCall.from_contract_call(
            open_pnl.functions.nextEpochValuesRequestCount(),
            extra_data={
                "function": "nextEpochValuesRequestCount",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield next_epoch_values_request_count

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        call_by_name = self.dictify_multicall_results(block_number, call_results)

        # Decode common variables
        share_price, total_supply, total_assets, errors, max_deposit = self.process_core_erc_4626_result(call_by_name)

        # Epoch-based deposit logic:
        # Deposits closed when accPnlPerTokenUsed > 0 AND totalSupply >= currentMaxSupply
        deposits_open = True  # Default to open
        acc_pnl_result = call_by_name.get("accPnlPerTokenUsed")
        max_supply_result = call_by_name.get("currentMaxSupply")
        total_supply_result = call_by_name.get("totalSupply")

        if acc_pnl_result and acc_pnl_result.success and max_supply_result and max_supply_result.success:
            acc_pnl = convert_int256_bytes_to_int(acc_pnl_result.result)
            current_max_supply = convert_int256_bytes_to_int(max_supply_result.result)

            if acc_pnl > 0:
                # During PnL periods, deposits capped at currentMaxSupply - totalSupply
                if total_supply_result and total_supply_result.success:
                    raw_total_supply = convert_int256_bytes_to_int(total_supply_result.result)
                    if raw_total_supply >= current_max_supply:
                        deposits_open = False

        # Redemptions open when nextEpochValuesRequestCount == 0
        redemption_open = None
        epoch_result = call_by_name.get("nextEpochValuesRequestCount")
        if epoch_result and epoch_result.success:
            count = convert_int256_bytes_to_int(epoch_result.result)
            redemption_open = count == 0

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
        )


class GainsVault(ERC4626Vault):
    """Gains-like vault support.

    This covers gToken smart contract based vaults:

    - Gains (gTrade) GToken vaults
    - Ostium LPs
    - For example see `gUSDC vault implementation contract <https://arbiscan.io/address/0xeb754588eff264793bb80be65866d1>`__

    GToken is an `ERC-4626 <https://tradingstrategy.ai/glossary/erc-4626>`__ compatible vault with a custom functions and logic
    for redemptions. We provide a logic to handle this custom logic in :py:class:`eth_defi.gains.deposit_redeem.GainsDepositManager`.

    - `Gains has custom withdrawal and PnL mechanism to smooth out losses, based on epochs <https://medium.com/gains-network/introducing-gtoken-vaults-ea98f10a49d5>`__

    For more examples see :ref:`tutorials`.

    Deposit and redeem example:

    .. code-block:: python

        vault: GainsVault = create_vault_instance_autodetect(web3, "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0")

        amount = Decimal(100)

        tx_hash = usdc.approve(
            vault.address,
            amount,
        ).transact({"from": test_user})
        assert_transaction_success_with_explanation(web3, tx_hash)

        bound_func = deposit_4626(
            vault,
            test_user,
            amount,
        )
        tx_hash = bound_func.transact({"from": test_user})
        assert_transaction_success_with_explanation(web3, tx_hash)

        share_token = vault.share_token
        shares = share_token.fetch_balance_of(test_user)
        assert shares == pytest.approx(Decimal("81.54203"))

        # Withdrawals can be only executed on the first two days of an epoch.
        # We start in a state that is outside of this window, so we need to move to the next epoch first.
        assert vault.open_pnl_contract.functions.nextEpochValuesRequestCount().call() == 2
        assert vault.can_create_redemption_request(test_user) is False

        # 0. Clear epoch
        force_next_gains_epoch(
            vault,
            test_user,
        )

        # 1. Create a redemption request
        assert vault.open_pnl_contract.functions.nextEpochValuesRequestCount().call() == 0
        assert vault.can_create_redemption_request(test_user) is True, f"We have {vault.open_pnl_contract.functions.nextEpochValuesRequestCount().call()}"
        redemption_request = vault.create_redemption_request(
            owner=test_user,
            shares=shares,
        )
        assert isinstance(redemption_request, GainsRedemptionRequest)
        assert redemption_request.owner == test_user
        assert redemption_request.to == test_user
        assert redemption_request.shares == shares

        # 2.a) Broadcast and parse redemption request tx
        assert vault.open_pnl_contract.functions.nextEpochValuesRequestCount().call() == 0
        tx_hashes = []
        funcs = redemption_request.funcs
        tx_hash = funcs[0].transact({"from": test_user, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash)
        tx_hashes.append(tx_hash)

        # 2.b) Parse result
        redemption_ticket = redemption_request.parse_redeem_transaction(tx_hashes)
        assert redemption_ticket.raw_shares == pytest.approx(81.54203 * 10**6)
        assert redemption_ticket.owner == test_user
        assert redemption_ticket.to == test_user
        assert redemption_ticket.current_epoch == 197
        assert redemption_ticket.unlock_epoch == 200

        # Cannot redeem yet, need to wait for the next epoch
        assert vault.can_redeem(redemption_ticket) is False

        # 3. Move forward few epochs where our request unlocks
        for i in range(0, 3):
            force_next_gains_epoch(
                vault,
                test_user,
            )

        assert vault.fetch_current_epoch() >= 200

        # Cannot redeem yet, need to wait for the next epoch
        assert vault.can_redeem(redemption_ticket) is True

        # 4. Settle our redemption
        func = vault.settle_redemption(redemption_ticket)
        tx_hash = func.transact({"from": test_user})
        assert_transaction_success_with_explanation(web3, tx_hash)

        shares = share_token.fetch_balance_of(test_user)
        assert shares == 0
    """

    @property
    def name(self) -> str:
        return f"gTrade ({super().name})"

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
        except (ValueError, BadFunctionCallOutput):
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

    def estimate_redemption_ready(self, now_: datetime.datetime = None) -> datetime.datetime | None:
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

    def get_historical_reader(self, stateful) -> VaultHistoricalReader:
        return GainsHistoricalReader(self, stateful)

    def get_deposit_manager(self) -> "eth_defi.gains.deposit_redeem.GainsDepositManager":
        from eth_defi.erc_4626.vault_protocol.gains.deposit_redeem import GainsDepositManager

        return GainsDepositManager(self)

    def fetch_deposit_closed_reason(self) -> str | None:
        """Check maxDeposit to determine if deposits are closed.

        Deposits closed when vault reaches max supply during profitable periods.
        """
        try:
            max_deposit = self.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
            if max_deposit == 0:
                return f"{DEPOSIT_CLOSED_CAP_REACHED} (maxDeposit=0)"
        except Exception:
            pass
        return None

    def fetch_redemption_closed_reason(self) -> str | None:
        """Check epoch state - redemptions open when nextEpochValuesRequestCount == 0."""
        try:
            count = self.open_pnl_contract.functions.nextEpochValuesRequestCount().call()
            if count > 0:
                next_open = self.fetch_redemption_next_open()
                if next_open:
                    remaining = next_open - native_datetime_utc_now()
                    hours = remaining.total_seconds() / 3600
                    if hours < 24:
                        return f"{REDEMPTION_CLOSED_EPOCH_WINDOW} (opens in {hours:.0f}h)"
                    return f"{REDEMPTION_CLOSED_EPOCH_WINDOW} (opens in {hours / 24:.1f}d)"
                return REDEMPTION_CLOSED_EPOCH_WINDOW
        except Exception:
            pass
        return None

    def fetch_deposit_next_open(self) -> datetime.datetime | None:
        """Deposit timing unpredictable - depends on vault supply vs cap."""
        return None

    def fetch_redemption_next_open(self) -> datetime.datetime | None:
        """Get when withdrawals will next be open.

        - Redemptions open at the start of the next epoch when nextEpochValuesRequestCount resets to 0
        """
        try:
            count = self.open_pnl_contract.functions.nextEpochValuesRequestCount().call()
            if count == 0:
                return None  # Already open
            epoch_start = self.fetch_current_epoch_start()
            epoch_duration = self.fetch_epoch_duration()
            return epoch_start + epoch_duration
        except Exception:
            return None


class OstiumVault(GainsVault):
    """Ostium vault is a Gains-like vault.

    - OstiumVault.sol https://github.com/0xOstium/smart-contracts-public/blob/da3b944623bef814285b7f418d43e6a95f4ad4b1/src/OstiumVault.sol#L243
    - OstiumVault on Arbitrum
    - OstiumOpenPnl https://arbiscan.io/address/0xe607ac9ff58697c5978afa1fc1c5c437a6d1858c

    What Ostium says:

        This repository is adapted from the Gains v5 open-source codebase. We credit the Gains Network contributors for their work, which served as a starting point for this implementation. Significant modifications and new functionality have been introduced to align with Ostium’s protocol architecture and design objectives. This includes integrations and components specific to our system. For reference, please consult the original Gains v5 repository for upstream logic and licensing information.
    """

    @property
    def name(self) -> str:
        return f"Ostium Liquidity Pool Vault"

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="gains/OstiumVault.json",
        )

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
        except (ValueError, BadFunctionCallOutput):
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
