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

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.cctp.constants import CHAIN_ID_TO_CCTP_DOMAIN
from eth_defi.cctp.receive import prepare_receive_message
from eth_defi.cctp.transfer import prepare_approve_for_burn, prepare_deposit_for_burn
from eth_defi.token import USDC_NATIVE_TOKEN
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)


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

    :param gas:
        Gas limit for transactions.

    :return:
        :class:`CCTPBurnResult` with burn transaction hash and metadata.
    """
    source_chain_id = source_web3.eth.chain_id

    assert source_chain_id in CHAIN_ID_TO_CCTP_DOMAIN, f"Source chain {source_chain_id} is not CCTP-enabled"
    assert dest_chain_id in CHAIN_ID_TO_CCTP_DOMAIN, f"Destination chain {dest_chain_id} is not CCTP-enabled"

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
    tx_hash = moduled_tx.transact({"from": sender, "gas": gas})
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
    burn_tx_hash = moduled_tx.transact({"from": sender, "gas": gas})
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

    :param gas:
        Gas limit for the receive transaction.

    :return:
        Transaction hash of the receive as hex string.
    """
    dest_chain_id = dest_web3.eth.chain_id

    receive_fn = prepare_receive_message(dest_web3, message, attestation)
    relayer = dest_web3.eth.accounts[0] if dest_web3.eth.accounts else sender
    receive_tx_hash = receive_fn.transact({"from": relayer, "gas": gas})
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
    simulate: bool = False,
    test_attesters: dict[int, LocalAccount] | None = None,
    attestation_timeout: float = 1200.0,
    gas: int = 1_000_000,
    max_workers: int | None = None,
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

    :param source_web3:
        Web3 connected to the source chain (e.g. Arbitrum).

    :param source_vault:
        Lagoon vault on the source chain.

    :param destinations:
        List of :class:`CCTPBridgeDestination` for each target chain.

    :param sender:
        Address of the asset manager.

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

    # --- Phase 1: Burns (sequential on source chain) ---
    logger.info("Phase 1: Burning USDC for %d destinations...", n_dest)
    burn_results: list[CCTPBurnResult] = []
    for dest in destinations:
        dest_chain_id = dest.dest_web3.eth.chain_id
        burn_result = burn_usdc_cctp(
            source_web3=source_web3,
            source_vault=source_vault,
            dest_chain_id=dest_chain_id,
            dest_safe_address=dest.dest_safe_address,
            amount=dest.amount,
            sender=sender,
            gas=gas,
        )
        burn_results.append(burn_result)

    logger.info("Phase 1 complete: %d burns confirmed", len(burn_results))

    # --- Phase 2: Attestations (parallel) ---
    logger.info("Phase 2: Obtaining attestations for %d burns...", n_dest)
    attestation_data: list[tuple[bytes, bytes]] = []

    if simulate:
        # Simulation mode: forge attestations in parallel (instant)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for idx, burn_result in enumerate(burn_results):
                dest_chain_id = burn_result.dest_chain_id
                attester = test_attesters[dest_chain_id]
                future = executor.submit(
                    _get_attestation,
                    burn_result=burn_result,
                    simulate=True,
                    test_attester=attester,
                    attestation_timeout=attestation_timeout,
                )
                futures[future] = idx

            indexed_results: dict[int, tuple[bytes, bytes]] = {}
            for future in as_completed(futures):
                idx = futures[future]
                indexed_results[idx] = future.result()

            attestation_data = [indexed_results[i] for i in range(n_dest)]
    else:
        # Production mode: poll Iris API in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for idx, burn_result in enumerate(burn_results):
                future = executor.submit(
                    _get_attestation,
                    burn_result=burn_result,
                    simulate=False,
                    test_attester=None,
                    attestation_timeout=attestation_timeout,
                )
                futures[future] = idx

            indexed_results: dict[int, tuple[bytes, bytes]] = {}
            for future in as_completed(futures):
                idx = futures[future]
                indexed_results[idx] = future.result()

            attestation_data = [indexed_results[i] for i in range(n_dest)]

    logger.info("Phase 2 complete: %d attestations obtained", len(attestation_data))

    # --- Phase 3: Receives (parallel across destination chains) ---
    logger.info("Phase 3: Receiving on %d destination chains...", n_dest)
    receive_tx_hashes: list[str] = [""] * n_dest

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for idx, (dest, (message, attestation)) in enumerate(zip(destinations, attestation_data)):
            future = executor.submit(
                receive_usdc_cctp,
                dest_web3=dest.dest_web3,
                message=message,
                attestation=attestation,
                sender=sender,
                gas=gas,
            )
            futures[future] = idx

        for future in as_completed(futures):
            idx = futures[future]
            receive_tx_hashes[idx] = future.result()

    logger.info("Phase 3 complete: %d receives confirmed", n_dest)

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
    nonce_base: int = 999_999_000,
) -> tuple[bytes, bytes]:
    """Obtain attestation for a burn, either forged or from Iris API.

    :return:
        Tuple of (message_bytes, attestation_bytes).
    """
    source_chain_id = burn_result.source_chain_id
    dest_chain_id = burn_result.dest_chain_id
    source_domain = CHAIN_ID_TO_CCTP_DOMAIN[source_chain_id]
    dest_domain = CHAIN_ID_TO_CCTP_DOMAIN[dest_chain_id]

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

        cctp_attestation = fetch_attestation(
            source_domain=source_domain,
            transaction_hash=burn_result.burn_tx_hash,
            timeout=attestation_timeout,
        )
        logger.info("Attestation received from Iris API for chain %d", dest_chain_id)
        return cctp_attestation.message, cctp_attestation.attestation
