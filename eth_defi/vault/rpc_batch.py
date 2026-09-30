"""Current-state batches for ordinary ERC-4626 admission and metadata reads.

Specialised adapters retain their protocol paths. HyperEVM is deliberately
excluded from these shared batches because HyperCore reads can poison a batch.
See https://eips.ethereum.org/EIPS/eip-4626 for the ordinary method semantics.
"""

import logging
import os
from decimal import Decimal
from typing import Iterator

from joblib import Parallel, delayed
from requests.exceptions import RequestException
from tqdm_loggable.auto import tqdm
from web3 import Web3
from web3.exceptions import Web3Exception

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault import DENOMINATION_UNAVAILABLE_EXCHANGE_RATE, UNKNOWN_EXCHANGE_RATE, ERC4626Vault, VaultReaderState
from eth_defi.event_reader.multicall_batcher import EncodedCall, read_multicall_chunked
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory
from eth_defi.vault.base import VaultBase
from eth_defi.vault.rpc_scan_state import classify_rpc_scan_failure, is_contract_read_failure

logger = logging.getLogger(__name__)


def fetch_metadata_snapshots(detections: list[ERC4262VaultDetection], web3factory: MultiProviderWeb3Factory, block_identifier: int, max_workers: int) -> dict[str, dict]:
    """Read ordinary metadata inputs in shared batches at one source block.

    Only generic ERC-4626, Morpho v1 and IPOR are enabled initially. Adapter
    consumers additionally verify method identity and the source block.
    HyperEVM and custom share-token paths keep their specialised reads.

    :param detections: Metadata candidates already selected as due.
    :param web3factory: Factory with phase accounting and verified chain ID.
    :param block_identifier: Common numeric block for this bounded batch.
    :param max_workers: Maximum threaded read concurrency.
    :return: Lower-case address to successful raw inputs and source block.
    """
    eligible = [d for d in detections if d.chain != 999 and (not d.features or d.features <= {ERC4626Feature.morpho_like, ERC4626Feature.ipor_like})]
    if not eligible:
        return {}
    calls = [EncodedCall.from_keccak_signature(address=Web3.to_checksum_address(detection.address), function=function, signature=Web3.keccak(text=function + "()")[:4], data=b"", extra_data={}) for detection in eligible for function in ("asset", "totalAssets", "totalSupply", "share")]
    snapshots = {}
    for result in read_multicall_chunked(eligible[0].chain, web3factory, calls, block_identifier=block_identifier, max_workers=max_workers, chunk_size=int(os.environ.get("METADATA_BATCH_SIZE", "40")), timestamped_results=False, backend="threading", progress_bar_desc="Batched metadata inputs", rpc_request_stats=web3factory.rpc_request_stats):
        snapshot = snapshots.setdefault(result.call.address.lower(), {"block": block_identifier})
        if result.success and len(result.result) == 32:
            snapshot[result.call.func_name] = result.result
        elif not result.success and result.call.func_name == "share":
            snapshot["share_reverted"] = True
    return snapshots


def fetch_batched_tvl_probes(vaults: list[VaultBase], web3factory: MultiProviderWeb3Factory, block_identifier: int, max_workers: int) -> Iterator[tuple[VaultBase, Decimal | None, bool]]:
    """Batch canonical total assets and preserve specialised NAV conversions.

    Method-identity checks keep adapters with overridden NAV/assets paths on
    individual reads. Failed subcalls produce explicit unavailable outcomes.
    Returned values carry no synthetic source timestamp.

    :param vaults: Due, already instantiated admission candidates.
    :param web3factory: Verified-chain factory with physical request accounting.
    :param block_identifier: Numeric source block for ordinary batched reads.
    :param max_workers: Maximum threaded read concurrency.
    :return: Vault, verified estimated USD TVL, and unsupported-conversion flag.
    """
    ordinary = [vault for vault in vaults if isinstance(vault, ERC4626Vault) and vault.chain_id != 999 and type(vault).fetch_nav is ERC4626Vault.fetch_nav and type(vault).fetch_total_assets is ERC4626Vault.fetch_total_assets]
    ordinary_ids = {id(vault) for vault in ordinary}
    specialised = [vault for vault in vaults if id(vault) not in ordinary_ids]
    rates = {}

    def fetch_conversion(vault: VaultBase) -> tuple[VaultBase, Decimal | None]:
        """Resolve one adapter's USD conversion before batching its assets.

        Admission failures leave this candidate unverified while other candidates
        continue. Programming errors still propagate to the phase boundary.

        :param vault: Ordinary adapter selected for a shared assets batch.
        :return: Vault and conversion rate, or None after an unavailable read.
        """
        try:
            reader = vault.get_historical_reader(stateful=True)
            state = reader.reader_state if isinstance(reader.reader_state, VaultReaderState) else VaultReaderState(vault)
            return vault, state.exchange_rate
        except (Web3Exception, RequestException, ValueError, ArithmeticError) as error:
            logger.warning("Admission conversion unavailable for %s: %s", vault.address, error)
            return vault, None

    conversions = Parallel(n_jobs=max_workers, backend="threading", return_as="generator")(delayed(fetch_conversion)(vault) for vault in ordinary)
    eligible = []
    for vault, rate in tqdm(conversions, total=len(ordinary), desc="Preparing admission conversions"):
        if rate is None:
            yield vault, None, False
        elif rate in (UNKNOWN_EXCHANGE_RATE, DENOMINATION_UNAVAILABLE_EXCHANGE_RATE):
            yield vault, None, True
        else:
            rates[vault.address.lower()] = rate
            eligible.append(vault)
    ordinary = eligible
    by_address = {vault.address.lower(): vault for vault in ordinary}
    calls = [EncodedCall.from_keccak_signature(address=vault.address, function="totalAssets", signature=Web3.keccak(text="totalAssets()")[:4], data=b"", extra_data={}) for vault in ordinary]
    if calls:
        observed = set()
        try:
            for result in read_multicall_chunked(ordinary[0].chain_id, web3factory, calls, block_identifier=block_identifier, max_workers=max_workers, chunk_size=int(os.environ.get("TVL_PROBE_BATCH_SIZE", "40")), timestamped_results=False, backend="threading", progress_bar_desc="Batched admission TVL", rpc_request_stats=web3factory.rpc_request_stats):
                vault = by_address[result.call.address.lower()]
                observed.add(vault.address.lower())
                if not result.success or len(result.result) != 32:
                    yield vault, None, False
                else:
                    assets = vault.denomination_token.convert_to_decimals(int.from_bytes(result.result, "big"))
                    yield vault, assets * rates[vault.address.lower()], False
        except (Web3Exception, RequestException, RuntimeError) as error:
            if classify_rpc_scan_failure(error) != "transient" and not is_contract_read_failure(error):
                raise
            logger.warning("Admission batch deferred; preserving unverified candidates: %s", error)
            yield from ((vault, None, False) for address, vault in by_address.items() if address not in observed)

    def fetch_specialised(vault: VaultBase) -> tuple[VaultBase, Decimal | None, bool]:
        """Use the adapter's own NAV and reader conversion on specialised paths.

        No block pinning is introduced for current-window HyperCore values.

        :param vault: Specialised adapter.
        :return: Probe outcome, retaining failures as unverified.
        """
        try:
            reader = vault.get_historical_reader(stateful=True)
            state = reader.reader_state if isinstance(reader.reader_state, VaultReaderState) else VaultReaderState(vault)
            rate = state.exchange_rate
            if rate in (UNKNOWN_EXCHANGE_RATE, DENOMINATION_UNAVAILABLE_EXCHANGE_RATE):
                return vault, None, True
            return vault, vault.fetch_nav() * rate, False
        except (Web3Exception, RequestException, ValueError, RuntimeError, ArithmeticError) as error:
            logger.warning("Specialised admission probe deferred for %s: %s", vault.address, error)
            return vault, None, False

    if specialised:
        yield from Parallel(n_jobs=max_workers, backend="threading", return_as="generator")(delayed(fetch_specialised)(vault) for vault in tqdm(specialised, desc="Specialised admission TVL"))
