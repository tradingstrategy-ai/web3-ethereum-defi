"""Guarded real-provider metadata parity and bounded historical scan checks.

Use RPC_PARITY_CHECK=true for adapter parity, RPC_SCAN_CHECK=true for nine
Arbitrum samples (RPC_CHECK_CHAIN_ID=999 selects recent HyperEVM blocks),
or RPC_BATCH_CHECK=true for a catalogue-backed 40/100 check.
All outputs and token/reader caches are temporary; catalogue input is read-only.
"""

import logging
import math
import os
import time
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import pyarrow.parquet as pq
from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import ACTIVITY_STATUS_MIN_NAV, create_vault_scan_record
from eth_defi.event_reader.multicall_batcher import get_multicall_batch_size_from_env
from eth_defi.hyperliquid.constants import HYPEREVM_MULTICALL_GREYLIST
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.provider.rpcdb import RPCRequestStats
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.historical import scan_historical_prices_to_parquet
from eth_defi.vault.rpc_batch import fetch_metadata_snapshots, prepare_metadata_batch
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)

#: Existing integration targets, plus the local catalogue's Arbitrum IPOR anchor.
TARGETS = {
    1: (("sDAI", "0x83F20F44975D03b1b09e64809B757c47f942BEeA", set()), ("Morpho v1", "0xbEef047a543E45807105E51A8BBEFCc5950fcfBa", {ERC4626Feature.morpho_like}), ("IPOR", "0xf8f226da66244f89e70c5b5d1a5c5b0d505eb1d8", {ERC4626Feature.ipor_like})),
    42161: (("Morpho v2", "0xbeefff13dd098de415e07f033dae65205b31a894", {ERC4626Feature.morpho_v2_like}), ("IPOR", "0xa91267a25939b2b0f046013fbf9597008f7f014b", {ERC4626Feature.ipor_like})),
    999: (("HypurrFi", "0x8001e1e7b05990d22dd8cdb9737f9fe6589827ce", {ERC4626Feature.hypurrfi_like}), ("Hyperdrive Liquidator", "0x9271a5c684330b2a6775e96b3c140fc1dc3c89be", {ERC4626Feature.hyperdrive_hl_like}), ("HYPED", "0x4d0ff6a0dd9f7316b674fb37993a3ce28bea340e", {ERC4626Feature.hyperdrive_hl_like})),
    8453: (("IPOR", "0xd6701905c59ee618dc36dc747506bce0a4ac760a", {ERC4626Feature.ipor_like}),),
}
RPC_VARIABLES = {1: "JSON_RPC_ETHEREUM", 42161: "JSON_RPC_ARBITRUM", 8453: "JSON_RPC_BASE", 999: "JSON_RPC_HYPERLIQUID"}


def make_detections(chain_id: int, first_block: int) -> list[ERC4262VaultDetection]:
    """Build the small reviewed test cohort without running event discovery.

    Source deployment is checked separately before bounded historical reads.

    :param chain_id: Supported check chain.
    :param first_block: Lower scan bound, not a claimed deployment block.
    :return: Classified candidates for the fixed integration targets.
    """
    now = native_datetime_utc_now()
    return [ERC4262VaultDetection(chain=chain_id, address=Web3.to_checksum_address(address), features=features, first_seen_at_block=first_block, first_seen_at=now, updated_at=now, deposit_count=100, redeem_count=0) for _, address, features in TARGETS[chain_id]]


def log_requests(stats: RPCRequestStats, label: str, elapsed: float) -> int:
    """Report physical attempts and redacted domains for manual-test evidence.

    Encoded calls are not reported as requests. Counters include failed attempts.

    :param stats: Accumulator for the measured path.
    :param label: Check and batch-size identification.
    :param elapsed: Measured wall-clock seconds.
    :return: Total physical attempts.
    """
    calls, errors = stats.export()
    count = sum(calls.values())
    logger.info("%s: requests=%d errors=%s elapsed=%.2fs providers=%s", label, count, errors, elapsed, sorted({domain for domain, _ in calls}))
    return count


def check_metadata(chain_id: int, batch_size: int, directory: Path) -> None:
    """Compare full adapter rows using independent direct and prepared caches.

    Both paths use the same numeric block, including Morpho fee observations.

    :param chain_id: Chain selected from the reviewed target table.
    :param batch_size: Regular payload limit for preparation and warmup.
    :param directory: Temporary cache directory owned by the caller.
    :return: None; asserts economic parity and non-broken metadata.
    """
    rpc_url = os.environ[RPC_VARIABLES[chain_id]]
    connection = create_multi_provider_web3(rpc_url)
    assert connection.eth.chain_id == chain_id
    block = connection.eth.block_number - 16
    detections = make_detections(chain_id, 1)
    rows = []
    for mode in ("direct", "prepared"):
        stats = RPCRequestStats(operation="metadata_" + mode)
        factory = MultiProviderWeb3Factory(rpc_url, expected_chain_id=chain_id, skip_verification=True, rpc_request_stats=stats)
        web3 = factory()
        cache = TokenDiskCache(directory / f"{chain_id}-{mode}.sqlite")
        started = time.monotonic()
        try:
            snapshots = prepare_metadata_batch(detections, factory, block, 2, cache, min_lending_nav=ACTIVITY_STATUS_MIN_NAV, batch_size=batch_size) if mode == "prepared" else {}
            mode_rows = []
            for detection in detections:
                row = create_vault_scan_record(web3, detection, block, token_cache=cache, metadata_snapshot=snapshots.get(detection.address.lower()))
                assert row["Name"] and not row["Name"].startswith("<broken"), (chain_id, detection.address, mode)
                mode_rows.append(row)
            rows.append(mode_rows)
        finally:
            cache.close()
        log_requests(stats, f"metadata chain={chain_id} block={block} {mode}", time.monotonic() - started)
    for direct, prepared in zip(*rows, strict=True):
        for key in ("NAV", "Shares", "Denomination", "Share token", "_fees", "_flags", "_available_liquidity", "_utilisation"):
            assert direct[key] == prepared[key], (chain_id, direct["Name"], key, direct[key], prepared[key])
        logger.info("PASS metadata parity chain=%d block=%d vault=%s", chain_id, block, direct["_detection_data"].address)


def check_historical_scan(chain_id: int, batch_size: int, max_workers: int, directory: Path) -> None:
    """Run nine historical samples through the real writer and stateful consumer.

    Arbitrum uses an hourly grid; HyperEVM uses 90 recent blocks to test its
    isolated lane without attempting a HyperCore historical backfill.
    Explicit bounds replay a previous run. Each invocation gets independent
    temporary token, timestamp and reader state, and never publishes to production.

    :param chain_id: Arbitrum (42161) or HyperEVM (999).
    :param batch_size: Regular physical payload limit, including token warmup.
    :param max_workers: Bounded historical worker count.
    :param directory: Temporary directory for all outputs and caches.
    :return: None; asserts source identity, numeric values and reader progress.
    """
    if chain_id not in (42161, 999):
        raise ValueError("Historical smoke checks support Arbitrum or HyperEVM")
    chain_name = "HyperEVM" if chain_id == 999 else "Arbitrum"
    span, step = (90, 10) if chain_id == 999 else (129600, 14400)
    greylist = HYPEREVM_MULTICALL_GREYLIST if chain_id == 999 else frozenset()
    rpc_url = os.environ[RPC_VARIABLES[chain_id]]
    stats = RPCRequestStats(operation=f"manual_scan_{chain_id}")
    web3 = create_multi_provider_web3(rpc_url, rpc_request_stats=stats)
    assert web3.eth.chain_id == chain_id
    end_block = int(os.environ["END_BLOCK"]) if os.environ.get("END_BLOCK") else web3.eth.block_number - 16
    start_block = int(os.environ.get("START_BLOCK", end_block - span))
    if not 0 < start_block < end_block or end_block - start_block > span:
        raise ValueError(f"Require 0 < START_BLOCK < END_BLOCK and at most {span} {chain_name} blocks")
    logger.info("%s manual scan START_BLOCK=%d END_BLOCK=%d step=%d requested_size=%d regular_cap=%d greylist_size=1", chain_name, start_block, end_block, step, batch_size, min(batch_size, 40) if chain_id == 999 else batch_size)
    factory = MultiProviderWeb3Factory(rpc_url, expected_chain_id=chain_id, skip_verification=True, rpc_request_stats=stats)
    config = configure_hypersync_from_env(web3)
    assert config.hypersync_client is not None
    detections = make_detections(chain_id, start_block)
    cache = TokenDiskCache(directory / "tokens.sqlite")
    started = time.monotonic()
    invalid_metadata = []
    try:
        snapshots = prepare_metadata_batch(detections, factory, end_block - 1, max_workers, cache, min_lending_nav=ACTIVITY_STATUS_MIN_NAV, batch_size=batch_size)
        vaults = []
        for detection in detections:
            assert web3.eth.get_code(detection.address, start_block), f"Vault not deployed at start: {detection.address}"
            vault = create_vault_instance(web3, detection.address, detection.features, token_cache=cache, default_block_identifier=end_block - 1)
            if snapshot := snapshots.get(detection.address.lower()):
                vault._rpc_metadata_snapshot = snapshot
            vault.first_seen_at_block = start_block
            vaults.append(vault)
        report = scan_historical_prices_to_parquet(output_fname=directory / "prices.parquet", web3=web3, web3factory=factory, vaults=vaults, token_cache=cache, start_block=start_block, end_block=end_block, step=step, frequency="1h", reader_states={}, write_all_samples=True, max_workers=max_workers, hypersync_client=config.hypersync_client, timestamp_cache_file=directory / "timestamps", rpc_request_stats=stats, batch_size=batch_size, greylist=greylist)
        table = pq.read_table(report["output_fname"])
        samples = table.to_pylist()
        assert samples, "No historical rows produced"
        expected = {vault.address.lower(): vault for vault in vaults}
        regular = set(expected) - {address.lower() for address in greylist}
        assert regular <= {row["address"] for row in samples}, "A regular vault lost all observations"
        assert {row["address"] for row in samples} <= set(expected)
        for row in samples:
            assert row["chain"] == chain_id
            assert start_block <= row["block_number"] < end_block
            assert row["block_number"] in range(start_block, end_block, step)
            assert row["share_price"] is not None and math.isfinite(row["share_price"])
            assert row["total_assets"] is not None and math.isfinite(row["total_assets"])
        # Independent exact-block reads validate one real observation per vault.
        for address, vault in expected.items():
            observed = [row for row in samples if row["address"] == address]
            state = report["reader_states"][vault.spec]
            denomination, shares = vault.denomination_token, vault.share_token
            logger.info("Scan token metadata vault=%s denomination=%s symbol=%s decimals=%s share=%s share_decimals=%s", address, denomination.address if denomination else None, denomination.symbol if denomination else None, denomination.decimals if denomination else None, shares.address, shares.decimals)
            if denomination is None or denomination.decimals <= 0 or shares.decimals <= 0:
                invalid_metadata.append(address)
                logger.error("INVALID token metadata vault=%s; scaled economics are not validated", address)
            if address in {target.lower() for target in greylist}:
                if not observed:
                    assert state["last_block"] is None, "Unavailable isolated vault advanced progress"
                    logger.warning("DEFERRED isolated vault=%s rows=0 last_block=%s errors=%s", address, state["last_block"], state["rpc_error_count"])
                else:
                    assert state["last_block"] == observed[-1]["block_number"]
                    logger.info("ISOLATED observations vault=%s rows=%d last_block=%s; HyperCore availability does not establish historical NAV semantics", address, len(observed), state["last_block"])
                continue
            row = observed[-1]
            direct_assets = vault.fetch_total_assets(row["block_number"])
            # Historical ERC-4626 prices use convertToAssets, including virtual
            # shares and empty-vault behaviour, rather than assets / supply.
            raw_price = vault.vault_contract.functions.convertToAssets(vault.share_token.convert_to_raw(Decimal(1))).call(block_identifier=row["block_number"])
            direct_price = vault.denomination_token.convert_to_decimals(raw_price)
            assert math.isclose(float(direct_assets), row["total_assets"], rel_tol=1e-8)
            assert math.isclose(float(direct_price), row["share_price"], rel_tol=1e-8)
            assert state["last_block"] == row["block_number"]
            logger.info("PASS scan vault=%s rows=%d last_block=%d cadence=%s timestamps=%s..%s", address, len(observed), state["last_block"], state["vault_poll_frequency"], observed[0]["timestamp"], row["timestamp"])
        # Keep comparable rows in the log, excluding volatile write timestamps.
        logger.info("Scan values: %s", [(r["address"], r["block_number"], r["share_price"], r["total_assets"]) for r in samples])
    finally:
        cache.close()
        log_requests(stats, f"{chain_name} scan {start_block}..{end_block} size={batch_size}", time.monotonic() - started)
        logger.info("Operation requests: %s", dict(stats.operation_calls))
    assert not invalid_metadata, f"Invalid token decimals for scanned vaults: {invalid_metadata}"


def check_batch_sizes(chain_id: int) -> None:
    """Compare actual full metadata payloads at 40 and 100 using distinct vaults.

    The catalogue is a read-only source of known deployments. Served optional
    subcall failures must agree; unavailable transport fails the check outright.

    :param chain_id: Chain selected by RPC_CHECK_CHAIN_ID, default Arbitrum.
    :return: None; asserts identical inputs and fewer physical attempts.
    """
    rpc_url = os.environ[RPC_VARIABLES[chain_id]]
    web3 = create_multi_provider_web3(rpc_url)
    assert web3.eth.chain_id == chain_id
    block = web3.eth.block_number - 16
    database = VaultDatabase.read(Path(os.environ.get("VAULT_DATABASE", DEFAULT_VAULT_DATABASE)))
    detections = [row["_detection_data"] for spec, row in database.rows.items() if spec.chain_id == chain_id and not str(row.get("Name", "")).startswith("<broken") and row.get("_detection_data") is not None and row["_detection_data"].features in (set(), {ERC4626Feature.morpho_like}, {ERC4626Feature.morpho_v2_like}, {ERC4626Feature.ipor_like})][:26]
    assert len(detections) >= 25, "Require at least 25 distinct reviewed vaults in the local catalogue"
    outputs, counts = [], []
    for size in (40, 100):
        stats = RPCRequestStats(operation="batch_size_parity")
        factory = MultiProviderWeb3Factory(rpc_url, expected_chain_id=chain_id, skip_verification=True, rpc_request_stats=stats)
        started = time.monotonic()
        outputs.append(fetch_metadata_snapshots(detections, factory, block, 2, batch_size=size, include_optional=True))
        counts.append(log_requests(stats, f"batch parity chain={chain_id} block={block} size={size} targets={len(detections)}", time.monotonic() - started))
    assert outputs[0] == outputs[1], "40/100 economic inputs differ"
    assert counts[1] < counts[0], counts
    logger.info("PASS 40/100 full-batch parity chain=%d block=%d vaults=%s", chain_id, block, [d.address for d in detections])


def main() -> None:
    """Run only explicitly enabled manual integration checks.

    Separate modes keep the historical scan bounded and avoid requiring other
    chains' credentials for a single-chain run.

    :return: None; failed or incomplete checks exit with an exception.
    """
    modes = {name: os.environ.get(name, "false").lower() == "true" for name in ("RPC_PARITY_CHECK", "RPC_SCAN_CHECK", "RPC_BATCH_CHECK")}
    if not any(modes.values()):
        raise ValueError("Set RPC_PARITY_CHECK=true, RPC_SCAN_CHECK=true or RPC_BATCH_CHECK=true")
    setup_console_logging(os.environ.get("LOG_LEVEL", "info"))
    batch_size = get_multicall_batch_size_from_env()
    max_workers = int(os.environ.get("MAX_WORKERS", "2"))
    if max_workers <= 0:
        raise ValueError("MAX_WORKERS must be positive")
    with TemporaryDirectory(prefix="vault-rpc-check-") as temporary:
        directory = Path(temporary)
        if modes["RPC_SCAN_CHECK"]:
            check_historical_scan(int(os.environ.get("RPC_CHECK_CHAIN_ID", "42161")), batch_size, max_workers, directory)
        if modes["RPC_PARITY_CHECK"]:
            chains = [int(os.environ["RPC_CHECK_CHAIN_ID"])] if os.environ.get("RPC_CHECK_CHAIN_ID") else (1, 42161, 8453)
            for chain_id in chains:
                check_metadata(chain_id, batch_size, directory)
        if modes["RPC_BATCH_CHECK"]:
            check_batch_sizes(int(os.environ.get("RPC_CHECK_CHAIN_ID", "42161")))


if __name__ == "__main__":
    main()
