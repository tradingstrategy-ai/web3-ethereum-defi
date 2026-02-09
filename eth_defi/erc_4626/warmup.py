"""Warmup system to detect broken vault contract calls.

Before running historical price scans, we test each vault's supported calls
to detect which ones revert or are too expensive. Results are stored in
VaultReaderState and persisted to disk.

See README-reader-states.md for documentation.
"""

import logging
from typing import Iterable

from web3 import Web3

from eth_defi.erc_4626.vault import ERC4626HistoricalReader

logger = logging.getLogger(__name__)

#: Maximum gas allowed for warmup calls before marking as broken
#: Calls using more gas than this are considered too expensive for multicall batching
#: TelosC Surge uses 36M gas for maxDeposit() - entire Plasma block limit
DEFAULT_MAX_GAS = 1_000_000


def warmup_vault_reader(
    reader: ERC4626HistoricalReader,
    block_number: int,
    max_gas: int = DEFAULT_MAX_GAS,
) -> dict[str, tuple[int, bool]]:
    """Test a single vault reader's supported calls.

    The reader provides the calls to test via get_warmup_calls().
    Each call is tested individually and the result stored in reader_state.

    Uses gas estimation to detect expensive calls before executing them.
    Calls using more gas than `max_gas` are marked as broken without execution.

    :param reader:
        The vault reader to test

    :param block_number:
        Block number to use for testing

    :param max_gas:
        Maximum allowed gas for a call. Calls exceeding this are marked broken.
        Defaults to DEFAULT_MAX_GAS (1M gas).

    :return:
        Dict of function_name -> (check_block, reverts) for newly checked calls
    """
    if reader.reader_state is None:
        return {}

    results = {}
    vault = reader.vault

    # Get warmup calls from the reader itself
    # Each reader provides its own (function_name, callable, contract_call) tuples
    for warmup_item in reader.get_warmup_calls():
        # Support both old (function_name, callable) and new (function_name, callable, contract_call) formats
        if len(warmup_item) == 2:
            function_name, test_callable = warmup_item
            contract_call = None
        else:
            function_name, test_callable, contract_call = warmup_item

        # Skip if we've already checked this function
        if reader.reader_state.get_call_status(function_name) is not None:
            continue

        reverts = False
        reason = None

        # First check gas estimation if we have a contract call
        if contract_call is not None:
            try:
                gas_estimate = contract_call.estimate_gas()
                if gas_estimate > max_gas:
                    reverts = True
                    reason = f"excessive gas: {gas_estimate:,} > {max_gas:,}"
                    logger.warning(
                        "Vault %s call %s uses excessive gas: %s",
                        vault.address, function_name, reason
                    )
            except Exception as e:
                # Gas estimation failed - call reverts
                reverts = True
                reason = f"gas estimation failed: {str(e)[:80]}"
                logger.info(
                    "Vault %s call %s gas estimation failed: %s",
                    vault.address, function_name, str(e)[:100]
                )

        # If gas estimation passed (or not available), try the actual call
        if not reverts:
            try:
                test_callable()
            except Exception as e:
                reverts = True
                reason = f"call failed: {str(e)[:80]}"
                logger.info(
                    "Vault %s call %s reverts: %s",
                    vault.address, function_name, str(e)[:100]
                )

        reader.reader_state.set_call_status(function_name, block_number, reverts)
        results[function_name] = (block_number, reverts)

        if reverts:
            logger.warning(
                "Marked %s.%s as broken at block %d (%s)",
                vault.address[:10], function_name, block_number, reason or "unknown"
            )

    return results


def warmup_vault_readers(
    web3: Web3,
    readers: Iterable[ERC4626HistoricalReader],
    block_number: int | None = None,
) -> dict[str, dict[str, tuple[int, bool]]]:
    """Run warmup checks on all vault readers.

    :param web3:
        Web3 connection

    :param readers:
        Iterable of vault readers to test

    :param block_number:
        Block number to use for testing. Defaults to latest.

    :return:
        Dict of vault_address -> {function_name -> (check_block, reverts)}
    """
    if block_number is None:
        block_number = web3.eth.block_number

    results = {}
    checked_count = 0
    broken_count = 0

    for reader in readers:
        vault_results = warmup_vault_reader(reader, block_number)
        if vault_results:
            results[reader.vault.address] = vault_results
            checked_count += len(vault_results)
            broken_count += sum(1 for _, reverts in vault_results.values() if reverts)

    logger.info(
        "Warmup complete: checked %d calls across %d vaults, %d broken",
        checked_count, len(results), broken_count
    )

    return results
