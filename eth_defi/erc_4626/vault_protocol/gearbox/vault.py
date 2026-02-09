"""Gearbox Protocol vault support.

Gearbox Protocol is a composable leverage protocol that provides lending pools
compatible with ERC-4626. The PoolV3 contract manages liquidity deposits from
passive lenders and borrowing by credit accounts.

- Homepage: https://gearbox.finance/
- App: https://app.gearbox.fi/
- Documentation: https://docs.gearbox.finance/
- GitHub: https://github.com/Gearbox-protocol/core-v3
- Twitter: https://x.com/GearboxProtocol
- Audits: https://docs.gearbox.finance/risk-and-security/audits-bug-bounty

Fee structure:

- Withdrawal fee: 0% for passive lenders
- APY spread: ~50% between borrower rate and lender rate goes to DAO
- For passive lenders, fees are internalised in the share price

Example vault contracts:

- Hyperithm USDT0 Pool on Plasma: https://plasmascan.to/address/0xb74760fd26400030620027dd29d19d74d514700e
- GHO v3 Pool on Ethereum: https://etherscan.io/address/0x4d56c9cba373ad39df69eb18f076b7348000ae09
"""

import datetime
import logging
from decimal import Decimal
from typing import Iterable

from eth_typing import BlockIdentifier
from web3 import Web3

from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.types import Percent
from eth_defi.vault.base import (
    DEPOSIT_CLOSED_PAUSED,
    REDEMPTION_CLOSED_INSUFFICIENT_LIQUIDITY,
    REDEMPTION_CLOSED_PAUSED,
    VaultHistoricalRead,
    VaultHistoricalReader,
)

logger = logging.getLogger(__name__)

#: Minimal ABI for Gearbox PoolV3 functions not in standard ERC-4626
GEARBOX_POOL_V3_ABI = [
    {"inputs": [], "name": "paused", "outputs": [{"type": "bool"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "availableLiquidity", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "totalBorrowed", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
]

#: Keccak signatures for multicall
AVAILABLE_LIQUIDITY_SIGNATURE = Web3.keccak(text="availableLiquidity()")[0:4]
TOTAL_BORROWED_SIGNATURE = Web3.keccak(text="totalBorrowed()")[0:4]


class GearboxVaultHistoricalReader(ERC4626HistoricalReader):
    """Read Gearbox vault core data + utilisation metrics."""

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        yield from self.construct_core_erc_4626_multicall()
        yield from self.construct_utilisation_calls()

    def construct_utilisation_calls(self) -> Iterable[EncodedCall]:
        """Add Gearbox-specific utilisation calls."""
        available_liquidity_call = EncodedCall.from_keccak_signature(
            address=self.vault.address,
            signature=AVAILABLE_LIQUIDITY_SIGNATURE,
            function="availableLiquidity",
            data=b"",
            extra_data={
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield available_liquidity_call

        total_borrowed_call = EncodedCall.from_keccak_signature(
            address=self.vault.address,
            signature=TOTAL_BORROWED_SIGNATURE,
            function="totalBorrowed",
            data=b"",
            extra_data={
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield total_borrowed_call

    def process_utilisation_result(self, call_by_name: dict[str, EncodedCallResult]) -> tuple[Decimal | None, Percent | None]:
        """Decode Gearbox utilisation data.

        Utilisation = totalBorrowed / (totalBorrowed + availableLiquidity)
        """
        available_liquidity_result = call_by_name.get("availableLiquidity")
        total_borrowed_result = call_by_name.get("totalBorrowed")

        if available_liquidity_result is None or total_borrowed_result is None:
            return None, None

        denomination_token = self.vault.denomination_token
        if denomination_token is None:
            return None, None

        available_liquidity_raw = int.from_bytes(available_liquidity_result.result[0:32], byteorder="big")
        total_borrowed_raw = int.from_bytes(total_borrowed_result.result[0:32], byteorder="big")

        available_liquidity = denomination_token.convert_to_decimals(available_liquidity_raw)

        total_pool = available_liquidity_raw + total_borrowed_raw
        if total_pool == 0:
            utilisation = 0.0
        else:
            utilisation = total_borrowed_raw / total_pool

        return available_liquidity, utilisation

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        call_by_name = self.dictify_multicall_results(block_number, call_results)

        # Decode common variables
        share_price, total_supply, total_assets, errors, max_deposit = self.process_core_erc_4626_result(call_by_name)
        available_liquidity, utilisation = self.process_utilisation_result(call_by_name)

        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            performance_fee=0.0,
            management_fee=0.0,
            errors=errors,
            max_deposit=max_deposit,
            available_liquidity=available_liquidity,
            utilisation=utilisation,
        )


class GearboxVault(ERC4626Vault):
    """Gearbox Protocol PoolV3 vault.

    Gearbox pools allow passive liquidity providers to deposit assets and earn
    yield from borrowers (credit accounts) who pay interest on borrowed funds.

    Key features:

    - ERC-4626 compatible lending pool
    - Yield from institutional-grade leveraged positions
    - Zero withdrawal fees for passive lenders
    - Credit manager integration for leveraged borrowing

    - Homepage: https://gearbox.finance/
    - App: https://app.gearbox.fi/
    - Documentation: https://docs.gearbox.finance/
    - GitHub: https://github.com/Gearbox-protocol/core-v3
    - Twitter: https://x.com/GearboxProtocol
    """

    def has_custom_fees(self) -> bool:
        """Gearbox pools have no custom deposit/withdrawal fees for passive lenders."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """No management fee for passive lenders."""
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """No performance fee for passive lenders (fees internalised in share price)."""
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Gearbox pools have no lock-up for passive lenders."""
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get link to the Gearbox app."""
        return "https://app.gearbox.fi/"

    def _get_gearbox_contract(self):
        """Get contract instance with Gearbox-specific ABI."""
        return self.web3.eth.contract(address=self.address, abi=GEARBOX_POOL_V3_ABI)

    def fetch_deposit_closed_reason(self) -> str | None:
        """Check if deposits are closed.

        Gearbox pools can be paused by governance.
        Deposits are generally always open unless paused.

        Note: We don't use maxDeposit(address(0)) because Gearbox's implementation
        checks owner balance, making it unsuitable as a global availability check.
        """
        try:
            gearbox_contract = self._get_gearbox_contract()
            paused = gearbox_contract.functions.paused().call()
            if paused:
                return f"{DEPOSIT_CLOSED_PAUSED} (paused=true)"
        except Exception:
            pass
        return None

    def fetch_redemption_closed_reason(self) -> str | None:
        """Check if redemptions are closed due to paused state or no liquidity.

        Gearbox pools may have limited withdrawal liquidity when utilisation is high.
        All deposited funds could be lent to credit accounts, leaving no liquidity
        for immediate redemptions.

        Note: We don't use maxRedeem(address(0)) because Gearbox's implementation
        is: `Math.min(balanceOf(owner), convertToShares(availableLiquidity()))`.
        Since balanceOf(address(0)) is always 0, maxRedeem(address(0)) always returns 0
        regardless of actual available liquidity.
        """
        try:
            gearbox_contract = self._get_gearbox_contract()

            # Check if paused first
            paused = gearbox_contract.functions.paused().call()
            if paused:
                return f"{REDEMPTION_CLOSED_PAUSED} (paused=true)"

            # Check available liquidity
            available_liquidity = gearbox_contract.functions.availableLiquidity().call()
            if available_liquidity == 0:
                return f"{REDEMPTION_CLOSED_INSUFFICIENT_LIQUIDITY} (availableLiquidity=0)"

        except Exception:
            pass
        return None

    def can_check_redeem(self) -> bool:
        """Gearbox does NOT support address(0) checks for redemption availability.

        maxRedeem(address(0)) always returns 0 because the implementation uses
        min(balanceOf(owner), convertToShares(availableLiquidity())) and
        balanceOf(address(0)) is always 0.
        """
        return False

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Get Gearbox-specific historical reader with utilisation metrics."""
        return GearboxVaultHistoricalReader(self, stateful)

    def fetch_available_liquidity(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Get the amount of denomination token available for immediate withdrawal.

        Uses Gearbox's `availableLiquidity()` function which returns the amount
        of underlying tokens not currently lent to credit accounts.

        :param block_identifier:
            Block to query. Defaults to "latest".

        :return:
            Amount in denomination token units (human-readable Decimal).
        """
        try:
            gearbox_contract = self._get_gearbox_contract()
            available_raw = gearbox_contract.functions.availableLiquidity().call(block_identifier=block_identifier)
            denomination_token = self.denomination_token
            if denomination_token is None:
                return None
            return denomination_token.convert_to_decimals(available_raw)
        except Exception:
            return None

    def fetch_utilisation_percent(self, block_identifier: BlockIdentifier = "latest") -> Percent | None:
        """Get the percentage of assets currently lent out.

        Utilisation = totalBorrowed / (totalBorrowed + availableLiquidity)

        :param block_identifier:
            Block to query. Defaults to "latest".

        :return:
            Utilisation as float between 0.0 and 1.0 (0% to 100%).
        """
        try:
            gearbox_contract = self._get_gearbox_contract()
            available_liquidity = gearbox_contract.functions.availableLiquidity().call(block_identifier=block_identifier)
            total_borrowed = gearbox_contract.functions.totalBorrowed().call(block_identifier=block_identifier)

            total_pool = available_liquidity + total_borrowed
            if total_pool == 0:
                return 0.0
            return total_borrowed / total_pool
        except Exception:
            return None
