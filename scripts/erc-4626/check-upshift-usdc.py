"""Read current admission and token compatibility for the reviewed Upshift vaults.

Run after loading the normal private environment::

    source .local-test.env && poetry run python scripts/erc-4626/check-upshift-usdc.py

Uses configured chain RPC connections and sends no transactions. The default
amount is 10 USDC. Optional UPSHIFT_OWNER checks that custody account; without
it, account-specific admission remains unverified. Optional AMOUNT,
MAX_WORKERS (default 4), and REPORT_JSON override sizing, concurrency and the
JSON report path. Read-only eligibility is not a successful executor round trip.
``observed_head_block`` records the head seen before the diagnostic; individual
views read current state and are not a snapshot pinned to that block.
When policy reads succeed, permissioned deployments are ``permissioned_excluded``
from this USDC shortlist, even when UPSHIFT_OWNER is admitted. Failed reads stay
``read_failed``. The audit retains excluded entries; it requests no admission.
"""

import json
import logging
import os
from decimal import Decimal
from pathlib import Path

from eth_abi.exceptions import DecodingError
from eth_typing import HexAddress
from joblib import Parallel, delayed
from requests.exceptions import RequestException
from tabulate import tabulate
from tqdm_loggable.auto import tqdm
from web3 import Web3
from web3.exceptions import Web3Exception

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.upshift.vault import UpshiftVault
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable
from eth_defi.vault_protocols.repairs.upshift import TARGETS

logger = logging.getLogger(__name__)


def fetch_vault_result(spec: VaultSpec, amount: Decimal, owner: HexAddress | None) -> dict:
    """Read one deployment without implying execution or unrestricted admission.

    :param spec: Reviewed chain and vault identity.
    :param amount: USDC input amount to check.
    :param owner: Optional actual custody address.
    :return: JSON-safe observations with a precise support or availability blocker.
    """
    result = {"chain_id": spec.chain_id, "vault_address": spec.vault_address, "status": "read_failed"}
    try:
        web3 = create_multi_provider_web3(read_json_rpc_url(spec.chain_id))
        block = web3.eth.block_number
        result["observed_head_block"] = block
        vault = create_vault_instance_autodetect(web3, spec.vault_address)
        assert isinstance(vault, UpshiftVault), "Detected adapter is not Upshift"
        manager = vault.get_deposit_manager()
        result.update(name=vault.name, payout_asset=vault.denomination_token.symbol, payout_address=vault.denomination_token.address, share_token=vault.share_token.address)
        accepted = vault.fetch_all_denomination_tokens()
        usdc = USDC_NATIVE_TOKEN[spec.chain_id]
        result["accepted_assets"] = [{"symbol": token.symbol, "address": token.address} for token in accepted]
        result["configured_lag_seconds"] = int(vault.upshift_contract.functions.lagDuration().call())
        result["requires_whitelist"] = vault.is_whitelisted_deposit()
        result["owner_admitted"] = vault.is_account_whitelisted(owner) if owner else None
        result["payout_token_balance"] = str(vault.denomination_token.fetch_balance_of(vault.address))
        result["deposit_closed_reason"] = vault.fetch_deposit_closed_reason()
        result["withdrawal_closed_reason"] = vault.fetch_redemption_closed_reason()
        if result["requires_whitelist"]:
            result["status"] = "permissioned_excluded"
        elif usdc.lower() not in {token.address_lower for token in accepted}:
            result["status"] = "usdc_not_accepted"
        elif result["owner_admitted"] is False:
            result["status"] = "custody_account_blocked"
        elif vault.denomination_token.address_lower != usdc.lower():
            result["status"] = "payout_conversion_route_required"
        elif result["deposit_closed_reason"] or result["withdrawal_closed_reason"]:
            result["status"] = "temporarily_unavailable"
        else:
            if owner:
                kwargs = {"accepted_asset": usdc} if vault.multi_asset_like else {}
                manager.create_deposit_request(owner, amount=amount, check_enough_token=False, **kwargs)
            else:
                result["deposit_capacity_raw"] = manager.fetch_max_deposit_for_asset(usdc) if vault.multi_asset_like else None
            result["status"] = "eligible_not_traded" if owner else "account_admission_unverified"
    except (Web3Exception, RequestException, DecodingError, ValueError, KeyError, AssertionError, VaultFlowUnavailable) as error:
        result["error_type"] = type(error).__name__
        logger.warning("Upshift read/preflight failed for %s: %s", spec.as_string_id(), type(error).__name__)
    return result


def main(max_workers: int = 4) -> None:
    """Check the fixed catalogue inventory and optionally export dated evidence.

    RPC credentials come from the normal environment. The report records no
    provider URLs, keys or transaction journals.

    :param max_workers: Default concurrency, optionally overridden by MAX_WORKERS.
    :return: ``None`` after tabular diagnostics and an optional JSON export.
    """
    logging.basicConfig(level=logging.INFO)
    amount = Decimal(os.environ.get("AMOUNT", "10"))
    workers = int(os.environ.get("MAX_WORKERS", str(max_workers)))
    assert amount > 0 and workers > 0
    owner = os.environ.get("UPSHIFT_OWNER")
    if owner:
        owner = Web3.to_checksum_address(owner)
    results = list(tqdm(Parallel(n_jobs=workers, backend="threading", return_as="generator")(delayed(fetch_vault_result)(spec, amount, owner) for spec in TARGETS), total=len(TARGETS), desc="Checking Upshift vaults"))
    print(tabulate([{key: result.get(key) for key in ("chain_id", "name", "payout_asset", "status", "error_type")} for result in results], headers="keys"))
    report = os.environ.get("REPORT_JSON")
    if report:
        Path(report).write_text(json.dumps({"checked_at": native_datetime_utc_now().isoformat(), "amount_usdc": str(amount), "owner": owner, "results": results}, indent=2) + "\n")


if __name__ == "__main__":
    main()
