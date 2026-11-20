# ruff: noqa: N999 - Numbered filenames are required by the migration runner.
"""Refresh the trading account and complete fee schedule for Pear.

Purpose: Refresh persisted metadata used by the individual vault JSON export.
Scope: The four reviewed Pear deployments on HyperEVM.
Branch: examine-pear-garden-vault-protocol
PR title: feat: add Pear vault support on HyperEVM
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1799
Preserves: Leads, NAV observations, reader state and unrelated catalogue fields.
Expected: Refresh fee and trading account metadata, or report missing rows.

LLM-generated operational migration; changes require human review.
Run through the numbered migration runner with the scanner stopped.
"""

import logging

from eth_defi.erc_4626.vault_protocol.pear.constants import PEAR_CHAIN_ID
from eth_defi.erc_4626.vault_protocol.pear.vault import PearVault
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec
from eth_defi.vault_protocols.migration import MigrationContext

logger = logging.getLogger(__name__)

#: Freeze the deployment inventory reviewed for this refresh.
TARGETS = (
    VaultSpec(PEAR_CHAIN_ID, "0x65c9f4665c7d9ce96f53aafc67796dcad46f4096"),
    VaultSpec(PEAR_CHAIN_ID, "0xd38a67a4f54a9b4286d72f08440485a395ddfa70"),
    VaultSpec(PEAR_CHAIN_ID, "0xdf6917ee9a2d9b34075bb099a0a1e955bcfcdf84"),
    VaultSpec(PEAR_CHAIN_ID, "0x69f2ed780a4cec49e552dbcd35a60f41368bc362"),
)


def migrate(context: MigrationContext) -> None:
    """Refresh the custom Pear fields using the same accessors as the scanner.

    Only existing rows are updated. Read and validate all targets before
    publishing so a failed read leaves the previous catalogue intact.

    :param context: Guarded context owning the staged vault database.
    :return: None after preview or commit.
    """
    database = context.metadata()
    staged = {}
    missing = []
    web3 = None
    for spec in TARGETS:
        row = database.rows.get(spec)
        if row is None:
            missing.append(spec.as_string_id())
            continue
        detection = row.get("_detection_data")
        if row.get("Protocol") != "Pear" or detection is None or detection.get_spec() != spec:
            raise ValueError(f"Conflicting Pear vault identity for {spec}")
        if web3 is None:
            web3 = create_multi_provider_web3(read_json_rpc_url(PEAR_CHAIN_ID))
            assert web3.eth.chain_id == PEAR_CHAIN_ID

        logger.info("Refreshing Pear trading account and fees for %s", spec)
        vault = PearVault(web3, spec, default_block_identifier="latest")
        fees = vault.get_fee_data()
        staged[spec] = (
            row
            | vault.fetch_scan_record_extra_data()
            | {
                "_fees": fees,
                "Mgmt fee": fees.management,
                "Perf fee": fees.performance,
                "Deposit fee": fees.deposit,
                "Withdraw fee": fees.withdraw,
                "Protocol fee": fees.protocol,
            }
        )

    database.rows.update(staged)
    context.report(refreshed_rows=len(staged), missing_specs=missing)
    context.commit_metadata()
