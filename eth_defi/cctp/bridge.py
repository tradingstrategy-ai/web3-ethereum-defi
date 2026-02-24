"""CCTP V2 bridge helpers for Lagoon vaults.

High-level functions to bridge USDC from a Lagoon vault's Safe on one chain
to Safe addresses on other chains via Circle's Cross-Chain Transfer Protocol V2.

Provides both sequential (single-destination) and parallel (multi-destination)
bridging. The parallel flow is structured in three phases:

1. **Burn phase** — approve + ``depositForBurn()`` for each destination (sequential
   on the source chain for nonce ordering)
2. **Attestation phase** — poll Circle's Iris API or forge attestations
   (parallel across destinations)
3. **Receive phase** — ``receiveMessage()`` on each destination chain
   (parallel across chains)

Supports both simulation (Anvil fork with forged attestations) and
production (polling Circle's Iris attestation API) modes.

Example (single bridge, simulation)::

    from eth_defi.cctp.bridge import bridge_usdc_cctp
    from eth_defi.cctp.testing import replace_attester_on_fork

    test_attester = replace_attester_on_fork(web3_destination)
    result = bridge_usdc_cctp(
        source_web3=web3_arbitrum,
        dest_web3=web3_base,
        source_vault=arb_vault,
        dest_safe_address=base_safe_address,
        amount=1_000_000,  # 1 USDC
        sender=deployer.address,
        simulate=True,
        test_attester=test_attester,
    )

Example (parallel bridges to 4 chains)::

    from eth_defi.cctp.bridge import CCTPBridgeDestination, bridge_usdc_cctp_parallel
    from eth_defi.cctp.testing import replace_attester_on_fork

    # Prepare test attesters on each destination fork
    test_attesters = {
        web3_eth.eth.chain_id: replace_attester_on_fork(web3_eth),
        web3_base.eth.chain_id: replace_attester_on_fork(web3_base),
        web3_hyper.eth.chain_id: replace_attester_on_fork(web3_hyper),
        web3_monad.eth.chain_id: replace_attester_on_fork(web3_monad),
    }

    destinations = [
        CCTPBridgeDestination(dest_web3=web3_eth, dest_safe_address=eth_safe, amount=1_000_000),
        CCTPBridgeDestination(dest_web3=web3_base, dest_safe_address=base_safe, amount=1_000_000),
        CCTPBridgeDestination(dest_web3=web3_hyper, dest_safe_address=hyper_safe, amount=1_000_000),
        CCTPBridgeDestination(dest_web3=web3_monad, dest_safe_address=monad_safe, amount=1_000_000),
    ]

    results = bridge_usdc_cctp_parallel(
        source_web3=web3_arbitrum,
        source_vault=arb_vault,
        destinations=destinations,
        sender=deployer.address,
        simulate=True,
        test_attesters=test_attesters,
    )
"""

import enum
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from hexbytes import HexBytes
from tqdm_loggable.auto import tqdm
from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.cctp.receive import prepare_receive_message
from eth_defi.cctp.transfer import _resolve_cctp_domain, prepare_approve_for_burn, prepare_deposit_for_burn
from eth_defi.chain import get_chain_name
from eth_defi.hotwallet import HotWallet
from eth_defi.token import USDC_NATIVE_TOKEN
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)


def _send_contract_tx(
    web3: Web3,
    func: ContractFunction,
    sender: HexAddress,
    hot_wallet: HotWallet | None,
    gas: int,
) -> HexBytes:
    """Send a contract function call, either via HotWallet signing or unlocked account.

    :param hot_wallet:
        When provided, signs and broadcasts via ``eth_sendRawTransaction``.
        When ``None``, uses ``eth_sendTransaction`` (requires unlocked account, e.g. Anvil).
    """
    if hot_wallet is not None:
        signed_tx = hot_wallet.sign_bound_call_with_new_nonce(
            func,
            tx_params={"gas": gas},
            web3=web3,
            fill_gas_price=True,
        )
        raw_bytes = signed_tx.rawTransaction
        return web3.eth.send_raw_transaction(raw_bytes)
    else:
        return func.transact({"from": sender, "gas": gas})


class CCTPBridgePhase(enum.Enum):
    """Phase of a single CCTP bridge transfer.

    Used for progress tracking when block numbers are not available.
    Each transfer progresses through these phases in order.
    """

    #: Approving USDC and calling depositForBurn on the source chain
    burning = "burning"

    #: Waiting for Iris API to index the burn transaction (HTTP 404)
    waiting_for_indexing = "waiting_for_indexing"

    #: Iris API indexed the burn, waiting for block finality confirmations
    pending_confirmations = "pending_confirmations"

    #: Attestation received from Iris API (or forged in simulation)
    attested = "attested"

    #: Calling receiveMessage on the destination chain
    receiving = "receiving"

    #: USDC successfully minted on the destination chain
    complete = "complete"


#: Type for progress callbacks: (transfer_index, phase, dest_chain_id)
CCTPProgressCallback = Callable[[int, CCTPBridgePhase, int], None]


@dataclass(slots=True)
class CCTPBridgeResult:
    """Result of a CCTP bridge operation."""

    #: Transaction hash of the burn on the source chain
    burn_tx_hash: str

    #: Transaction hash of the receive on the destination chain
    receive_tx_hash: str

    #: Amount bridged in raw USDC units (6 decimals)
    amount: int

    #: Source chain ID
    source_chain_id: int

    #: Destination chain ID
    dest_chain_id: int


@dataclass(slots=True)
class CCTPBurnResult:
    """Result of the burn phase of a CCTP bridge.

    Returned by :func:`burn_usdc_cctp`. Contains everything needed
    to proceed with attestation and receive phases.
    """

    #: Transaction hash of the burn on the source chain
    burn_tx_hash: str

    #: Amount burned in raw USDC units (6 decimals)
    amount: int

    #: Source EVM chain ID
    source_chain_id: int

    #: Destination EVM chain ID
    dest_chain_id: int

    #: Safe address on the destination chain where USDC will be minted
    dest_safe_address: str


@dataclass(slots=True)
class CCTPBridgeDestination:
    """Destination configuration for parallel CCTP bridges.

    Used with :func:`bridge_usdc_cctp_parallel` to specify
    where USDC should be bridged to.
    """

    #: Web3 connected to the destination chain
    dest_web3: Web3

    #: Safe address on the destination chain
    dest_safe_address: HexAddress

    #: Amount in raw USDC units (6 decimals)
    amount: int


def burn_usdc_cctp(
    *,
    source_web3: Web3,
    source_vault,
    dest_chain_id: int,
    dest_safe_address: HexAddress,
    amount: int,
    sender: HexAddress,
    hot_wallet: HotWallet | None = None,
    gas: int = 1_000_000,
) -> CCTPBurnResult:
    """Execute the approve + burn phase of a CCTP bridge.

    Approves USDC to TokenMessengerV2 and calls ``depositForBurn()``
    through the vault's TradingStrategyModuleV0 guard.

    This is the source-chain portion of a CCTP bridge. After burning,
    proceed with attestation (simulation or Iris API) and then
    :func:`receive_usdc_cctp` on the destination chain.

    :param source_web3:
        Web3 connected to the source chain.

    :param source_vault:
        Lagoon vault on the source chain.

    :param dest_chain_id:
        EVM chain ID of the destination chain.

    :param dest_safe_address:
        Safe address on the destination chain.

    :param amount:
        Amount in raw USDC units (6 decimals).

    :param sender:
        Address of the asset manager.

    :param hot_wallet:
        When provided, signs transactions locally via ``eth_sendRawTransaction``.
        When ``None``, uses ``eth_sendTransaction`` (requires unlocked account, e.g. Anvil).

    :param gas:
        Gas limit for transactions.

    :return:
        :class:`CCTPBurnResult` with burn transaction hash and metadata.
    """
    source_chain_id = source_web3.eth.chain_id

    assert _resolve_cctp_domain(source_chain_id) is not None, f"Source chain {source_chain_id} is not CCTP-enabled"
    assert _resolve_cctp_domain(dest_chain_id) is not None, f"Destination chain {dest_chain_id} is not CCTP-enabled"

    logger.info(
        "Burning %d raw USDC on chain %d for chain %d (safe %s)",
        amount,
        source_chain_id,
        dest_chain_id,
        dest_safe_address,
    )

    # Step 1: Approve USDC to TokenMessengerV2
    approve_fn = prepare_approve_for_burn(source_web3, amount)
    moduled_tx = source_vault.transact_via_trading_strategy_module(approve_fn)
    tx_hash = _send_contract_tx(source_web3, moduled_tx, sender, hot_wallet, gas)
    assert_transaction_success_with_explanation(source_web3, tx_hash)
    logger.info("USDC approval for CCTP burn confirmed: %s", tx_hash.hex())

    # Step 2: Burn USDC via depositForBurn
    burn_fn = prepare_deposit_for_burn(
        source_web3,
        amount=amount,
        destination_chain_id=dest_chain_id,
        mint_recipient=dest_safe_address,
    )
    moduled_tx = source_vault.transact_via_trading_strategy_module(burn_fn)
    burn_tx_hash = _send_contract_tx(source_web3, moduled_tx, sender, hot_wallet, gas)
    assert_transaction_success_with_explanation(source_web3, burn_tx_hash)
    logger.info("CCTP burn confirmed: %s", burn_tx_hash.hex())

    return CCTPBurnResult(
        burn_tx_hash=burn_tx_hash.hex(),
        amount=amount,
        source_chain_id=source_chain_id,
        dest_chain_id=dest_chain_id,
        dest_safe_address=dest_safe_address,
    )


def receive_usdc_cctp(
    *,
    dest_web3: Web3,
    message: bytes,
    attestation: bytes,
    sender: HexAddress,
    hot_wallet: HotWallet | None = None,
    gas: int = 1_000_000,
) -> str:
    """Execute the receive phase of a CCTP bridge on the destination chain.

    Calls ``receiveMessage()`` on the destination chain's
    MessageTransmitterV2. Anyone can relay — no special permissions required.

    :param dest_web3:
        Web3 connected to the destination chain.

    :param message:
        CCTP message bytes (from attestation or :func:`~eth_defi.cctp.testing.craft_cctp_message`).

    :param attestation:
        Signed attestation bytes.

    :param sender:
        Relayer address (can be any funded account).

    :param hot_wallet:
        When provided, signs transactions locally via ``eth_sendRawTransaction``.
        When ``None``, uses ``eth_sendTransaction`` (requires unlocked account, e.g. Anvil).

    :param gas:
        Gas limit for the receive transaction.

    :return:
        Transaction hash of the receive as hex string.
    """
    dest_chain_id = dest_web3.eth.chain_id

    receive_fn = prepare_receive_message(dest_web3, message, attestation)
    receive_tx_hash = _send_contract_tx(dest_web3, receive_fn, sender, hot_wallet, gas)
    assert_transaction_success_with_explanation(dest_web3, receive_tx_hash)
    logger.info("CCTP receive confirmed on chain %d: %s", dest_chain_id, receive_tx_hash.hex())

    return receive_tx_hash.hex()


def bridge_usdc_cctp(
    *,
    source_web3: Web3,
    dest_web3: Web3,
    source_vault,
    dest_safe_address: HexAddress,
    amount: int,
    sender: HexAddress,
    hot_wallet: HotWallet | None = None,
    simulate: bool = False,
    test_attester: LocalAccount | None = None,
    attestation_timeout: float = 300.0,
    gas: int = 1_000_000,
) -> CCTPBridgeResult:
    """Bridge USDC from a Lagoon vault Safe to a destination Safe via CCTP.

    Performs the full approve -> burn -> attest -> receive flow sequentially
    for a single destination. For bridging to multiple destinations, use
    :func:`bridge_usdc_cctp_parallel` instead.

    :param source_web3:
        Web3 connected to the source chain.

    :param dest_web3:
        Web3 connected to the destination chain.

    :param source_vault:
        :class:`LagoonVault` on the source chain. USDC is burned from its Safe
        via ``transact_via_trading_strategy_module()``.

    :param dest_safe_address:
        Safe address on the destination chain where USDC will be minted.

    :param amount:
        Amount in raw USDC units (6 decimals). E.g. ``1_000_000`` for 1 USDC.

    :param sender:
        Address of the asset manager that executes trades via the module.

    :param hot_wallet:
        When provided, signs transactions locally via ``eth_sendRawTransaction``.
        When ``None``, uses ``eth_sendTransaction`` (requires unlocked account, e.g. Anvil).

    :param simulate:
        If True, use forged attestations on Anvil forks instead of
        polling Circle's Iris API. Requires ``test_attester``.

    :param test_attester:
        Test attester account from :func:`~eth_defi.cctp.testing.replace_attester_on_fork`.
        Required when ``simulate=True``.

    :param attestation_timeout:
        Maximum seconds to wait for Iris API attestation (production mode only).

    :param gas:
        Gas limit for transactions.

    :return:
        :class:`CCTPBridgeResult` with transaction hashes and amount.
    """
    dest_chain_id = dest_web3.eth.chain_id

    if simulate:
        assert test_attester is not None, "test_attester is required in simulate mode"

    # Phase 1: Burn
    burn_result = burn_usdc_cctp(
        source_web3=source_web3,
        source_vault=source_vault,
        dest_chain_id=dest_chain_id,
        dest_safe_address=dest_safe_address,
        amount=amount,
        sender=sender,
        hot_wallet=hot_wallet,
        gas=gas,
    )

    # Phase 2: Attestation
    message, attestation = _get_attestation(
        burn_result=burn_result,
        simulate=simulate,
        test_attester=test_attester,
        attestation_timeout=attestation_timeout,
    )

    # Phase 3: Receive
    receive_tx_hash = receive_usdc_cctp(
        dest_web3=dest_web3,
        message=message,
        attestation=attestation,
        sender=sender,
        hot_wallet=hot_wallet,
        gas=gas,
    )

    return CCTPBridgeResult(
        burn_tx_hash=burn_result.burn_tx_hash,
        receive_tx_hash=receive_tx_hash,
        amount=amount,
        source_chain_id=burn_result.source_chain_id,
        dest_chain_id=dest_chain_id,
    )


def bridge_usdc_cctp_parallel(
    *,
    source_web3: Web3,
    source_vault,
    destinations: list[CCTPBridgeDestination],
    sender: HexAddress,
    hot_wallet: HotWallet | None = None,
    simulate: bool = False,
    test_attesters: dict[int, LocalAccount] | None = None,
    attestation_timeout: float = 2400.0,
    gas: int = 1_000_000,
    max_workers: int | None = None,
    progress: bool = True,
) -> list[CCTPBridgeResult]:
    """Bridge USDC from a Lagoon vault to multiple destination chains in parallel.

    Executes the CCTP bridge flow in three phases to minimise wall-clock time:

    1. **Burns** (sequential) — approve + ``depositForBurn()`` for each destination.
       Sequential because all burns happen on the same source chain and need
       ordered nonce management.

    2. **Attestations** (parallel) — poll Iris API or forge attestations for
       all burns simultaneously. In production mode this is the bottleneck
       (~15-19 minutes per transfer); parallelism reduces total wait from
       ``N * 15min`` to ``~15min``.

    3. **Receives** (parallel) — ``receiveMessage()`` on each destination chain
       simultaneously. Each chain is independent.

    When *progress* is ``True``, shows a ``tqdm`` progress bar tracking each
    transfer through :class:`CCTPBridgePhase` stages.  Since block-level
    progress is not available from the Iris API, the bar advances by phase
    transitions instead.

    :param source_web3:
        Web3 connected to the source chain (e.g. Arbitrum).

    :param source_vault:
        Lagoon vault on the source chain.

    :param destinations:
        List of :class:`CCTPBridgeDestination` for each target chain.

    :param sender:
        Address of the asset manager.

    :param hot_wallet:
        When provided, signs transactions locally via ``eth_sendRawTransaction``.
        When ``None``, uses ``eth_sendTransaction`` (requires unlocked account, e.g. Anvil).

        For the receive phase on multiple destination chains, a separate
        :class:`HotWallet` is created per thread from the same underlying account
        with per-chain nonce management.

    :param simulate:
        If True, use forged attestations. Requires ``test_attesters``.

    :param test_attesters:
        Mapping of destination chain ID to test attester account.
        Required when ``simulate=True``. Create via
        :func:`~eth_defi.cctp.testing.replace_attester_on_fork`.

    :param attestation_timeout:
        Maximum seconds to wait for Iris API attestations. Default 20 minutes.

    :param gas:
        Gas limit for transactions.

    :param max_workers:
        Maximum parallel threads for attestation polling and receives.
        Defaults to number of destinations.

    :param progress:
        Show a ``tqdm`` progress bar tracking per-transfer phase transitions.

    :return:
        List of :class:`CCTPBridgeResult` in the same order as ``destinations``.
    """
    if not destinations:
        return []

    if simulate:
        assert test_attesters is not None, "test_attesters is required in simulate mode"

    if max_workers is None:
        max_workers = len(destinations)

    n_dest = len(destinations)
    total_amount = sum(d.amount for d in destinations)
    logger.info(
        "Parallel CCTP bridge: %d destinations, %d total raw USDC",
        n_dest,
        total_amount,
    )

    # Resolve destination chain names for progress bar descriptions
    dest_chain_ids = [d.dest_web3.eth.chain_id for d in destinations]
    dest_names = [get_chain_name(cid) for cid in dest_chain_ids]

    # Track per-transfer phase for progress bar description.
    # Each transfer goes through len(CCTPBridgePhase) phases = 6 steps.
    n_phases = len(CCTPBridgePhase)
    transfer_phases: list[CCTPBridgePhase] = [CCTPBridgePhase.burning] * n_dest
    #: Per-transfer poll attempt count (updated by attestation callback).
    poll_counts: list[int] = [0] * n_dest
    lock = threading.Lock()

    progress_bar = tqdm(
        total=n_dest * n_phases,
        desc="CCTP bridge",
        unit="phase",
        disable=not progress,
    )

    def _update_phase(idx: int, phase: CCTPBridgePhase, attempt: int = 0):
        """Thread-safe progress bar update."""
        with lock:
            if attempt > 0:
                poll_counts[idx] = attempt
            old_phase = transfer_phases[idx]
            if phase.value == old_phase.value:
                progress_bar.set_postfix_str(f"polls: {sum(poll_counts)}")
                progress_bar.refresh()
                return
            transfer_phases[idx] = phase
            # Advance by the number of phases skipped
            old_ord = list(CCTPBridgePhase).index(old_phase)
            new_ord = list(CCTPBridgePhase).index(phase)
            advance = max(0, new_ord - old_ord)
            if advance > 0:
                progress_bar.update(advance)
            # Build description showing per-transfer status
            parts = [f"{dest_names[i]}:{transfer_phases[i].value}" for i in range(n_dest)]
            progress_bar.set_description(f"CCTP [{', '.join(parts)}]")
            progress_bar.set_postfix_str(f"polls: {sum(poll_counts)}")

    # --- Phase 1: Burns (sequential on source chain) ---
    burn_results: list[CCTPBurnResult] = []
    for idx, dest in enumerate(destinations):
        _update_phase(idx, CCTPBridgePhase.burning)
        burn_result = burn_usdc_cctp(
            source_web3=source_web3,
            source_vault=source_vault,
            dest_chain_id=dest_chain_ids[idx],
            dest_safe_address=dest.dest_safe_address,
            amount=dest.amount,
            sender=sender,
            hot_wallet=hot_wallet,
            gas=gas,
        )
        burn_results.append(burn_result)

    # --- Phase 2: Attestations ---
    attestation_data: list[tuple[bytes, bytes]] = []

    if simulate:
        # Simulation mode: forge attestations sequentially (instant, no threading needed)
        for idx, burn_result in enumerate(burn_results):
            attester = test_attesters[dest_chain_ids[idx]]
            _update_phase(idx, CCTPBridgePhase.attested)
            attestation_data.append(
                _get_attestation(
                    burn_result=burn_result,
                    simulate=True,
                    test_attester=attester,
                    attestation_timeout=attestation_timeout,
                )
            )
    else:
        # Production mode: poll Iris API in parallel (~15-19 min per transfer)
        def _attest_with_progress(idx: int, burn_result: CCTPBurnResult) -> tuple[bytes, bytes]:
            threading.current_thread().name = f"cctp-attest-{dest_names[idx]}"

            def on_phase(iris_status: str, attempt: int = 0):
                phase_map = {
                    "waiting_for_indexing": CCTPBridgePhase.waiting_for_indexing,
                    "pending_confirmations": CCTPBridgePhase.pending_confirmations,
                    "complete": CCTPBridgePhase.attested,
                }
                phase = phase_map.get(iris_status)
                if phase:
                    _update_phase(idx, phase, attempt=attempt)

            return _get_attestation(
                burn_result=burn_result,
                simulate=False,
                test_attester=None,
                attestation_timeout=attestation_timeout,
                on_phase_change=on_phase,
            )

        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="cctp-attest") as executor:
            futures = {}
            for idx, burn_result in enumerate(burn_results):
                _update_phase(idx, CCTPBridgePhase.waiting_for_indexing)
                future = executor.submit(_attest_with_progress, idx, burn_result)
                futures[future] = idx

            indexed_results: dict[int, tuple[bytes, bytes]] = {}
            for future in as_completed(futures):
                idx = futures[future]
                indexed_results[idx] = future.result()

            attestation_data = [indexed_results[i] for i in range(n_dest)]

    # --- Phase 3: Receives ---
    receive_tx_hashes: list[str] = [""] * n_dest

    # Build per-destination-chain HotWallets for the receive phase.
    # Each destination chain needs its own nonce management.
    dest_wallets: dict[int, HotWallet | None] = {}
    if hot_wallet is not None:
        for cid, dest in zip(dest_chain_ids, destinations):
            if cid not in dest_wallets:
                w = HotWallet(hot_wallet.account)
                w.sync_nonce(dest.dest_web3)
                dest_wallets[cid] = w
    else:
        for cid in dest_chain_ids:
            dest_wallets[cid] = None

    if simulate:
        # Simulation mode: receive sequentially (local Anvil forks, no threading needed)
        for idx, (dest, (message, attestation)) in enumerate(zip(destinations, attestation_data)):
            _update_phase(idx, CCTPBridgePhase.receiving)
            receive_tx_hashes[idx] = receive_usdc_cctp(
                dest_web3=dest.dest_web3,
                message=message,
                attestation=attestation,
                sender=sender,
                gas=gas,
            )
            _update_phase(idx, CCTPBridgePhase.complete)
    else:
        # Production mode: receive in parallel across independent destination chains
        def _receive_with_progress(idx: int, dest, message, attestation) -> str:
            threading.current_thread().name = f"cctp-receive-{dest_names[idx]}"
            _update_phase(idx, CCTPBridgePhase.receiving)
            tx_hash = receive_usdc_cctp(
                dest_web3=dest.dest_web3,
                message=message,
                attestation=attestation,
                sender=sender,
                hot_wallet=dest_wallets[dest_chain_ids[idx]],
                gas=gas,
            )
            _update_phase(idx, CCTPBridgePhase.complete)
            return tx_hash

        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="cctp-receive") as executor:
            futures = {}
            for idx, (dest, (message, attestation)) in enumerate(zip(destinations, attestation_data)):
                future = executor.submit(_receive_with_progress, idx, dest, message, attestation)
                futures[future] = idx

            for future in as_completed(futures):
                idx = futures[future]
                receive_tx_hashes[idx] = future.result()

    progress_bar.close()

    # Build results in input order
    results = []
    for idx, (burn_result, dest) in enumerate(zip(burn_results, destinations)):
        results.append(
            CCTPBridgeResult(
                burn_tx_hash=burn_result.burn_tx_hash,
                receive_tx_hash=receive_tx_hashes[idx],
                amount=burn_result.amount,
                source_chain_id=burn_result.source_chain_id,
                dest_chain_id=burn_result.dest_chain_id,
            )
        )

    return results


def _get_attestation(
    *,
    burn_result: CCTPBurnResult,
    simulate: bool,
    test_attester: LocalAccount | None,
    attestation_timeout: float,
    on_phase_change: Callable[[str], None] | None = None,
    nonce_base: int = 999_999_000,
) -> tuple[bytes, bytes]:
    """Obtain attestation for a burn, either forged or from Iris API.

    :param on_phase_change:
        Callback for Iris API status transitions (production mode only).
        Passed through to :func:`~eth_defi.cctp.attestation.fetch_attestation`.

    :return:
        Tuple of (message_bytes, attestation_bytes).
    """
    source_chain_id = burn_result.source_chain_id
    dest_chain_id = burn_result.dest_chain_id
    source_domain = _resolve_cctp_domain(source_chain_id)
    dest_domain = _resolve_cctp_domain(dest_chain_id)

    if simulate:
        from eth_defi.cctp.testing import craft_cctp_message, forge_attestation

        # Use dest_domain as nonce offset to avoid collisions across destinations
        nonce = nonce_base + dest_domain

        message = craft_cctp_message(
            source_domain=source_domain,
            destination_domain=dest_domain,
            nonce=nonce,
            mint_recipient=burn_result.dest_safe_address,
            amount=burn_result.amount,
            burn_token=USDC_NATIVE_TOKEN[source_chain_id],
        )
        attestation = forge_attestation(message, test_attester)
        logger.info("Forged attestation for chain %d (nonce=%d)", dest_chain_id, nonce)
        return message, attestation
    else:
        from eth_defi.cctp.attestation import fetch_attestation
        from eth_defi.cctp.constants import IRIS_API_BASE_URL, IRIS_API_SANDBOX_URL, TESTNET_CHAIN_IDS

        # Use sandbox Iris API for testnet chains
        api_url = IRIS_API_SANDBOX_URL if source_chain_id in TESTNET_CHAIN_IDS else IRIS_API_BASE_URL

        cctp_attestation = fetch_attestation(
            source_domain=source_domain,
            transaction_hash=burn_result.burn_tx_hash,
            timeout=attestation_timeout,
            api_base_url=api_url,
            on_phase_change=on_phase_change,
        )
        logger.info("Attestation received from Iris API for chain %d", dest_chain_id)
        return cctp_attestation.message, cctp_attestation.attestation
