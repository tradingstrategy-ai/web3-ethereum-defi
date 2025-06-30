"""Analyse Lagoon protocol deposits and redemptions.

- To track our treasury balance

- Find Lagoon events here https://github.com/hopperlabsxyz/lagoon-v0/blob/b790b1c1fbb51a101b0c78a4bb20e8700abed054/src/vault/primitives/Events.sol
"""

import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal

from hexbytes import HexBytes
from web3._utils.events import EventLogErrorFlags

from eth_defi.lagoon.vault import LagoonVault
from eth_defi.timestamp import get_block_timestamp
from eth_defi.token import TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class LagoonSettlementEvent:
    """Capture Lagoon vault flow when it is settled.

    - Use to adjust vault treasury balances for internal accounting
    - Shows the Lagoon vault status after the settlement at a certain block height
    - We do not capture individual users

    The cycle is
    - Value vault
    - Settle deposits (USD in) and redeemds (USDC out, shares in)
    - Because valuation is done before the settle, you need to be careful what the values reflect here
    - We pull some values from receipt, some values at the end of the block
    """

    #: Chain we checked
    chain_id: int

    #: settleDeposit() transaction by the asset managre
    tx_hash: HexBytes

    #: When the settlement was done
    block_number: int

    #: When the settlement was done
    timestamp: datetime.datetime

    #: Vault address
    vault: LagoonVault

    #: Number of deposit event processed (0..1)
    deposit_events: int

    #: Number of deposit event processed (0..1)
    redeem_events: int

    #: How much new underlying was added to the vault
    deposited: Decimal

    #: How much was redeemed successfully
    redeemed: Decimal

    #: Shares added for new investor
    shares_minted: Decimal

    #: Shares burned for redemptions
    shares_burned: Decimal

    #: Vault latest settled valuation.
    #:
    #: This does not include the newly settled deposits,
    #: as they were not part of the previous share valuation cycle.
    #:
    total_assets: Decimal

    #: Outstanding shares.
    #:
    #: Vault latest issued share count
    total_supply: Decimal

    #: Share price in the underlying token, after the settlement
    share_price: Decimal

    #: Amount of redemptions we could not settle (USD),
    #: because the lack of cash in the previous cycle.
    #:
    #: This much of cash needs to be made available for the next settlement cycle.
    #:
    pending_redemptions_underlying: Decimal

    #: Amount of redemptions we could not settle (share count),
    #: because the lack of cash in the previous cycle.
    #:
    pending_redemptions_shares: Decimal

    #: Balance of the underlying token (treasuty/reserve) at the end of the block
    underlying_balance: Decimal

    @property
    def underlying(self) -> TokenDetails:
        """Get USDC."""
        return self.vault.underlying_token

    @property
    def share_token(self) -> TokenDetails:
        """Get USDC."""
        return self.vault.share_token

    def get_serialiable_diagnostics_data(self) -> dict:
        """JSON serialisable diagnostics data for logging"""
        return {
            "chain_id": self.chain_id,
            "block_number": self.block_number,
            "timestamp": self.timestamp,
            "tx_hash": self.tx_hash.hex(),
            "deposit_events": self.deposit_events,
            "redeem_events": self.redeem_events,
            "vault": self.vault.vault_address,
            "underlying": self.underlying.address,
            "share_token": self.share_token.address,
            "deposited": self.deposited,
            "redeemed": self.redeemed,
            "shares_minted": self.shares_minted,
            "shares_burned": self.shares_burned,
            "total_assets": self.total_assets,
            "total_supply": self.total_supply,
            "share_price": self.share_price,
            "pending_redemptions_underlying": self.pending_redemptions_underlying,
            "pending_redemptions_shares": self.pending_redemptions_shares,
        }

    def get_underlying_diff(self) -> Decimal:
        """How much the underlying asset changed in the vault treasury"""
        return self.deposited - self.redeemed

    def get_underlying_balance(self) -> Decimal:
        """How much of treasury we are holding after this update"""
        return self.underlying_balance


def analyse_vault_flow_in_settlement(
    vault: LagoonVault,
    tx_hash: HexBytes,
) -> LagoonSettlementEvent:
    """Extract deposit and redeem events from a settlement transaction.

    - Analyse vault asset flow based on the settlement tx logs in the receipt
    - May need to call vault contract if no deposist or redeem events were prevent.
      This needs an archive node for historical lookback.

    - `Gnosis Safe error list <https://github.com/safe-global/safe-smart-account/blob/main/docs/error_codes.md>`__.

    :raise AssertionError:
        If the Lagoon settlement transaction reverted.
    """
    web3 = vault.web3
    receipt = web3.eth.get_transaction_receipt(tx_hash)
    assert receipt is not None, f"Cannot find tx: {tx_hash}"
    assert isinstance(tx_hash, HexBytes), f"Got {tx_hash}"

    if receipt["status"] != 1:
        # Do a verbose traceback / revert reason if the transaction failed
        # GS104: Method can only be called from an enabled module
        # fmt: off
        logger.error(
            f"Lagoon vault settlement transaction did not succeed: {tx_hash.hex()}\n"
            f"Vault: {vault}\n"
            f"Guard: {vault.trading_strategy_module_address}\n"
            f"Safe: {vault.safe_address}\n"
            f"Guard enabled: {vault.is_trading_strategy_module_enabled()}\n"
            f"Receipt: {receipt}\n"
        )
        # fmt: on
        assert_transaction_success_with_explanation(web3, tx_hash)

    deposits = vault.vault_contract.events.SettleDeposit().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
    redeems = vault.vault_contract.events.SettleRedeem().process_receipt(receipt, errors=EventLogErrorFlags.Discard)

    total_asset_updates = vault.vault_contract.events.TotalAssetsUpdated().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
    assert len(total_asset_updates) == 1, f"Does not look like Lagoon settlement tx, lacking event TotalAssetsUpdated: {tx_hash.hex()}"
    assert len(deposits) in (0, 1), "Only zer or one events per settlement TX"
    assert len(redeems) in (0, 1), "Only zer or one events per settlement TX"

    new_deposited_raw = sum(log["args"]["assetsDeposited"] for log in deposits)
    new_minted_raw = sum(log["args"]["sharesMinted"] for log in deposits)

    new_redeem_raw = sum(log["args"]["assetsWithdrawed"] for log in redeems)
    new_burned_raw = sum(log["args"]["sharesBurned"] for log in redeems)

    block_number = receipt["blockNumber"]
    timestamp = get_block_timestamp(web3, block_number)

    # The amount of shares that could not be redeemed due to lack of cash,
    # at the end of the block
    pending_shares = vault.get_flow_manager().fetch_pending_redemption(block_number)

    # Always pull these numbers at the end of the block
    total_supply = vault.fetch_total_supply(block_number)
    total_assets = vault.fetch_total_assets(block_number)

    if total_assets:
        share_price = total_supply / total_assets
    else:
        share_price = Decimal(0)

    underlying_balance = vault.underlying_token.fetch_balance_of(vault.safe_address, block_number)

    return LagoonSettlementEvent(
        chain_id=vault.chain_id,
        tx_hash=tx_hash,
        block_number=block_number,
        timestamp=timestamp,
        deposit_events=len(deposits),
        redeem_events=len(redeems),
        vault=vault,
        deposited=vault.underlying_token.convert_to_decimals(new_deposited_raw),
        redeemed=vault.underlying_token.convert_to_decimals(new_redeem_raw),
        shares_minted=vault.share_token.convert_to_decimals(new_minted_raw),
        shares_burned=vault.share_token.convert_to_decimals(new_burned_raw),
        total_assets=total_assets,
        total_supply=total_supply,
        pending_redemptions_shares=pending_shares,
        pending_redemptions_underlying=pending_shares * share_price,
        share_price=share_price,
        underlying_balance=underlying_balance,
    )
