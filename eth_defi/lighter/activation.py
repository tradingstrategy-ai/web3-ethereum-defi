"""Activate an already-funded Ethereum Safe's Lighter account.

The shared ceremony deposits through the guard, backs up an API key, registers
it with a direct Safe-owner transaction, then verifies the exact public key.
See https://apidocs.lighter.xyz/docs/api-keys. Funding and ownership handover
are the caller's responsibility.
"""

import json
import logging
import os
import secrets
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.vault_protocol.lagoon.vault import AutomatedSafe
from eth_defi.hotwallet import HotWallet
from eth_defi.lighter.api import LIGHTER_MIN_MAINNET_USDC, fetch_lighter_api_key, wait_for_lighter_account, wait_for_lighter_api_key, wait_for_lighter_collateral
from eth_defi.lighter.api_key import generate_lighter_api_key
from eth_defi.lighter.constants import LIGHTER_L1_CONTRACT, LIGHTER_USDC_ETHEREUM
from eth_defi.lighter.lagoon import broadcast_tx, deposit_usdc_from_lagoon_safe_into_lighter
from eth_defi.lighter.pubkey import MAX_API_KEY_INDEX, MIN_API_KEY_INDEX, build_change_pubkey_safe_tx
from eth_defi.lighter.session import LighterSession, create_lighter_session
from eth_defi.token import fetch_erc20_details

if TYPE_CHECKING:
    from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LighterAccountSetup

logger = logging.getLogger(__name__)


def default_activation_report_path() -> Path:
    """Choose a fresh protected report location without environment inputs.

    :return: New file path; no filesystem mutations are performed here.
    """
    return Path.home() / ".tradingstrategy/lighter" / f"activation-{native_datetime_utc_now():%Y%m%dT%H%M%S}-{secrets.token_hex(4)}.jsonl"


def record_activation(report: Path, stage: str, **details: Any) -> None:
    """Append and flush recovery evidence to a private JSONL file.

    Create the report exclusively before starting. It may contain a generated
    API private key; neither the details nor that key are written to the log.
    Calls from parallel deployment workers must be serialised by the caller.

    :param report: Existing mode-0600 progress file.
    :param stage: Human-readable operation stage.
    :param details: JSON-compatible addresses, nonces and results.
    """
    with report.open("a", encoding="utf-8") as out:
        out.write(json.dumps({"time": native_datetime_utc_now().isoformat(), "stage": stage, **details}) + "\n")
        out.flush()
        os.fsync(out.fileno())
    logger.info("Lighter deployment stage: %s", stage)


def create_activation_report(report: Path) -> None:
    """Exclusively reserve a private report before sending transactions.

    :param report: Fresh file path. Existing evidence is never overwritten.
    """
    report.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"):
        pass


def activate_lighter_safe(
    web3: Web3,
    wallet: HotWallet,
    safe: AutomatedSafe,
    *,
    api_key_index: int = MIN_API_KEY_INDEX,
    report_path: Path | None = None,
    progress: Callable[..., None] | None = None,
    session: LighterSession | None = None,
) -> "LighterAccountSetup":
    """Deposit one accounted USDC and register a backed-up API key.

    The Safe must still have the deployer as its sole owner and threshold one.
    No pool trades or additional Lagoon settlements are performed. A failed
    transaction or polling timeout stops the ceremony for manual recovery.

    :param web3: Ethereum mainnet connection.
    :param wallet: Sole Safe owner and whitelisted asset manager.
    :param safe: Funded Lagoon vault or Ethereum satellite.
    :param api_key_index: Unoccupied user slot, default four.
    :param report_path: New private report if no deployment progress callback is supplied.
    :param progress: Existing private deployment recorder, including key backup.
    :param session: Optional caller-owned API transport, including local simulation.
    :return: Verified account setup, with the secret excluded from ``repr``.
    """
    # Deployment imports the recorder above; load its result type after initialisation.
    from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LighterAccountSetup  # noqa: PLC0415

    assert web3.eth.chain_id == 1, "Lighter custody activation requires Ethereum"
    assert MIN_API_KEY_INDEX <= api_key_index <= MAX_API_KEY_INDEX
    assert {a.lower() for a in safe.safe.retrieve_owners()} == {wallet.address.lower()}
    assert safe.safe.retrieve_threshold() == 1
    usdc = fetch_erc20_details(web3, LIGHTER_USDC_ETHEREUM)
    assert usdc.fetch_balance_of(safe.safe_address) >= LIGHTER_MIN_MAINNET_USDC
    if progress is None:
        report_path = report_path or default_activation_report_path()
        create_activation_report(report_path)
        logger.info("Protected Lighter activation/key report: %s", report_path)

        def progress(stage: str, **details: Any) -> None:
            """Write one standalone activation record to the private report."""
            record_activation(report_path, stage, **details)

    def observe(tx_hash: str, nonce: int, description: str) -> None:
        """Persist an Ethereum transaction identity before broadcasting it."""
        progress("ethereum_transaction_signed", chain_id=1, tx_hash=tx_hash, nonce=nonce, description=description)

    progress("lighter_deposit_started", safe_address=safe.safe_address, nonce=wallet.current_nonce)
    deposit_hash = deposit_usdc_from_lagoon_safe_into_lighter(web3, wallet, vault=safe, usdc=usdc, deposit_usdc=LIGHTER_MIN_MAINNET_USDC, tx_observer=observe)
    progress("lighter_deposit_confirmed", tx_hash=deposit_hash)
    with nullcontext(session) if session is not None else create_lighter_session() as api_session:
        account_index = wait_for_lighter_account(api_session, safe.safe_address)
        collateral = wait_for_lighter_collateral(api_session, account_index, LIGHTER_MIN_MAINNET_USDC)
        occupied = fetch_lighter_api_key(api_session, account_index, api_key_index)
        if occupied is not None:
            public_key = occupied.get("public_key", occupied.get("publicKey"))
            assert isinstance(public_key, str) and bytes.fromhex(public_key.removeprefix("0x")) == bytes(40), "Refusing to overwrite an occupied or malformed API-key slot"
        key = generate_lighter_api_key(api_key_index)
        progress("lighter_key_saved", account_index=account_index, **asdict(key))
        progress("lighter_key_registration_started", nonce=wallet.current_nonce, safe_nonce=safe.safe.retrieve_nonce())
        safe_tx = build_change_pubkey_safe_tx(web3, safe.safe, account_index, key.api_key_index, bytes.fromhex(key.public_key.removeprefix("0x")), LIGHTER_L1_CONTRACT)
        safe_tx.sign(wallet.private_key.hex())
        change_hash = broadcast_tx(web3, wallet, safe_tx.w3_tx, "Register Lighter API key through Safe owner", gas_limit=2_000_000, tx_observer=observe)
        progress("lighter_key_registration_confirmed", tx_hash=change_hash)
        wait_for_lighter_api_key(api_session, account_index, api_key_index, key.public_key, timeout=900)
    return LighterAccountSetup(account_index, api_key_index, key.private_key, key.public_key, LIGHTER_MIN_MAINNET_USDC, deposit_hash, change_hash, collateral)
