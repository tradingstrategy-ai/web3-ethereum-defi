"""Manual funded Lighter pool mint/burn/secure-withdrawal/L1-claim test.

This performs real mainnet actions using an EXISTING small funded Safe-owned
Lighter account. It never deploys contracts or settles Lagoon. Start with no
pool holdings, pending secure withdrawals, orders or positions, and enough
already-accounted available collateral for a 10-USDC allocation by default.
Use a public pool with sufficient liquidity and an affordable raw share price.
LLP entry also requires existing LIT staking capacity; this script never stakes.

Required private environment inputs:

- ``JSON_RPC_ETHEREUM``: Ethereum RPC, supporting the usual fallback format.
- ``LIGHTER_TEST_PRIVATE_KEY`` or the earlier deployment's
  ``LAGOON_MULTCHAIN_TEST_PRIVATE_KEY``: Ethereum asset manager for the guarded L1 claim.
- ``LIGHTER_DEPLOYMENT_REPORT``: The protected JSONL report from
  ``deploy-lagoon-lighter-multichain.py`` containing the backed-up API key and
  satellite addresses. This identity cannot be safely chosen from arbitrary
  previous deployments.
- ``LIGHTER_POOL_INDEX``: Deliberately selected public pool to test; an
  investment target cannot safely be inferred.

Optional ``LIGHTER_POOL_TEST_USDC`` defaults to ``10``. Optional
``LIGHTER_POOL_TEST_REPORT`` defaults to a fresh protected JSONL file. Routine
timeouts and polling use the library defaults. The private key stays in memory;
short-lived auth tokens and signed payloads are never printed.

Install the optional SDK using the pinned installation commands in
``scripts/lagoon/lagoon-lighter-trade-example.py``. The signing methods used here
are synchronous. Only SDK client construction/cleanup requires an event loop;
execution and polling use the synchronous library APIs.

Run after loading the private environment::

    poetry run python scripts/lagoon/lagoon-lighter-pool-roundtrip.py

Records include dated equity, raw holdings, estimated values, transaction
hashes and actual withdrawal/claim amounts. This exercises the complete API
and Ethereum route. It does not enable production aggregate NAV automatically:
review whether equity includes pool shares using the saved before/after data.
On interruption, inspect the saved operation and reconcile its original hash
with ``LighterOperation.from_dict()`` and ``LighterPoolExecutor.reconcile()``;
never blindly re-run this round trip to retry an uncertain mutation.
"""

import asyncio
import json
import logging
import os
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.vault_protocol.lagoon.lighter import wait_for_safe_usdc
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonSatelliteVault
from eth_defi.hotwallet import HotWallet
from eth_defi.lighter.activation import create_activation_report, default_activation_report_path, record_activation
from eth_defi.lighter.api import fetch_lighter_withdrawal_delay
from eth_defi.lighter.execution import (
    LIGHTER_SIGNING_CHAIN_ID,
    LIGHTER_USDC_ASSET_ID,
    LighterAccountIdentity,
    LighterOperation,
    LighterPoolExecutor,
    fetch_execution_account,
    fetch_pool_holdings,
    fetch_pool_state,
    fetch_withdrawals,
    prepare_pool_deposit,
    prepare_pool_redemption,
    prepare_secure_withdrawal,
    wait_for_available_collateral,
    wait_for_claimable_withdrawal,
    wait_for_pool_shares,
)
from eth_defi.lighter.lagoon import claim_usdc_to_lagoon_safe_from_lighter
from eth_defi.lighter.sdk import import_lighter_sdk
from eth_defi.lighter.session import create_lighter_session
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN, fetch_erc20_details
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def load_setup(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load an explicitly selected completed private multichain deployment.

    :param path: Protected JSONL deployment/key backup.
    :return: Ethereum deployment addresses and backed-up account/key identity.
    """
    assert path.stat().st_mode & 0o077 == 0, "Deployment report must be private"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[-1]["stage"] == "complete", "Deployment is incomplete; recover it before trading"
    ethereum = [r["deployment"] for r in rows if r["stage"] == "chain_setup_complete" and r["deployment"]["chain_id"] == 1]
    keys = [r for r in rows if r["stage"] == "lighter_key_saved"]
    assert len(ethereum) == len(keys) == 1
    return ethereum[0], keys[0]


async def run_roundtrip() -> None:  # noqa: PLR0914
    """Run the manual lifecycle using the official optional SDK signer.

    The enclosing loop exists only because SDK client construction and close
    require it. No asynchronous network execution helpers are used here.
    """
    web3 = create_multi_provider_web3(os.environ["JSON_RPC_ETHEREUM"], default_http_timeout=(3, 180))
    assert web3.eth.chain_id == 1
    wallet = HotWallet.from_private_key(os.environ.get("LIGHTER_TEST_PRIVATE_KEY") or os.environ["LAGOON_MULTCHAIN_TEST_PRIVATE_KEY"])
    wallet.sync_nonce(web3)
    deployment, key = load_setup(Path(os.environ["LIGHTER_DEPLOYMENT_REPORT"]).expanduser())
    pool_index = int(os.environ["LIGHTER_POOL_INDEX"])
    amount = Decimal(os.environ.get("LIGHTER_POOL_TEST_USDC", "10"))
    assert amount.is_finite() and amount >= 1
    report = Path(os.environ["LIGHTER_POOL_TEST_REPORT"]).expanduser() if os.environ.get("LIGHTER_POOL_TEST_REPORT") else default_activation_report_path()
    create_activation_report(report)
    safe = LagoonSatelliteVault(web3, safe_address=deployment["safe_address"], trading_strategy_module_address=deployment["trading_strategy_module_address"])
    usdc = fetch_erc20_details(web3, USDC_NATIVE_TOKEN[1])
    sdk = import_lighter_sdk()
    signer = sdk.SignerClient(url="https://mainnet.zklighter.elliot.ai", account_index=key["account_index"], api_private_keys={key["api_key_index"]: key["private_key"]}, nonce_management_type=sdk.nonce_manager.NonceManagerType.NONE)
    assert signer.chain_id == LIGHTER_SIGNING_CHAIN_ID, "Unexpected SDK signing network"
    logger.info("Funded pool lifecycle report: %s", report)

    def persist(op: LighterOperation) -> None:
        """Save restart evidence without logging signatures or private keys."""
        record_activation(report, "operation", operation=op.as_dict())

    def auth() -> str:
        """Create an official short-lived token for replay-safe reads."""
        token, error = signer.create_auth_token_with_expiry(api_key_index=key["api_key_index"])
        assert error is None and token, "Lighter auth-token creation failed"
        return token

    with create_lighter_session() as session:
        identity = LighterAccountIdentity(key["account_index"], key["api_key_index"])
        executor = LighterPoolExecutor(session, identity, signer, persist)

        def observe(stage: str) -> None:
            """Keep equity and investor pool values separate for NAV analysis."""
            account, equity = fetch_execution_account(session, identity.account_index)
            holdings = tuple(fetch_pool_holdings(session, identity.account_index))
            record_activation(report, stage, equity_usdc=str(equity.total_asset_value), available_usdc=str(equity.available_balance), collateral_usdc=str(equity.collateral), shares=account["shares"], pool_gross_usdc=str(sum((row.gross_value for row in holdings), Decimal(0))), estimated_operator_fees_usdc=str(sum((row.estimated_fee for row in holdings), Decimal(0))))

        try:
            account, equity = fetch_execution_account(session, identity.account_index)
            assert account["l1_address"].lower() == deployment["safe_address"].lower()
            assert not account["shares"] and not account["positions"] and int(account["pending_order_count"]) == 0, "Use an idle account without existing exposure"
            assert equity.available_balance >= amount
            before_history = tuple(fetch_withdrawals(session, identity.account_index, auth()))
            assert not any(row["status"] in {"pending", "claimable"} for row in before_history), "Recover existing secure withdrawals first"
            baseline_ids = {str(row["id"]) for row in before_history}
            observe("before_mint")
            mint = prepare_pool_deposit(executor, pool_index, amount, auth_token=auth())
            executor.submit(mint)
            executor.reconcile(mint)
            wait_for_pool_shares(session, identity.account_index, pool_index, mint.amount)
            observe("after_mint")
            pool = fetch_pool_state(session, pool_index)
            (holding,) = fetch_pool_holdings(session, identity.account_index)
            if pool.is_llp:
                ready = holding.entered_at + pool.cooldown
                while native_datetime_utc_now() < ready:
                    logger.info("Waiting for LLP cooldown until %s UTC", ready)
                    time.sleep(max(0, min(30, (ready - native_datetime_utc_now()).total_seconds())))
            _, before_burn_equity = fetch_execution_account(session, identity.account_index)
            burn = prepare_pool_redemption(executor, pool_index)
            assert burn.amount == mint.amount
            executor.submit(burn)
            executor.reconcile(burn)
            wait_for_pool_shares(session, identity.account_index, pool_index, 0)
            available = wait_for_available_collateral(session, identity.account_index, before_burn_equity.available_balance + usdc.convert_to_decimals(1))
            observe("after_burn")
            # Withdraw the actual available monetary amount, rounded by native token metadata.
            withdrawal_amount = usdc.convert_to_decimals(usdc.convert_to_raw(available))
            delay = fetch_lighter_withdrawal_delay(session)
            record_activation(report, "withdrawal_started", baseline_ids=sorted(baseline_ids), requested_usdc=str(withdrawal_amount), delay_seconds=delay)
            withdrawal = prepare_secure_withdrawal(executor, usdc, withdrawal_amount)
            executor.submit(withdrawal)
            executor.reconcile(withdrawal)
            deadline = time.monotonic() + 300
            while True:
                # This test is the sole writer and refuses any pre-existing pending withdrawal.
                new = [row for row in fetch_withdrawals(session, identity.account_index, auth()) if str(row["id"]) not in baseline_ids]
                assert len(new) <= 1, "Ambiguous withdrawal evidence; inspect the saved transaction manually"
                if new:
                    row = new[0]
                    assert row["type"] == "secure" and int(row["asset_id"]) == LIGHTER_USDC_ASSET_ID
                    assert Decimal(0) < Decimal(row["amount"]) <= withdrawal_amount
                    withdrawal_id = str(row["id"])
                    record_activation(report, "withdrawal_identified", tx_hash=withdrawal.tx_hash, withdrawal_id=withdrawal_id)
                    break
                if time.monotonic() >= deadline:
                    message = "Confirmed withdrawal is not indexed; recover the saved operation before retrying"
                    raise TimeoutError(message)
                logger.info("Waiting for the confirmed secure withdrawal history record")
                time.sleep(5)
            row = wait_for_claimable_withdrawal(session, identity.account_index, withdrawal_id, auth, timeout=max(3600, delay + 1800))
            claimable = Decimal(row["amount"])
            before = usdc.fetch_balance_of(safe.safe_address)
            record_activation(report, "claim_started", withdrawal_id=withdrawal_id, claimable_usdc=str(claimable), nonce=wallet.current_nonce)
            claim_hash = claim_usdc_to_lagoon_safe_from_lighter(web3, wallet, vault=safe, usdc=usdc, claimable_usdc=claimable, tx_observer=lambda tx_hash, nonce, description: record_activation(report, "claim_signed", tx_hash=tx_hash, nonce=nonce, description=description))
            after = wait_for_safe_usdc(usdc, safe.safe_address, before + claimable)
            assert usdc.convert_to_raw(after - before) == usdc.convert_to_raw(claimable)
            observe("after_claim")
            record_activation(report, "complete", claim_tx_hash=claim_hash, claimed_usdc=str(claimable))
            logger.info("Lifecycle complete: minted/burned %d raw shares, claimed %s USDC to Safe", mint.amount, claimable)
        finally:
            await signer.close()


def main() -> None:
    """Run the explicitly configured funded mainnet lifecycle check."""
    setup_console_logging(os.environ.get("LOG_LEVEL", "info"))
    asyncio.run(run_roundtrip())


if __name__ == "__main__":
    main()
