"""Current-state batches for ordinary ERC-4626 admission and metadata reads.

Specialised adapters retain their protocol paths. HyperEVM is deliberately
excluded from these shared batches because HyperCore reads can poison a batch.
See https://eips.ethereum.org/EIPS/eip-4626 for the ordinary method semantics.
"""

import logging
from decimal import Decimal
from typing import Iterator

from joblib import Parallel, delayed
from requests.exceptions import RequestException
from tqdm_loggable.auto import tqdm
from web3 import Web3
from web3.exceptions import Web3Exception

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault import DENOMINATION_UNAVAILABLE_EXCHANGE_RATE, UNKNOWN_EXCHANGE_RATE, ERC4626Vault, VaultReaderState
from eth_defi.erc_4626.vault_protocol.ipor.vault import IPORVault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import MorphoV1Vault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault
from eth_defi.event_reader.multicall_batcher import DEFAULT_MULTICALL_BATCH_SIZE, EncodedCall, read_multicall_chunked
from eth_defi.middleware import ProbablyNodeHasNoBlock
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory
from eth_defi.token import TokenDetails, TokenDiskCache, fetch_erc20_details
from eth_defi.vault.base import VaultBase
from eth_defi.vault.rpc_scan_state import classify_rpc_scan_failure, is_contract_read_failure

logger = logging.getLogger(__name__)


def fetch_metadata_snapshots(detections: list[ERC4262VaultDetection], web3factory: MultiProviderWeb3Factory, block_identifier: int, max_workers: int, *, batch_size: int = DEFAULT_MULTICALL_BATCH_SIZE, include_optional: bool = False) -> dict[str, dict]:
    """Read ordinary metadata inputs in shared batches at one source block.

    Only generic ERC-4626, Morpho v1/v2 and IPOR are enabled. Adapter
    consumers additionally verify method identity and the source block.
    HyperEVM and other adapter families keep their specialised reads.

    Called by lead discovery and pending-metadata recovery after scheduling has
    selected candidates. These are raw inputs, not final metadata records:
    adapters remain responsible for token decoding and protocol economics.
    Pinning the inputs to one block lets those adapters reject a snapshot from
    a different observation rather than silently mixing old and new values.

    :param detections: Metadata candidates already selected as due.
    :param web3factory: Factory with phase accounting and verified chain ID.
    :param block_identifier: Common numeric block for this bounded batch.
    :param max_workers: Maximum threaded read concurrency.
    :param batch_size:
        Positive maximum encoded subcalls per chunk, default 100. Each vault
        contributes four core subcalls, plus optional paused/fee probes when
        requested. This counts calls rather than vaults. Scanner entrypoints
        can override the default for a constrained provider. Existing Multicall
        chain limits and failure handling may further split a chunk.
    :return: Lower-case address to successful raw inputs and source block.
    :param include_optional: Include reviewed paused/fee inputs in the core pass.
    :raises ValueError: If batch_size is not positive.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    # This allowlist is deliberately narrower than the ERC-4626 interface.
    # Protocol detection does not prove that an adapter's asset/share methods
    # have standard semantics. Extend it only alongside adapter parity checks.
    eligible = [d for d in detections if _metadata_adapter(d) is not None]
    if len({d.chain for d in detections}) > 1:
        raise ValueError("Metadata batches must contain one chain")
    if type(block_identifier) is not int:
        raise ValueError("Metadata source block must be numeric")
    if not eligible:
        return {}
    # Batch limits count encoded subcalls, not vaults.
    # Metadata consumers need the source block but no block timestamp, so
    # timestamped_results=False avoids an otherwise redundant RPC per chunk.
    calls = []
    for detection in eligible:
        functions = ["asset", "totalAssets", "totalSupply", "share"]
        if include_optional:
            adapter = _metadata_adapter(detection)
            functions.append("paused")
            if adapter is MorphoV1Vault:
                functions.append("fee")
            elif adapter is MorphoV2Vault:
                functions.extend(("performanceFee", "managementFee"))
        calls.extend(EncodedCall.from_keccak_signature(address=Web3.to_checksum_address(detection.address), function=function, signature=Web3.keccak(text=function + "()")[:4], data=b"", extra_data={}) for function in functions)

    snapshots = {}
    for result in read_multicall_chunked(eligible[0].chain, web3factory, calls, block_identifier=block_identifier, max_workers=max_workers, chunk_size=batch_size, batch_size=batch_size, timestamped_results=False, backend="threading", progress_bar_desc="Batched metadata inputs", rpc_request_stats=web3factory.rpc_request_stats):
        snapshot = snapshots.setdefault(result.call.address.lower(), {"block": block_identifier})
        if result.success and len(result.result) == 32:
            snapshot[result.call.func_name] = result.result
        elif not result.success and result.call.func_name == "paused" and include_optional:
            snapshot["paused_reverted"] = True
        elif not result.success and result.call.func_name == "share":
            # Ordinary vaults are their own share token when share() reverts.
            # Empty successful data is not proof of that convention; leave it
            # absent so the adapter performs its normal validation/fallback.
            snapshot["share_reverted"] = True
    return snapshots


def _metadata_adapter(detection: ERC4262VaultDetection) -> type[ERC4626Vault] | None:
    """Select only the exact feature sets whose raw inputs have been reviewed.

    Specialised or ambiguous detections retain adapter-owned reads. Ordinary
    ERC-4626 semantics are defined at https://eips.ethereum.org/EIPS/eip-4626.

    :param detection: Candidate with chain and classifier features.
    :return: Reviewed adapter class, or None when batching is inappropriate.
    """
    if detection.chain == 999:
        return None
    families = {frozenset(): ERC4626Vault, frozenset({ERC4626Feature.morpho_like}): MorphoV1Vault, frozenset({ERC4626Feature.ipor_like}): IPORVault, frozenset({ERC4626Feature.morpho_v2_like}): MorphoV2Vault}
    return families.get(frozenset(detection.features))


def is_metadata_snapshot_compatible(vault: VaultBase, snapshot: dict, block_identifier: int) -> bool:
    """Validate ephemeral input identity before attaching it to an adapter.

    Exact adapter classes exclude specialised subclasses with different
    economics. Legacy core-only snapshots retain their existing block check.

    :param vault: Actual constructed adapter, never a planning-only instance.
    :param snapshot: Raw observations from metadata preparation.
    :param block_identifier: Requested numeric metadata source block.
    :return: Whether the snapshot can be consumed by this adapter.
    """
    if type(vault) not in (ERC4626Vault, MorphoV1Vault, IPORVault, MorphoV2Vault):
        return False
    if snapshot.get("block") != block_identifier:
        return False
    if "adapter" not in snapshot:
        return True
    return snapshot.get("adapter") == type(vault).__name__ and snapshot.get("chain") == vault.chain_id and snapshot.get("vault") == vault.address.lower()


def prepare_metadata_batch(
    detections: list[ERC4262VaultDetection],
    web3factory: MultiProviderWeb3Factory,
    block_identifier: int,
    max_workers: int,
    token_cache: TokenDiskCache,
    *,
    min_lending_nav: Decimal,
    batch_size: int = DEFAULT_MULTICALL_BATCH_SIZE,
) -> dict[str, dict]:
    """Prepare shared metadata, token details and eligible idle balances.

    Normal discovery and retry recovery use the same fixed-block inputs.
    Exhausted transport failures propagate before any per-vault fallback work;
    snapshots and partially completed batches are never durable catalogue state.

    :param detections: Due candidates from one chain.
    :param web3factory: Verified-chain worker factory with request accounting.
    :param block_identifier: Numeric block for all economic inputs.
    :param max_workers: Maximum concurrent threaded batch reads.
    :param token_cache: Caller-owned token cache, warmed before metadata workers.
    :param min_lending_nav: Existing scan eligibility threshold in token units.
    :param batch_size: Positive regular payload limit, also used for token warmup.
    :return: Lower-case vault address to ephemeral source-identified snapshots.
    """
    snapshots = fetch_metadata_snapshots(detections, web3factory, block_identifier, max_workers, batch_size=batch_size, include_optional=True)
    if not snapshots:
        return snapshots
    by_address = {d.address.lower(): d for d in detections}
    token_addresses = set()
    for address, snapshot in snapshots.items():
        detection = by_address[address]
        snapshot.update(chain=detection.chain, vault=address, adapter=_metadata_adapter(detection).__name__)
        token_addresses.add(Web3.to_checksum_address(address))
        for field in ("asset", "share"):
            value = snapshot.get(field)
            if value is not None and any(value) and not any(value[:12]):
                token_addresses.add(Web3.to_checksum_address("0x" + value[-20:].hex()))
    chain_id = detections[0].chain
    token_cache.load_token_details_with_multicall(chain_id, web3factory, sorted(token_addresses), block_identifier=block_identifier, max_workers=max_workers, batch_size=batch_size, display_progress=True)
    web3 = web3factory()
    idle_calls = []
    for address, snapshot in snapshots.items():
        if snapshot["adapter"] not in ("MorphoV1Vault", "MorphoV2Vault", "IPORVault"):
            continue
        asset, assets = snapshot.get("asset"), snapshot.get("totalAssets")
        if asset is None or assets is None or any(asset[:12]) or not any(asset):
            continue
        token_address = Web3.to_checksum_address("0x" + asset[-20:].hex())
        entry = token_cache.get(TokenDetails.generate_cache_key(chain_id, token_address))
        # Supported scanner denomination tokens have positive decimals. Zero is
        # expected unavailable metadata; retain adapter fallback, without new
        # token-cache provenance fields or a zero-decimal support path.
        if entry is None or not 0 < entry.get("decimals", 0) <= 255:
            continue
        token = fetch_erc20_details(web3, token_address, chain_id=chain_id, cache=token_cache)
        if token.convert_to_decimals(int.from_bytes(assets, "big")) <= min_lending_nav:
            continue
        snapshot["idle_token"] = token_address.lower()
        idle_calls.append(EncodedCall.from_keccak_signature(address=token_address, function="balanceOf", signature=Web3.keccak(text="balanceOf(address)")[:4], data=bytes.fromhex(address[2:]).rjust(32, b"\0"), extra_data={"vault": address}))
    for result in read_multicall_chunked(chain_id, web3factory, idle_calls, block_identifier=block_identifier, max_workers=max_workers, chunk_size=batch_size, batch_size=batch_size, timestamped_results=False, backend="threading", rpc_request_stats=web3factory.rpc_request_stats):
        if result.success and len(result.result) == 32:
            snapshots[result.call.extra_data["vault"]]["idle_balance"] = result.result
    logger.info("Prepared metadata: candidates=%d snapshots=%d tokens=%d idle_subcalls=%d", len(detections), len(snapshots), len(token_addresses), len(idle_calls))
    return snapshots


def fetch_current_vault_tvl_usd(vault: VaultBase) -> tuple[Decimal | None, bool]:
    """Probe a discovered vault's current TVL using its existing USD conversion.

    The probe lets a live scan include a meaningful vault even when it has
    fewer than the normal number of deposit events. The vault's own reader
    state supplies protocol-specific exchange-rate overrides. An unknown
    conversion is reported separately and never treated as verified USD TVL.

    Specialised admission batches and direct callers share this path. The adapter
    owns the timing and composition of its NAV, including live HyperCore values;
    forcing the ordinary batch block onto these reads could make them unavailable.

    :param vault:
        Vault adapter to read at the provider's current state.
    :return:
        ``(tvl_usd, unsupported_conversion)``. The first member is a Decimal USD
        estimate or None; the second is True when the denomination rate is
        unknown or unavailable. A failed read returns ``(None, False)`` so it is
        distinguishable from an unsupported conversion returning ``(None, True)``.
    """
    try:
        reader = vault.get_historical_reader(stateful=True)
        state = reader.reader_state if isinstance(reader.reader_state, VaultReaderState) else VaultReaderState(vault)
        rate = state.exchange_rate
        if rate in (UNKNOWN_EXCHANGE_RATE, DENOMINATION_UNAVAILABLE_EXCHANGE_RATE):
            return None, True
        nav = vault.fetch_nav()
        return nav * rate, False
    except (Web3Exception, RequestException, ProbablyNodeHasNoBlock, ValueError, RuntimeError, ArithmeticError) as error:
        logger.warning("Cannot verify current USD TVL for %s: %s", vault.address, error)
        return None, False


def fetch_batched_tvl_probes(vaults: list[VaultBase], web3factory: MultiProviderWeb3Factory, block_identifier: int, max_workers: int, *, batch_size: int = DEFAULT_MULTICALL_BATCH_SIZE) -> Iterator[tuple[VaultBase, Decimal | None, bool]]:
    """Batch canonical total assets and preserve specialised NAV conversions.

    Method-identity checks keep adapters with overridden NAV/assets paths on
    individual reads. Failed subcalls produce explicit unavailable outcomes.
    Returned values carry no synthetic source timestamp.

    The all-chain price selector uses these observations to admit low-activity
    candidates. Admission is provisional: it does not advance historical reader
    progress or replace previously verified qualification. Unavailable reads
    therefore produce deferred candidates while existing readers keep scanning.

    :param vaults: Due, already instantiated admission candidates.
    :param web3factory: Verified-chain factory with physical request accounting.
    :param block_identifier: Numeric source block for ordinary batched reads.
    :param max_workers: Maximum threaded read concurrency.
    :param batch_size:
        Positive maximum encoded subcalls per chunk, default 100. Each ordinary
        vault contributes one totalAssets subcall. The all-chain admission
        selector passes the scanner's configured limit; direct callers can
        override it as well. Existing Multicall chain limits and failure
        handling may further split a chunk. Specialised reads are unaffected.
    :return:
        Iterator of ``(vault, tvl_usd, unsupported_conversion)`` tuples.
        ``tvl_usd`` is a Decimal estimate or None when unavailable.
        ``unsupported_conversion`` is True only when the denomination rate is
        unknown or unavailable; a failed RPC read yields ``(vault, None, False)``.
    :raises ValueError: If batch_size is not positive, when iteration begins.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    # An override is a semantic boundary, even when the vault implements the
    # standard ABI. Such adapters may add offchain or HyperCore NAV components;
    # calling totalAssets() directly would understate their admission TVL.
    ordinary = [vault for vault in vaults if isinstance(vault, ERC4626Vault) and vault.chain_id != 999 and type(vault).fetch_nav is ERC4626Vault.fetch_nav and type(vault).fetch_total_assets is ERC4626Vault.fetch_total_assets]
    ordinary_ids = {id(vault) for vault in ordinary}
    specialised = [vault for vault in vaults if id(vault) not in ordinary_ids]
    rates = {}

    def fetch_conversion(vault: VaultBase) -> tuple[VaultBase, Decimal | None]:
        """Resolve one adapter's USD conversion before batching its assets.

        Admission failures leave this candidate unverified while other candidates
        continue. Programming errors still propagate to the phase boundary.

        :param vault: Ordinary adapter selected for a shared assets batch.
        :return:
            ``(vault, exchange_rate)`` with the adapter's denomination-to-USD
            Decimal rate, including sentinel rates; None means the read failed.
        """
        try:
            reader = vault.get_historical_reader(stateful=True)
            state = reader.reader_state if isinstance(reader.reader_state, VaultReaderState) else VaultReaderState(vault)
            return vault, state.exchange_rate
        except (Web3Exception, RequestException, ProbablyNodeHasNoBlock, ValueError, ArithmeticError) as error:
            logger.warning("Admission conversion unavailable for %s: %s", vault.address, error)
            return vault, None

    # Reuse the historical reader's denomination rules rather than guessing
    # USD parity from token symbols. Resolve them before encoding the batch so
    # unsupported/temporarily unavailable conversions cannot qualify a vault.
    # Threads preserve the factory's shared physical-attempt accounting.
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
            for result in read_multicall_chunked(ordinary[0].chain_id, web3factory, calls, block_identifier=block_identifier, max_workers=max_workers, chunk_size=batch_size, batch_size=batch_size, timestamped_results=False, backend="threading", progress_bar_desc="Batched admission TVL", rpc_request_stats=web3factory.rpc_request_stats):
                vault = by_address[result.call.address.lower()]
                observed.add(vault.address.lower())
                if not result.success or len(result.result) != 32:
                    yield vault, None, False
                else:
                    assets = vault.denomination_token.convert_to_decimals(int.from_bytes(result.result, "big"))
                    yield vault, assets * rates[vault.address.lower()], False
        except (Web3Exception, RequestException, ExtraValueError, ProbablyNodeHasNoBlock, RuntimeError) as error:
            if classify_rpc_scan_failure(error) != "transient" and not is_contract_read_failure(error):
                raise
            logger.warning("Admission batch deferred; preserving unverified candidates: %s", error)
            # Chunked reads can yield successes before a later chunk fails.
            # Emit only unseen candidates as unavailable; duplicating earlier
            # results would overwrite their probe outcomes in the caller.
            yield from ((vault, None, False) for address, vault in by_address.items() if address not in observed)

    def fetch_specialised(vault: VaultBase) -> tuple[VaultBase, Decimal | None, bool]:
        """Attach candidate identity to the shared current-state TVL observation.

        Batch scheduling requires the vault beside the outcome, while direct
        callers only need the value and conversion status. Reuse one read path
        so conversion and failure semantics cannot drift between those callers.

        :param vault: Specialised adapter that owns its NAV read semantics.
        :return: ``(vault, tvl_usd, unsupported_conversion)`` admission outcome.
        """
        return vault, *fetch_current_vault_tvl_usd(vault)

    if specialised:
        yield from Parallel(n_jobs=max_workers, backend="threading", return_as="generator")(delayed(fetch_specialised)(vault) for vault in tqdm(specialised, desc="Specialised admission TVL"))
