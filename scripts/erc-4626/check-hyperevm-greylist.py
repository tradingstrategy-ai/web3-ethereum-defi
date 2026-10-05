"""Manually compare mixed and isolated HyperEVM reads without changing state.

Run with ``source .local-test.env && poetry run python
scripts/erc-4626/check-hyperevm-greylist.py``. Uses only JSON_RPC_HYPERLIQUID from
the supplied environment. The bounded check probes the first three configured
providers at their head and two older blocks. It writes no caches, prices or
reader state and does not retry a failing mixed payload.

See docs/README-hyperevm-hypercore-read-gas.md. HyperCore-backed success at a
historical block does not prove historical Core semantics; compare against the
pure EVM totalSupply results and treat availability as a provider capability.
"""

import logging
import os
from collections.abc import Iterator

from eth_abi import encode
from eth_typing import HexAddress
from tabulate import tabulate
from tqdm_loggable.auto import tqdm
from web3 import Web3

from eth_defi.event_reader.multicall_batcher import EncodedCall, MulticallNonRetryable, MulticallRetryable, MultiprocessMulticallReader, get_multicall_contract
from eth_defi.hyperliquid.constants import HYPEREVM_MULTICALL_GREYLIST
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.provider.named import get_provider_name

logger = logging.getLogger(__name__)
#: Trace-confirmed active Core reader used in the scanner's isolated lane.
HYPED = HexAddress("0x4d0ff6a0dd9f7316b674fb37993a3ce28bea340e")
#: Pure EVM control: failure here indicates provider health rather than Core access.
USD_T0 = HexAddress("0xb8ce59fc3717ada4c02eadf9682a9e934f625ebb")


def fetch_greylist_probe_results(rpc_url: str, block_offsets: tuple[int, ...] = (0, 200, 20_000), max_providers: int = 3) -> Iterator[dict]:
    """Compare bounded real requests using the operator's configured endpoints.

    This command is an external integration check, not a historical backfill.
    Repeated copies reproduce the documented batch amplification; one cheap
    ERC-20 selector verifies the robust lane is still readable. Revert payloads
    are summarised without provider exception strings in the result table.
    Full exception/debug diagnostics can include RPC URLs; redact before sharing.
    See the `HyperCore read-precompile documentation
    <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/interacting-with-hypercore>`__.

    :param rpc_url: Project-format space-separated provider endpoints.
    :param block_offsets: Non-negative distances behind each provider's own head.
    :param max_providers: Positive maximum endpoints to probe, default three.
    :return: Rows with provider host, integer source block/offset, lane-success
        summaries, isolated revert/unavailable counts and boolean ``robust_ok``.
        ``assets_raw`` and ``supply_raw`` are onchain integers or None when their
        read fails, not denominated NAV estimates.
    """
    assert max_providers > 0 and all(offset >= 0 for offset in block_offsets)
    # Tuple members are (target_address, Solidity signature, encoded arguments).
    # Duplicating the four scanner probes stresses aggregate precompile accounting
    # while the final cheap token read tests whether its neighbour remains usable.
    probes = [(HYPED, "totalAssets()", b""), (HYPED, "convertToAssets(uint256)", encode(["uint256"], [1])), (HYPED, "maxDeposit(address)", encode(["address"], ["0x0000000000000000000000000000000000000000"])), (HYPED, "totalSupply()", b"")]
    calls = [EncodedCall.from_keccak_signature(address, signature=Web3.keccak(text=signature)[:4], function=signature, data=arguments, extra_data={}) for address, signature, arguments in probes * 2 + [(USD_T0, "totalSupply()", b"")]]
    for endpoint in tqdm(rpc_url.split()[:max_providers], desc="HyperEVM provider check"):
        web3 = create_multi_provider_web3(endpoint, retries=0, hint="manual-greylist-check")
        assert web3.eth.chain_id == 999
        head = web3.eth.block_number
        reader = MultiprocessMulticallReader(web3, greylist=HYPEREVM_MULTICALL_GREYLIST)
        for offset in tqdm(block_offsets, desc=get_provider_name(web3.provider), leave=False):
            block = max(1, head - offset)
            contract = get_multicall_contract(web3, block_identifier=block)
            encoded = [(Web3.to_checksum_address(call.address), call.data) for call in calls]
            try:
                mixed = reader.fetch_multicall_with_batch_size(contract, block, len(calls), encoded, False)
                mixed_status = f"{sum(success for success, _data in mixed)}/{len(mixed)} successful"
            except (MulticallRetryable, MulticallNonRetryable) as error:
                mixed_status = type(error).__name__
            isolated = list(reader.process_calls(block, calls, allow_greylist_unavailable=True))
            assets = isolated[0]
            supply = isolated[3]
            # Served contract reverts and exhausted transport gas errors are
            # different failures. Keep them visible instead of calling every
            # unsuccessful subcall unavailable; no counter here estimates NAV.
            row = {
                "provider": get_provider_name(web3.provider),
                "block": block,
                "offset": offset,
                "mixed": mixed_status,
                "isolated": f"{sum(result.success for result in isolated)}/{len(isolated)} successful",
                "isolated_reverts": sum(not result.success and result.unavailable_error is None for result in isolated),
                "isolated_unavailable": sum(result.unavailable_error is not None for result in isolated),
                "assets_raw": int.from_bytes(assets.result, "big") if assets.success else None,
                "supply_raw": int.from_bytes(supply.result, "big") if supply.success else None,
                "robust_ok": isolated[-1].success,
            }
            assert row["robust_ok"], "Pure ERC-20 read failed; inspect provider health"
            logger.info("Checked provider=%s block=%s mixed=%s isolated=%s robust_ok=%s", row["provider"], block, mixed_status, row["isolated"], row["robust_ok"])
            yield row


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    rows = list(fetch_greylist_probe_results(os.environ["JSON_RPC_HYPERLIQUID"]))
    logger.info("HyperEVM integration results:\n%s", tabulate(rows, headers="keys"))
