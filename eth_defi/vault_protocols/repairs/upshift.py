"""Refresh reviewed Upshift accessor fields without resampling historical prices."""

from collections.abc import Callable
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from tqdm_loggable.auto import tqdm
from web3 import Web3

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.scan import fetch_deposit_permission
from eth_defi.erc_4626.vault_protocol.upshift.vault import UpshiftVault
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.repairs.price_backfill import _assert_rows_unchanged

#: Public EVM USDC-input inventory reviewed on 2026-10-30. No new rows are created.
TARGETS = (
    VaultSpec(1, "0x0A152C5b8D48C26619FF3ae85Ef1B54A79e20870"),  # USPC Custody Vault
    VaultSpec(1, "0xAD958C4c0c90bf0216e0f5472F074a9AB30f595F"),  # Axis Origin USDx
    VaultSpec(1, "0xcd69123b3FBBfC666E1f6a501da27B564C00De54"),  # Tori Ecosystem Vault
    VaultSpec(143, "0x42B3b62a6685E3a3F93483715Ef4300f5E7336CC"),  # earnAUSD Looping Vault
    VaultSpec(1, "0x5776B576474ad2513E550190C5855613859F3A26"),  # SharpByte RWA Ecosystem Vault
    VaultSpec(1, "0xE9B725010A9E419412ed67d0fA5f3A5f40159D32"),  # Upshift Core USDC
    VaultSpec(1, "0x955256B31097dDf47a9E47A95aDfDFB4460D8522"),  # NEMO USDC Prime
    VaultSpec(999, "0xcfe06d2499aE635830D11859941e76354D5717CC"),  # Coinmerce Capital USDC
    VaultSpec(1, "0x998D7b14c123c1982404562b68edDB057b0477cB"),  # Upshift Gamma USDC Vault
    VaultSpec(143, "0x36eDbF0C834591BFdfCaC0Ef9605528c75c406aA"),  # earnAUSD
    VaultSpec(1, "0x076f3f14d87eA2D34DB66A4b5b9f091918008552"),  # Upshift Clear RWA
    VaultSpec(1, "0x74aD2F789Ed583DBd141bbdafC673fE1F033718b"),  # Sentora USD
    VaultSpec(1, "0x02EA41404CCA842dF16e7fd327756192d576F503"),  # Sentora Protected USD
    VaultSpec(1, "0x80E1048eDE66ec4c364b4F22C8768fc657FF6A42"),  # Upshift USDC
    VaultSpec(1, "0x18EE038C114a07f4B08b420fb1E4149a4F357249"),  # Upshift Wildcat USD
)


def refresh_upshift_metadata(database: VaultDatabase, fetch_web3: Callable[[int], Web3] | None = None) -> dict:
    """Stage only lifecycle, admission, delay and liquidity accessor corrections.

    All RPC reads finish before mutating the staged database. Missing vaults
    are reported without changing discovery or creating price-history gaps.

    :param database: Migration-owned staged database.
    :param fetch_web3: Optional supplied per-chain connection factory for tests.
    :return: JSON-safe counts and missing reviewed identities.
    """
    connections = {}
    updates = []
    missing = []
    for spec in tqdm(TARGETS, desc="Refreshing Upshift metadata"):
        row = database.rows.get(spec)
        if row is None:
            missing.append(spec.as_string_id())
            continue
        detection = row.get("_detection_data")
        if detection is None or detection.get_spec() != spec or ERC4626Feature.upshift_like not in detection.features:
            raise ValueError(f"Conflicting Upshift detection at {spec.as_string_id()}")
        if spec.chain_id not in connections:
            connections[spec.chain_id] = fetch_web3(spec.chain_id) if fetch_web3 else create_multi_provider_web3(read_json_rpc_url(spec.chain_id))
        web3 = connections[spec.chain_id]
        assert web3.eth.chain_id == spec.chain_id
        vault = UpshiftVault(web3, spec, features=detection.features)
        updates.append(
            (
                row,
                {
                    "_deposit_manager": vault.get_deposit_manager_capability().as_initial_public_schema(),
                    "_deposit_permission": fetch_deposit_permission(vault).value,
                    "_lockup": vault.get_estimated_lock_up(),
                    "_withdrawal_period": vault.get_withdrawal_period(),
                    "_available_liquidity": vault.fetch_available_liquidity(),
                    "_utilisation": None,
                },
            )
        )
    changed = sum(any(row.get(key) != value for key, value in values.items()) for row, values in updates)
    for row, values in updates:
        row.update(values)
    return {"inspected_rows": len(updates), "changed_rows": changed, "missing": missing}


def _target_liquidity_mask(table: pa.Table) -> pa.ChunkedArray:
    """Select reviewed identities without matching missing chain/address values.

    :param table: Raw price rows with integer ``chain`` and string ``address``.
    :return: Non-null boolean mask for the fixed reviewed inventory.
    """
    mask = pa.chunked_array([pa.array([False] * len(table))])
    for chain in {spec.chain_id for spec in TARGETS}:
        addresses = pa.array([spec.vault_address for spec in TARGETS if spec.chain_id == chain])
        mask = pc.or_(mask, pc.and_(pc.equal(table["chain"], chain), pc.is_in(table["address"], value_set=addresses)))
    return pc.fill_null(mask, False)


def stage_upshift_liquidity_correction(source: Path, destination: Path) -> int:
    """Clear historical request caps mislabelled as available liquidity.

    Stream existing rows in their original order. Every other column, including
    every historical price and timestamp, is verified unchanged by readback.
    There is no state backfill, schema change or reader-state reset.

    :param source: Existing raw price Parquet file.
    :param destination: Separate staged file on the same filesystem.
    :return: Number of target liquidity observations replaced with null.
    """
    changed = 0
    with pq.ParquetFile(source) as original:
        schema = original.schema_arrow
        if "available_liquidity" not in schema.names:
            raise ValueError("Existing price file has no available_liquidity column")
        with pq.ParquetWriter(destination, schema=schema) as writer:
            for batch in tqdm(original.iter_batches(batch_size=100_000), desc="Correcting Upshift liquidity"):
                table = pa.Table.from_batches([batch])
                mask = _target_liquidity_mask(table)
                liquidity = table["available_liquidity"]
                changed += pc.sum(pc.and_(mask, pc.is_valid(liquidity))).as_py() or 0
                corrected = pc.if_else(mask, pa.scalar(None, type=liquidity.type), liquidity)
                writer.write_table(table.set_column(schema.get_field_index("available_liquidity"), schema.field("available_liquidity"), corrected))
    with pq.ParquetFile(source) as original, pq.ParquetFile(destination) as staged:
        assert original.metadata.num_rows == staged.metadata.num_rows, "Historical row count changed"
        assert original.schema_arrow.equals(staged.schema_arrow, check_metadata=True), "Historical schema changed"
        for before, after in zip(original.iter_batches(batch_size=100_000), staged.iter_batches(batch_size=100_000), strict=True):
            before_table, after_table = pa.Table.from_batches([before]), pa.Table.from_batches([after])
            _assert_rows_unchanged(before_table.drop(["available_liquidity"]), after_table.drop(["available_liquidity"]), "Upshift liquidity correction")
            unrelated = pc.invert(_target_liquidity_mask(before_table))
            _assert_rows_unchanged(before_table.filter(unrelated), after_table.filter(unrelated), "Unrelated vault liquidity")
    return changed
