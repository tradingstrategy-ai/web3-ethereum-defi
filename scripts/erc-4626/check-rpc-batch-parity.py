"""Minimal real-provider parity check for ordinary, Morpho v1 and IPOR reads.

Set RPC_PARITY_CHECK=true and supply JSON_RPC_ETHEREUM through the usual local
secrets. All cache writes use a temporary directory; production state is not
read or changed. Values are compared at one numeric current-state block.
"""

import logging
import os
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault import ERC4626Vault, VaultReaderState
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.provider.rpcdb import RPCRequestStats
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.rpc_batch import fetch_batched_tvl_probes, fetch_metadata_snapshots

logger = logging.getLogger(__name__)

#: Reviewed Ethereum products already used by the repository integrations.
TARGETS = (
    ("sDAI", "0x83F20F44975D03b1b09e64809B757c47f942BEeA", set()),
    ("Morpho v1", "0xbEef047a543E45807105E51A8BBEFCc5950fcfBa", {ERC4626Feature.morpho_like}),
    ("IPOR", "0xf8f226da66244f89e70c5b5d1a5c5b0d505eb1d8", {ERC4626Feature.ipor_like}),
)


def main() -> None:
    """Check exact fixed-block metadata/TVL parity against the actual provider.

    The guard prevents accidental requests when merely checking script imports.
    Every required value must be present; broken metadata is a failed check.

    :return: None; raises on mismatch and logs redacted successful evidence.
    """
    if os.environ.get("RPC_PARITY_CHECK", "false").lower() != "true":
        raise ValueError("Set RPC_PARITY_CHECK=true for the intentional real-provider check")
    rpc_url = os.environ["JSON_RPC_ETHEREUM"]
    original_rollout = os.environ.get("VAULT_RPC_OPTIMISATIONS")
    setup_console_logging(os.environ.get("LOG_LEVEL", "info"))
    stats = RPCRequestStats(operation="parity")
    web3 = create_multi_provider_web3(rpc_url, rpc_request_stats=stats)
    assert web3.eth.chain_id == 1
    block = web3.eth.block_number - 16
    factory = MultiProviderWeb3Factory(rpc_url, expected_chain_id=1, skip_verification=True, rpc_request_stats=stats)
    now = native_datetime_utc_now()
    detections = [ERC4262VaultDetection(chain=1, address=Web3.to_checksum_address(address), features=features, first_seen_at_block=1, first_seen_at=now, updated_at=now, deposit_count=100, redeem_count=0) for _, address, features in TARGETS]
    snapshots = fetch_metadata_snapshots(detections, factory, block, max_workers=1)
    with TemporaryDirectory(prefix="vault-rpc-parity-") as temporary:
        caches = [TokenDiskCache(Path(temporary) / f"tokens-{mode}.sqlite") for mode in ("individual", "batch")]
        try:
            for (name, _, _), detection in zip(TARGETS, detections, strict=True):
                logger.info("Checking %s metadata at Ethereum block %d", name, block)
                os.environ["VAULT_RPC_OPTIMISATIONS"] = "false"
                individual = create_vault_scan_record(web3, detection, block, token_cache=caches[0])
                os.environ["VAULT_RPC_OPTIMISATIONS"] = "true"
                batched = create_vault_scan_record(web3, detection, block, token_cache=caches[1], metadata_snapshot=snapshots[detection.address.lower()])
                assert not str(individual["Name"]).startswith("<broken"), name
                assert not str(batched["Name"]).startswith("<broken"), name
                for key in ("NAV", "Shares", "Denomination", "Share token", "_available_liquidity", "_utilisation"):
                    assert individual[key] == batched[key], (name, key, individual[key], batched[key])
                vault = create_vault_instance(web3, detection.address, detection.features, token_cache=caches[1], default_block_identifier=block)
                if type(vault).fetch_nav is ERC4626Vault.fetch_nav and type(vault).fetch_total_assets is ERC4626Vault.fetch_total_assets:
                    expected = vault.fetch_nav(block) * VaultReaderState(vault).exchange_rate
                    outcomes = list(fetch_batched_tvl_probes([vault], factory, block, max_workers=1))
                    assert len(outcomes) == 1 and not outcomes[0][2]
                    assert isinstance(outcomes[0][1], Decimal) and outcomes[0][1] == expected, name
                logger.info("PASS %s fixed-block metadata and eligible admission parity", name)
        finally:
            for cache in caches:
                cache.close()
            if original_rollout is None:
                os.environ.pop("VAULT_RPC_OPTIMISATIONS", None)
            else:
                os.environ["VAULT_RPC_OPTIMISATIONS"] = original_rollout
    calls, _errors = stats.export()
    logger.info("Real-provider parity completed: three protocols, block %d; physical requests=%d; provider domains=%s", block, sum(calls.values()), sorted({domain for domain, _method in calls}))


if __name__ == "__main__":
    main()
