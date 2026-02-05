"""Plutus hedge token vault support."""

import datetime
import logging
from typing import Iterable

from eth_typing import BlockIdentifier

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import (
    DEPOSIT_CLOSED_BY_ADMIN,
    REDEMPTION_CLOSED_BY_ADMIN,
    VaultHistoricalRead,
    VaultHistoricalReader,
)

logger = logging.getLogger(__name__)


class PlutusHistoricalReader(ERC4626HistoricalReader):
    """Read Plutus vault core data + deposit/redemption state via maxDeposit/maxRedeem.

    - Plutus vaults are manually opened/closed
    - Uses ERC-4626 ``maxDeposit(address(0))`` and ``maxRedeem(address(0))``
      to determine if deposits/redemptions are open
    - Trading state is not tracked (always ``None``)
    """

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        yield from self.construct_core_erc_4626_multicall()
        yield from self.construct_max_redeem_call()

    def construct_max_redeem_call(self) -> Iterable[EncodedCall]:
        """Plutus uses maxRedeem(address(0)) to signal whether redemptions are open."""
        max_redeem = EncodedCall.from_contract_call(
            self.vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR),
            extra_data={
                "function": "maxRedeem",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield max_redeem

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        call_by_name = self.dictify_multicall_results(block_number, call_results)

        # Decode common variables
        share_price, total_supply, total_assets, errors, max_deposit = self.process_core_erc_4626_result(call_by_name)

        # Derive deposits_open/redemption_open from maxDeposit/maxRedeem
        deposits_open = None
        if max_deposit is not None:
            deposits_open = max_deposit > 0

        max_redeem = None
        redemption_open = None
        max_redeem_result = call_by_name.get("maxRedeem")
        if max_redeem_result and max_redeem_result.success and self.vault.share_token is not None:
            raw_max_redeem = convert_int256_bytes_to_int(max_redeem_result.result)
            max_redeem = self.vault.share_token.convert_to_decimals(raw_max_redeem)
            redemption_open = max_redeem > 0

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
            max_redeem=max_redeem,
            deposits_open=deposits_open,
            redemption_open=redemption_open,
        )


class PlutusVault(ERC4626Vault):
    """Plutus vaults.

    - Hedge token vaults: https://plutus.fi/Vaults
    - Docs: https://docs.plutusdao.io/plutus-docs
    - About plHEDGE vault: https://medium.com/@plutus.fi/introducing-plvhedge-an-automated-funding-arbitrage-vault-f2f222fa8c56
    """

    def get_historical_reader(self, stateful) -> VaultHistoricalReader:
        return PlutusHistoricalReader(self, stateful)

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Hardcoded PLutus fees.

        - Fees are internalized in the share price, no explicit performance fee as per discussion in Plutus Discord
        - https://docs.plutusdao.io/plutus-docs/protocol-fees
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Hardcoded PLutus fees.

        - Fees are internalized in the share price, no explicit performance fee as per discussion in Plutus Discord
        - https://docs.plutusdao.io/plutus-docs/protocol-fees
        """
        return 0.12

    def get_estimated_lock_up(self) -> datetime.timedelta:
        """Currently Plutus vaults are manually opened/closed.

        We estimate one month lock-up for modelling purposes based on the discussion with Plutus in Discord.
        """
        return datetime.timedelta(days=30)

    def get_link(self, referral: str | None = None) -> str:
        # No vault pages
        return f"https://plutus.fi/Vaults"

    def fetch_deposit_closed_reason(self) -> str | None:
        """Check maxDeposit to determine if deposits are closed.

        Plutus vaults are manually opened/closed by admin.
        """
        try:
            max_deposit = self.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
            if max_deposit == 0:
                return f"{DEPOSIT_CLOSED_BY_ADMIN} (maxDeposit=0)"
        except Exception:
            pass
        return None

    def fetch_redemption_closed_reason(self) -> str | None:
        """Check maxRedeem to determine if redemptions are closed.

        Plutus vaults are manually opened/closed by admin.
        """
        try:
            max_redeem = self.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
            if max_redeem == 0:
                return f"{REDEMPTION_CLOSED_BY_ADMIN} (maxRedeem=0)"
        except Exception:
            pass
        return None

    def fetch_deposit_next_open(self) -> datetime.datetime | None:
        """Deposit timing is unpredictable - manually controlled."""
        return None

    def fetch_redemption_next_open(self) -> datetime.datetime | None:
        """Withdrawal timing is unpredictable - manually controlled."""
        return None

    def can_check_redeem(self) -> bool:
        """Plutus supports address(0) checks for redemption availability.

        - maxRedeem(address(0)) returns 0 when admin has closed redemptions
        """
        return True
