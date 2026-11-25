# ruff: noqa: N999 - Numbered filenames are required by the migration runner.
"""Refresh HYPED's native HYPE denomination and current catalogue NAV.

Purpose: Correct the zero decimals previously read from the native asset marker.
Scope: Denomination, _denomination_token and NAV of the Hyperdrive HYPED row on chain 999.
Branch: examine-pear-garden-vault-protocol
PR title: feat: add Pear vault support on HyperEVM
PR: See the pull request for the branch above.
Preserves: Other catalogue fields, leads, historical prices and reader state.
Expected: Refresh one existing HYPED row, or report it missing. Flag older price rows for unit review.

Run after merge through the numbered migration runner in the quiesced scanner
container. Preview before applying. Historical HyperCore NAV cannot be replayed.
LLM-generated operational migration; changes require human review.
"""

import pyarrow.parquet as pq

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import NATIVE_ASSET_ADDRESS
from eth_defi.vault.base import VaultSpec
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.price_backfill import PRICE_FILE_NAME

TARGET = VaultSpec(999, "0x4d0ff6a0dd9f7316b674fb37993a3ce28bea340e")


def migrate(context: MigrationContext) -> None:
    """Replace the stored native denomination and value the existing catalogue row at latest.

    Read the current NAV using the same conversion as the scanner. Historical
    observations have no independently recorded token scale to migrate here.

    :param context: Runner-owned staged data, audit report and atomic publication.
    :return: None after staging the targeted update or reporting a missing row.
    """
    # Old price rows store floats but omit the token scale used to decode
    # them. Report the rows that predate head sampling for operator review;
    # dividing a previously correct observation by 10**18 would corrupt it.
    price_path = context.data_dir / PRICE_FILE_NAME
    legacy_rows = 0
    if price_path.exists():
        has_marker = "vault_poll_frequency" in pq.read_schema(price_path).names
        prices = pq.read_table(price_path, columns=["vault_poll_frequency"] if has_marker else [], filters=[("chain", "==", TARGET.chain_id), ("address", "==", TARGET.vault_address)])
        legacy_rows = sum(label != "latest" for label in prices.column(0).to_pylist()) if has_marker else len(prices)
    context.report(legacy_hyped_price_rows_for_unit_review=legacy_rows)

    row = context.metadata().rows.get(TARGET)
    if row is None:
        context.report(missing_specs=[TARGET.as_string_id()])
    else:
        web3 = create_multi_provider_web3(read_json_rpc_url(TARGET.chain_id))
        assert web3.eth.chain_id == TARGET.chain_id
        vault = ERC4626Vault(web3, TARGET, default_block_identifier="latest")
        token = vault.denomination_token
        if token is None or (token.address_lower, token.symbol, token.decimals) != (NATIVE_ASSET_ADDRESS, "HYPE", 18):
            raise ValueError(f"Unexpected native HYPE denomination for {TARGET}")
        row.update({"Denomination": token.symbol, "_denomination_token": token.export(), "NAV": vault.fetch_nav("latest")})
        context.report(refreshed_specs=[TARGET.as_string_id()])
    context.commit_metadata()
