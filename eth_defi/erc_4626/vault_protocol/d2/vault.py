"""D2 Finance vault support."""

import datetime
from dataclasses import dataclass
from functools import cached_property
import logging
from typing import Iterable

from web3.contract import Contract
from eth_typing import BlockIdentifier

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.compat import native_datetime_utc_now
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.utils import from_unix_timestamp
from eth_defi.vault.base import (
    DEPOSIT_CLOSED_FUNDING_PHASE,
    REDEMPTION_CLOSED_FUNDS_CUSTODIED,
    VaultHistoricalRead,
    VaultHistoricalReader,
    VaultTechnicalRisk,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class Epoch:
    funding_start: datetime.datetime
    epoch_start: datetime.datetime
    epoch_end: datetime.datetime


class D2HistoricalReader(ERC4626HistoricalReader):
    """Read D2 Finance vault core data + epoch-based deposit/redemption/trading state.

    - Deposits are open during the funding phase (``isFunding()``)
    - Redemptions are open when funds are not custodied and not during epoch
      (``notCustodiedAndNotDuringEpoch()``)
    - Trading is active when the vault is in an epoch (``isInEpoch()``)
    """

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        yield from self.construct_core_erc_4626_multicall()
        yield from self.construct_d2_epoch_calls()

    def construct_d2_epoch_calls(self) -> Iterable[EncodedCall]:
        """Add D2-specific epoch state calls."""
        is_funding = EncodedCall.from_contract_call(
            self.vault.vault_contract.functions.isFunding(),
            extra_data={
                "function": "isFunding",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield is_funding

        is_in_epoch = EncodedCall.from_contract_call(
            self.vault.vault_contract.functions.isInEpoch(),
            extra_data={
                "function": "isInEpoch",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield is_in_epoch

        not_custodied_and_not_during_epoch = EncodedCall.from_contract_call(
            self.vault.vault_contract.functions.notCustodiedAndNotDuringEpoch(),
            extra_data={
                "function": "notCustodiedAndNotDuringEpoch",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield not_custodied_and_not_during_epoch

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        call_by_name = self.dictify_multicall_results(block_number, call_results)

        # Decode common variables
        share_price, total_supply, total_assets, errors, max_deposit = self.process_core_erc_4626_result(call_by_name)

        # Decode D2-specific epoch state
        deposits_open = None
        is_funding_result = call_by_name.get("isFunding")
        if is_funding_result and is_funding_result.success:
            deposits_open = bool(convert_int256_bytes_to_int(is_funding_result.result))

        trading = None
        is_in_epoch_result = call_by_name.get("isInEpoch")
        if is_in_epoch_result and is_in_epoch_result.success:
            trading = bool(convert_int256_bytes_to_int(is_in_epoch_result.result))

        redemption_open = None
        not_custodied_result = call_by_name.get("notCustodiedAndNotDuringEpoch")
        if not_custodied_result and not_custodied_result.success:
            redemption_open = bool(convert_int256_bytes_to_int(not_custodied_result.result))

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
            trading=trading,
        )


class D2Vault(ERC4626Vault):
    """D2 Finance vaults.

    - Most vault logic is offchain, proprietary
    - VaultV1Whitelisted is a wrapper around Hyperliquid trading account
    - You need to hold a minimum amount of USDC (whitelistedAsset) to be able to deposit
    - The vault smart contract does not have visibility to the fees
    - Redemption must happen not during epoch
    - Fees are set and calculated offchain
    - The vaults have funding, trading and withdraw phases and you can only deposit/withdraw on the correct epoch
    - Lockups are up to 30-60 days or so
    - The vault owner can set epochs offhain, up to 10 years

    More information:

    - `Docs <https://gitbook.d2.finance/>`__
    - `HYPE++ strategy blog post <https://medium.com/@D2.Finance/hype-capitalizing-on-hyperliquids-launch-396f8665a2c0>`__

    Whitelist function logic:

    .. code-block:: solidity

            modifier onlyWhitelisted() {
                bool holder = false;
                if (whitelistAsset != address(0)) {
                    holder = IERC20(whitelistAsset).balanceOf(msg.sender) > whitelistBalance;
                }
                require(whitelisted[msg.sender] || holder, "!whitelisted");
                _;
            }

    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment.

        - Example impl https://arbiscan.io/address/0x350856A672e7bF7D7327c8a5e72Ac49833DBfB75#code
        """
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="d2/VaultV1Whitelisted.json",
        )

    def get_historical_reader(self, stateful) -> VaultHistoricalReader:
        return D2HistoricalReader(self, stateful)

    def fetch_current_epoch_id(self) -> int:
        return self.vault_contract.functions.getCurrentEpoch().call()

    def fetch_current_epoch_info(self) -> Epoch:
        data = self.vault_contract.functions.getCurrentEpochInfo().call()
        return Epoch(
            funding_start=from_unix_timestamp(data[0]),
            epoch_start=from_unix_timestamp(data[1]),
            epoch_end=from_unix_timestamp(data[2]),
        )

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Non on-chain fee information available.

        - D2 share price is fees-inclusive per them: https://x.com/D2_Finance/status/1988624499588116979
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fees are internalized in the share price.

        - D2 share price is fees-inclusive per them: https://x.com/D2_Finance/status/1988624499588116979
        """
        return 0.20

    def get_estimated_lock_up(self) -> datetime.timedelta:
        epoch = self.fetch_current_epoch_info()
        return epoch.epoch_end - epoch.epoch_start

    def fetch_deposit_closed_reason(self) -> str | None:
        """Deposits open during isFunding() phase."""
        try:
            is_funding = self.vault_contract.functions.isFunding().call()
            if not is_funding:
                next_open = self.fetch_deposit_next_open()
                if next_open:
                    remaining = next_open - native_datetime_utc_now()
                    hours = remaining.total_seconds() / 3600
                    if hours < 24:
                        return f"{DEPOSIT_CLOSED_FUNDING_PHASE} (opens in {hours:.0f}h)"
                    return f"{DEPOSIT_CLOSED_FUNDING_PHASE} (opens in {hours / 24:.1f}d)"
                return DEPOSIT_CLOSED_FUNDING_PHASE
        except Exception:
            pass
        return None

    def fetch_redemption_closed_reason(self) -> str | None:
        """Redemptions open when notCustodiedAndNotDuringEpoch()."""
        try:
            can_redeem = self.vault_contract.functions.notCustodiedAndNotDuringEpoch().call()
            if not can_redeem:
                next_open = self.fetch_redemption_next_open()
                if next_open:
                    remaining = next_open - native_datetime_utc_now()
                    hours = remaining.total_seconds() / 3600
                    if hours < 24:
                        return f"{REDEMPTION_CLOSED_FUNDS_CUSTODIED} (opens in {hours:.0f}h)"
                    return f"{REDEMPTION_CLOSED_FUNDS_CUSTODIED} (opens in {hours / 24:.1f}d)"
                return REDEMPTION_CLOSED_FUNDS_CUSTODIED
        except Exception:
            pass
        return None

    def fetch_deposit_next_open(self) -> datetime.datetime | None:
        """Get when deposits will next be open.

        - Deposits open at the start of the next funding phase (after epoch ends)
        """
        try:
            if self.vault_contract.functions.isFunding().call():
                return None  # Already open
            epoch = self.fetch_current_epoch_info()
            return epoch.epoch_end  # Next funding starts after epoch ends
        except Exception:
            return None

    def fetch_redemption_next_open(self) -> datetime.datetime | None:
        """Get when withdrawals will next be open.

        - Redemptions open when funds are not custodied and not during epoch
        """
        try:
            if self.vault_contract.functions.notCustodiedAndNotDuringEpoch().call():
                return None  # Already open
            epoch = self.fetch_current_epoch_info()
            return epoch.epoch_end
        except Exception:
            return None
