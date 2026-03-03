"""Decode cross-chain Lagoon + Safe + TradingStrategyModuleV0 guard configuration from on-chain events.

Scans all GuardV0Base and library configuration events emitted by the
TradingStrategyModuleV0 contract, optionally following CCTP destination
chains to build a full multichain picture.

Two main entry points:

- :func:`fetch_guard_config_events` — raw decoded events per chain
- :func:`build_multichain_guard_config` — structured dataclass output

Example (production with Hypersync)::

    import hypersync
    from eth_defi.erc_4626.vault_protocol.lagoon.config_event_scanner import (
        fetch_guard_config_events,
        build_multichain_guard_config,
    )

    client = hypersync.HypersyncClient(hypersync.ClientConfig(url=url))
    events, modules = fetch_guard_config_events(
        safe_address="0x...",
        web3=web3,
        hypersync_client=client,
    )
    config = build_multichain_guard_config(events, safe_address, modules)
    print(config.format_human_readable())

Example (testing on Anvil forks — no Hypersync)::

    events, modules = fetch_guard_config_events(
        safe_address="0x...",
        web3=web3_ethereum,
        chain_web3={1: web3_ethereum, 42161: web3_arbitrum},
    )
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

import eth_abi
from eth_typing import HexAddress
from safe_eth.safe import Safe
from tabulate import tabulate
from web3 import Web3
from web3.exceptions import ContractLogicError

from eth_defi.abi import get_abi_by_filename
from eth_defi.cctp.constants import CCTP_DOMAIN_NAMES, CCTP_DOMAIN_TO_CHAIN_ID, TESTNET_CCTP_DOMAIN_TO_CHAIN_ID, TESTNET_CHAIN_IDS
from eth_defi.chain import get_chain_name
from eth_defi.event_reader.filter import Filter
from eth_defi.event_reader.multicall_batcher import EncodedCall
from eth_defi.event_reader.reader import read_events
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.safe.safe_compat import create_safe_ethereum_client
from eth_defi.token import TokenDiskCache, fetch_erc20_details

try:
    import hypersync
    from hypersync import BlockField, LogField
except ImportError:
    hypersync = None


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ABI files whose events we need (module ABI + linked library ABIs)
# ---------------------------------------------------------------------------

#: ABI files containing all guard configuration events.
#: Library events (CowSwap, GMX, Velora, Hypercore) are emitted via
#: delegatecall so they appear at the module address, but their topic0
#: signatures live in the library ABIs.
GUARD_EVENT_ABI_FILES: tuple[str, ...] = (
    "safe-integration/TradingStrategyModuleV0.json",
    "guard/CowSwapLib.json",
    "guard/GmxLib.json",
    "guard/VeloraLib.json",
    "guard/HypercoreVaultLib.json",
)

#: Configuration events we care about.  Events not in this set
#: (e.g. VeloraSwapExecuted, OrderSigned, OwnershipTransferred) are
#: operational, not configuration, and are skipped during scanning.
GUARD_CONFIG_EVENT_NAMES: frozenset[str] = frozenset(
    {
        # GuardV0Base core
        "CallSiteApproved",
        "CallSiteRemoved",
        "SenderApproved",
        "SenderRemoved",
        "ReceiverApproved",
        "ReceiverRemoved",
        "WithdrawDestinationApproved",
        "WithdrawDestinationRemoved",
        "ApprovalDestinationApproved",
        "ApprovalDestinationRemoved",
        "DelegationApprovalDestinationApproved",
        "DelegationApprovalDestinationRemoved",
        "AssetApproved",
        "AssetRemoved",
        "AnyAssetSet",
        "AnyVaultSet",
        "LagoonVaultApproved",
        "ERC4626Approved",
        "CCTPMessengerApproved",
        "CCTPDestinationApproved",
        "CCTPDestinationRemoved",
        # CowSwapLib
        "CowSwapApproved",
        # GmxLib
        "GMXRouterApproved",
        "GMXMarketApproved",
        "GMXMarketRemoved",
        # VeloraLib
        "VeloraSwapperApproved",
        # HypercoreVaultLib
        "CoreWriterApproved",
        "CoreDepositWalletApproved",
        "HypercoreVaultApproved",
        "HypercoreVaultRemoved",
    }
)

#: Mapping from event name → list of (arg_name, fallback_label) pairs.
#: Used by :func:`build_event_based_labels` to extract address→label
#: mappings from decoded guard events when the ``notes`` field is empty.
_EVENT_ADDRESS_PARAMS: dict[str, list[tuple[str, str]]] = {
    "SenderApproved": [("sender", "Trade executor")],
    "ReceiverApproved": [("receiver", "Receiver")],
    "ApprovalDestinationApproved": [("destination", "Approval destination")],
    "WithdrawDestinationApproved": [("destination", "Withdraw destination")],
    "DelegationApprovalDestinationApproved": [("destination", "Delegation destination")],
    "AssetApproved": [("asset", "Asset")],
    "LagoonVaultApproved": [("vault", "Lagoon vault")],
    "ERC4626Approved": [("vault", "ERC-4626 vault")],
    "CCTPMessengerApproved": [("tokenMessenger", "CCTP TokenMessenger")],
    "CowSwapApproved": [("settlementContract", "CowSwap settlement")],
    "VeloraSwapperApproved": [("augustusSwapper", "Velora swapper")],
    "GMXRouterApproved": [
        ("exchangeRouter", "GMX ExchangeRouter"),
        ("syntheticsRouter", "GMX SyntheticsRouter"),
    ],
    "GMXMarketApproved": [("market", "GMX market")],
    "CoreWriterApproved": [("coreWriter", "Hypercore CoreWriter")],
    "CoreDepositWalletApproved": [("wallet", "Hypercore deposit wallet")],
    "HypercoreVaultApproved": [("vault", "Hypercore vault")],
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class DecodedGuardEvent:
    """A single decoded guard configuration event."""

    #: Event name (e.g. ``SenderApproved``, ``CCTPDestinationApproved``)
    event_name: str

    #: Decoded event arguments as a dict
    args: dict

    #: Block number where the event was emitted
    block_number: int

    #: Transaction hash (hex string)
    transaction_hash: str

    #: Log index within the transaction
    log_index: int


@dataclass(slots=True, frozen=True)
class ChainGuardConfig:
    """Structured guard configuration for a single chain.

    Built by processing chronological guard events: ``*Approved`` adds
    to the corresponding set, ``*Removed`` removes from it.
    """

    #: EVM chain ID
    chain_id: int

    #: Human-readable chain name
    chain_name: str

    #: Safe multisig address (same on all chains for a multichain deployment)
    safe_address: HexAddress

    #: TradingStrategyModuleV0 address on this chain
    module_address: HexAddress

    # Core access control
    #: Whitelisted trade-executor hot wallet addresses
    senders: tuple[HexAddress, ...]
    #: Whitelisted token/fund receivers (typically the Safe itself)
    receivers: tuple[HexAddress, ...]

    # Token management
    #: Whitelisted ERC-20 token addresses
    assets: tuple[HexAddress, ...]
    #: When True, any ERC-20 token is allowed (bypasses per-token whitelist)
    any_asset: bool

    # Transfer destinations
    #: Whitelisted approval destinations (routers etc.)
    approval_destinations: tuple[HexAddress, ...]
    #: Whitelisted withdraw destinations
    withdraw_destinations: tuple[HexAddress, ...]
    #: Whitelisted delegation approval destinations
    delegation_approval_destinations: tuple[HexAddress, ...]

    # Protocol integrations
    #: Lagoon vault addresses allowed for settlement
    lagoon_vaults: tuple[HexAddress, ...]
    #: ERC-4626 vault addresses allowed for deposit/redeem
    erc4626_vaults: tuple[HexAddress, ...]
    #: CCTP TokenMessenger addresses
    cctp_messengers: tuple[HexAddress, ...]
    #: CCTP destination domain IDs
    cctp_destinations: tuple[int, ...]
    #: CowSwap settlement contract addresses
    cowswap_settlements: tuple[HexAddress, ...]
    #: Velora Augustus Swapper addresses
    velora_swappers: tuple[HexAddress, ...]
    #: GMX routers as (exchangeRouter, syntheticsRouter) tuples
    gmx_routers: tuple[tuple[HexAddress, HexAddress], ...]
    #: GMX market addresses
    gmx_markets: tuple[HexAddress, ...]
    #: Hypercore CoreWriter addresses
    hypercore_core_writers: tuple[HexAddress, ...]
    #: Hypercore CoreDepositWallet addresses
    hypercore_deposit_wallets: tuple[HexAddress, ...]
    #: Hypercore vault addresses
    hypercore_vaults: tuple[HexAddress, ...]

    # Raw call sites
    #: Whitelisted (target, selector_hex) tuples
    call_sites: tuple[tuple[HexAddress, str], ...]


@dataclass(slots=True, frozen=True)
class MultichainGuardConfig:
    """Structured guard configuration across all chains in a deployment.

    Contains a :class:`ChainGuardConfig` per chain, keyed by chain ID.
    """

    #: Deterministic Safe address shared by all chains
    safe_address: HexAddress

    #: Per-chain guard configuration
    chains: dict[int, ChainGuardConfig]

    def format_human_readable(self) -> str:
        """Render the full multichain configuration as human-readable text."""
        lines: list[str] = []
        lines.append(f"Safe: {self.safe_address}")
        lines.append(f"Chains: {len(self.chains)}")
        lines.append("")

        for chain_id in sorted(self.chains):
            cfg = self.chains[chain_id]
            lines.append(f"=== {cfg.chain_name} (chain {chain_id}) ===")
            lines.append(f"  Module: {cfg.module_address}")
            lines.append("")

            _section(lines, "Senders (trade executors)", cfg.senders)
            _section(lines, "Receivers", cfg.receivers)

            if cfg.any_asset:
                lines.append("  Any asset: enabled")
            _section(lines, "Assets", cfg.assets)

            _section(lines, "Approval destinations", cfg.approval_destinations)
            _section(lines, "Withdraw destinations", cfg.withdraw_destinations)
            _section(lines, "Delegation approval destinations", cfg.delegation_approval_destinations)

            _section(lines, "Lagoon vaults", cfg.lagoon_vaults)
            _section(lines, "ERC-4626 vaults", cfg.erc4626_vaults)

            if cfg.cctp_messengers:
                lines.append("  CCTP messengers:")
                for addr in cfg.cctp_messengers:
                    lines.append(f"    {addr}")
            if cfg.cctp_destinations:
                lines.append("  CCTP destinations:")
                for domain in cfg.cctp_destinations:
                    name = CCTP_DOMAIN_NAMES.get(domain, f"domain {domain}")
                    _dtc = TESTNET_CCTP_DOMAIN_TO_CHAIN_ID if cfg.chain_id in TESTNET_CHAIN_IDS else CCTP_DOMAIN_TO_CHAIN_ID
                    dest_chain_id = _dtc.get(domain)
                    chain_info = f" (chain {dest_chain_id})" if dest_chain_id else ""
                    lines.append(f"    Domain {domain} -> {name}{chain_info}")

            _section(lines, "CowSwap settlements", cfg.cowswap_settlements)
            _section(lines, "Velora swappers", cfg.velora_swappers)

            if cfg.gmx_routers:
                lines.append("  GMX routers:")
                for exchange, synthetics in cfg.gmx_routers:
                    lines.append(f"    Exchange: {exchange}, Synthetics: {synthetics}")
            _section(lines, "GMX markets", cfg.gmx_markets)

            _section(lines, "Hypercore core writers", cfg.hypercore_core_writers)
            _section(lines, "Hypercore deposit wallets", cfg.hypercore_deposit_wallets)
            _section(lines, "Hypercore vaults", cfg.hypercore_vaults)

            if cfg.call_sites:
                lines.append(f"  Call sites: {len(cfg.call_sites)}")

            lines.append("")

        return "\n".join(lines)


def _section(lines: list[str], title: str, items: tuple) -> None:
    """Append a section only if items is non-empty."""
    if items:
        lines.append(f"  {title}:")
        for item in items:
            lines.append(f"    {item}")


# ---------------------------------------------------------------------------
# Detailed formatting with token resolution
# ---------------------------------------------------------------------------


def resolve_token_label(
    web3: Web3,
    address: HexAddress,
    token_cache: TokenDiskCache | None = None,
) -> str:
    """Resolve an ERC-20 token address to ``SYMBOL (address)`` format.

    Falls back to the raw address if resolution fails.

    :param web3:
        Web3 connection for on-chain lookups.

    :param address:
        ERC-20 token contract address.

    :param token_cache:
        Optional disk cache for token metadata.

    :return:
        Human-readable label like ``USDC (0x...)``.
    """
    try:
        details = fetch_erc20_details(web3, address, cache=token_cache)
        return f"{details.symbol} ({address})"
    except Exception:
        return address


def resolve_address_label(
    web3: Web3 | None,
    address: HexAddress,
    known_labels: dict[str, str] | None = None,
    fallback_labels: dict[str, str] | None = None,
) -> str:
    """Resolve a contract address to a human-readable label.

    Resolution priority:

    1. **known_labels** — explicit overrides (e.g. Safe → ``<our multisig>``)
    2. **name()** call — works for ERC-20 tokens, ERC-4626 vaults, etc.
    3. **fallback_labels** — event-derived labels for contracts without ``name()``
    4. ``<unknown>`` — last resort

    :param web3:
        Web3 connection for on-chain lookups.  If ``None``, only
        label dicts are checked.

    :param address:
        Contract address to resolve.

    :param known_labels:
        Optional ``{checksummed_address: label}`` mapping for addresses
        with pre-assigned labels (e.g. the Safe multisig).  Highest priority.

    :param fallback_labels:
        Optional ``{checksummed_address: label}`` mapping tried after
        ``name()`` fails.  Typically built from guard event ``notes`` fields
        via :func:`build_event_based_labels`.

    :return:
        Human-readable label like ``Morpho Gauntlet USDC (0x...)``,
        ``<our multisig> (0x...)``, or ``<unknown> (0x...)``.
    """
    checksum = Web3.to_checksum_address(address)

    # 1. Check pre-known labels (highest priority)
    if known_labels:
        label = known_labels.get(checksum)
        if label:
            return f"{label} ({checksum})"

    # 2. Try calling name() on the contract
    if web3 is not None:
        try:
            result = web3.eth.call(
                {
                    "to": checksum,
                    "data": Web3.keccak(text="name()")[:4],
                }
            )
            if result and len(result) > 0:
                name = eth_abi.decode(["string"], result)[0]
                if name:
                    return f"{name} ({checksum})"
        except Exception:
            pass

    # 3. Check fallback labels (event-derived, lower priority than name())
    if fallback_labels:
        label = fallback_labels.get(checksum)
        if label:
            return f"{label} ({checksum})"

    return f"<unknown> ({checksum})"


def build_event_based_labels(events: list[DecodedGuardEvent]) -> dict[str, str]:
    """Build address → label mapping from decoded guard configuration events.

    Extracts human-readable labels for contract addresses from the ``notes``
    field included in every guard configuration event.  When ``notes`` is
    empty, falls back to a role-based label derived from the event type
    (e.g. ``CowSwapApproved`` → ``"CowSwap settlement"``).

    This is particularly useful for contracts that do not implement a
    ``name()`` function (protocol routers, settlement contracts, relayers,
    etc.) where :func:`resolve_address_label` would otherwise return
    ``<unknown>``.

    :param events:
        Decoded guard events for a single chain.

    :return:
        ``{checksummed_address: label}`` mapping.
    """
    labels: dict[str, str] = {}

    for event in events:
        params = _EVENT_ADDRESS_PARAMS.get(event.event_name)
        if params is None:
            continue

        notes = event.args.get("notes", "")

        for arg_name, fallback_label in params:
            addr = event.args.get(arg_name)
            if not addr:
                continue
            checksum = Web3.to_checksum_address(addr)
            if checksum in labels:
                continue  # Keep the first label seen (chronological)
            label = notes if notes else fallback_label
            labels[checksum] = label

    return labels


def resolve_hypercore_vault_labels(
    vault_addresses: tuple[HexAddress, ...] | list[HexAddress],
) -> dict[str, str]:
    """Resolve Hypercore vault addresses to human-readable names via the Hyperliquid API.

    Hypercore vaults do not implement ``name()`` on-chain; their names
    are stored off-chain in the Hyperliquid API (``vaultDetails`` endpoint).

    :param vault_addresses:
        Hypercore vault addresses to resolve.

    :return:
        ``{checksummed_address: vault_name}`` mapping.  Addresses that
        cannot be resolved are omitted from the result.
    """
    if not vault_addresses:
        return {}

    from eth_defi.hyperliquid.session import create_hyperliquid_session
    from eth_defi.hyperliquid.vault import HyperliquidVault

    labels: dict[str, str] = {}
    session = create_hyperliquid_session()

    for addr in vault_addresses:
        checksum = Web3.to_checksum_address(addr)
        try:
            vault = HyperliquidVault(session=session, vault_address=checksum)
            info = vault.fetch_info()
            if info.name:
                labels[checksum] = info.name
                logger.info("Resolved Hypercore vault %s → %s", checksum, info.name)
        except Exception as e:
            logger.debug("Cannot resolve Hypercore vault %s: %s", checksum, e)

    return labels


def format_chain_config_detailed(
    cfg: ChainGuardConfig,
    web3: Web3 | None = None,
    token_cache: TokenDiskCache | None = None,
    known_labels: dict[str, str] | None = None,
    events: list[DecodedGuardEvent] | None = None,
    prefix: str = "",
    all_chain_configs: dict[int, ChainGuardConfig] | None = None,
    all_chain_web3: dict[int, Web3] | None = None,
    all_chain_events: dict[int, list[DecodedGuardEvent]] | None = None,
    rendered_chains: set[int] | None = None,
) -> str:
    """Format a single chain's guard configuration as a Unicode tree.

    Renders the chain's sections (senders, assets, vaults, etc.) using
    Unicode box-drawing characters (``\u251c\u2500\u2500``, ``\u2514\u2500\u2500``, ``\u2502``).  CCTP destination
    chains are rendered as nested subtrees under a "CCTP bridges" section,
    each showing their own Safe, module, and full guard configuration.

    When *web3* is provided, ERC-20 addresses are resolved to
    ``SYMBOL (address)`` labels via :func:`resolve_token_label`, and
    contract addresses are resolved via :func:`resolve_address_label`.

    When *events* is provided, :func:`build_event_based_labels` derives
    labels from event ``notes`` fields for contracts lacking ``name()``.

    :param cfg:
        Per-chain guard configuration.

    :param web3:
        Optional Web3 connection for token/contract name resolution.

    :param token_cache:
        Optional disk cache for token metadata.

    :param known_labels:
        Optional ``{checksummed_address: label}`` mapping.  The Safe
        address is automatically labelled ``<our multisig>``.

    :param events:
        Optional decoded guard events for event-based label resolution.

    :param prefix:
        Indentation prefix prepended to every output line (used for
        recursive CCTP nesting).

    :param all_chain_configs:
        Full ``{chain_id: ChainGuardConfig}`` dict for rendering CCTP
        destination chains inline.

    :param all_chain_web3:
        ``{chain_id: Web3}`` for all chains, used for label resolution
        on CCTP destination chains.

    :param all_chain_events:
        ``{chain_id: events}`` for all chains, used for event-based
        labels on CCTP destination chains.

    :param rendered_chains:
        Chain IDs already rendered in the tree.  Used to prevent
        circular nesting in bidirectional CCTP deployments.

    :return:
        Multi-line tree string ready for logging or printing.
    """
    # Track already-rendered chains to avoid circular nesting
    if rendered_chains is None:
        rendered_chains = set()
    rendered_chains = rendered_chains | {cfg.chain_id}

    # Build known labels: Safe as <our multisig> + Hypercore vaults + user overrides
    labels: dict[str, str] = {}
    safe_checksum = Web3.to_checksum_address(cfg.safe_address)
    labels[safe_checksum] = "<our multisig>"
    # Resolve Hypercore vault names via Hyperliquid API (they lack on-chain name())
    if cfg.hypercore_vaults:
        try:
            labels.update(resolve_hypercore_vault_labels(cfg.hypercore_vaults))
        except Exception as e:
            logger.debug("Hypercore vault label resolution failed: %s", e)
    if known_labels:
        labels.update(known_labels)

    # Event-based labels serve as fallback (after name() call)
    event_labels = build_event_based_labels(events) if events else None

    def _label(addr: HexAddress) -> str:
        return resolve_address_label(web3, addr, known_labels=labels, fallback_labels=event_labels)

    # Collect non-empty sections as (title, items) tuples.
    # "items" is a list of display strings for simple sections.
    # For CCTP bridges, items is a list of (title, subtree_lines, dest_cfg) tuples.
    sections: list[tuple[str, list]] = []

    if cfg.senders:
        sections.append(("Senders (trade executors)", list(cfg.senders)))

    if cfg.receivers:
        sections.append(("Receivers", [_label(addr) for addr in cfg.receivers]))

    if cfg.any_asset:
        sections.append(("Any asset", ["ENABLED"]))

    if cfg.assets:
        if web3 is not None:
            items = [resolve_token_label(web3, addr, token_cache) for addr in cfg.assets]
        else:
            items = list(cfg.assets)
        sections.append(("Whitelisted assets", items))

    if cfg.approval_destinations:
        sections.append(("Approval destinations (routers)", [_label(addr) for addr in cfg.approval_destinations]))

    if cfg.withdraw_destinations:
        sections.append(("Withdraw destinations", [_label(addr) for addr in cfg.withdraw_destinations]))

    if cfg.delegation_approval_destinations:
        sections.append(("Delegation approval destinations", [_label(addr) for addr in cfg.delegation_approval_destinations]))

    if cfg.lagoon_vaults:
        sections.append(("Lagoon vaults (settlement)", [_label(addr) for addr in cfg.lagoon_vaults]))

    if cfg.erc4626_vaults:
        sections.append(("ERC-4626 vaults", [_label(addr) for addr in cfg.erc4626_vaults]))

    if cfg.cctp_messengers:
        sections.append(("CCTP messengers", [_label(addr) for addr in cfg.cctp_messengers]))

    # CCTP bridges — render destination chains as nested subtrees
    # Skip chains already rendered to prevent circular nesting
    if cfg.cctp_destinations and all_chain_configs:
        # Use testnet mapping when the source chain is a testnet chain
        domain_to_chain = TESTNET_CCTP_DOMAIN_TO_CHAIN_ID if cfg.chain_id in TESTNET_CHAIN_IDS else CCTP_DOMAIN_TO_CHAIN_ID
        cctp_items: list[tuple] = []
        for domain in cfg.cctp_destinations:
            dest_chain_id = domain_to_chain.get(domain)
            if dest_chain_id and dest_chain_id in rendered_chains:
                continue  # Already rendered — skip to avoid circular nesting
            if dest_chain_id and dest_chain_id in all_chain_configs:
                dest_cfg = all_chain_configs[dest_chain_id]
                dest_name = CCTP_DOMAIN_NAMES.get(domain, f"domain {domain}")
                title = f"{dest_name} (chain {dest_chain_id}) via CCTP domain {domain}"
                cctp_items.append((title, dest_cfg))
            else:
                dest_name = CCTP_DOMAIN_NAMES.get(domain, f"domain {domain}")
                cctp_items.append((f"{dest_name} via CCTP domain {domain} (no config available)", None))
        if cctp_items:
            sections.append(("CCTP bridges", cctp_items))
    elif cfg.cctp_destinations:
        # No all_chain_configs — fall back to simple domain listing
        domain_to_chain_fallback = TESTNET_CCTP_DOMAIN_TO_CHAIN_ID if cfg.chain_id in TESTNET_CHAIN_IDS else CCTP_DOMAIN_TO_CHAIN_ID
        items = []
        for domain in cfg.cctp_destinations:
            dest_name = CCTP_DOMAIN_NAMES.get(domain, f"domain {domain}")
            dest_chain = domain_to_chain_fallback.get(domain)
            chain_info = f"chain {dest_chain}" if dest_chain else "?"
            items.append(f"Domain {domain} \u2192 {dest_name} ({chain_info})")
        sections.append(("CCTP destinations", items))

    if cfg.cowswap_settlements:
        sections.append(("CowSwap settlements", [_label(addr) for addr in cfg.cowswap_settlements]))

    if cfg.velora_swappers:
        sections.append(("Velora (ParaSwap) swappers", [_label(addr) for addr in cfg.velora_swappers]))

    if cfg.gmx_routers:
        items = [f"exchange: {_label(ex)}, synthetics: {_label(syn)}" for ex, syn in cfg.gmx_routers]
        sections.append(("GMX routers", items))

    if cfg.gmx_markets:
        sections.append(("GMX markets", [_label(addr) for addr in cfg.gmx_markets]))

    if cfg.hypercore_core_writers:
        sections.append(("Hypercore core writers", [_label(addr) for addr in cfg.hypercore_core_writers]))

    if cfg.hypercore_deposit_wallets:
        sections.append(("Hypercore deposit wallets", [_label(addr) for addr in cfg.hypercore_deposit_wallets]))

    if cfg.hypercore_vaults:
        sections.append(("Hypercore vaults", [_label(addr) for addr in cfg.hypercore_vaults]))

    if cfg.call_sites:
        sections.append((f"Call sites: {len(cfg.call_sites)} whitelisted", []))

    # Render sections as a Unicode tree
    lines: list[str] = []
    is_cctp_bridge_section = False

    for i, (title, items) in enumerate(sections):
        is_last = i == len(sections) - 1
        branch = "\u2514\u2500\u2500 " if is_last else "\u251c\u2500\u2500 "
        cont = "    " if is_last else "\u2502   "

        # Detect CCTP bridges section (items are tuples not strings)
        is_cctp_bridge_section = title == "CCTP bridges" and items and isinstance(items[0], tuple)

        if is_cctp_bridge_section:
            lines.append(f"{prefix}{branch}{title}")
            for j, cctp_item in enumerate(items):
                is_last_dest = j == len(items) - 1
                db = "\u2514\u2500\u2500 " if is_last_dest else "\u251c\u2500\u2500 "
                dc = "    " if is_last_dest else "\u2502   "
                dest_title = cctp_item[0]
                dest_cfg = cctp_item[1]

                if dest_cfg is None:
                    # No config available — just show the title
                    lines.append(f"{prefix}{cont}{db}{dest_title}")
                    continue

                lines.append(f"{prefix}{cont}{db}{dest_title}")
                dest_prefix = f"{prefix}{cont}{dc}"
                lines.append(f"{dest_prefix}Safe:   {dest_cfg.safe_address}")
                lines.append(f"{dest_prefix}Module: {dest_cfg.module_address}")
                lines.append(f"{dest_prefix}\u2502")
                dest_web3 = all_chain_web3.get(dest_cfg.chain_id) if all_chain_web3 else None
                dest_events = all_chain_events.get(dest_cfg.chain_id, []) if all_chain_events else []
                subtree = format_chain_config_detailed(
                    dest_cfg,
                    web3=dest_web3,
                    token_cache=token_cache,
                    known_labels=known_labels,
                    events=dest_events,
                    prefix=dest_prefix,
                    all_chain_configs=all_chain_configs,
                    all_chain_web3=all_chain_web3,
                    all_chain_events=all_chain_events,
                    rendered_chains=rendered_chains,
                )
                lines.append(subtree)
        elif not items:
            # Leaf section with no children (e.g. "Call sites: N whitelisted")
            lines.append(f"{prefix}{branch}{title}")
        else:
            # Simple section with item list
            lines.append(f"{prefix}{branch}{title}")
            for j, item in enumerate(items):
                is_last_item = j == len(items) - 1
                ib = "\u2514\u2500\u2500 " if is_last_item else "\u251c\u2500\u2500 "
                lines.append(f"{prefix}{cont}{ib}{item}")

    return "\n".join(lines)


def format_event_summary(events: dict[int, list[DecodedGuardEvent]]) -> str:
    """Format event counts per chain as a tabular summary string.

    :param events:
        Raw events per chain from :func:`fetch_guard_config_events`.

    :return:
        Multi-line table string with columns: Chain, Event, Count.
    """
    rows: list[list] = []
    for cid in sorted(events):
        chain_name = get_chain_name(cid)
        event_counts: dict[str, int] = {}
        for e in events[cid]:
            event_counts[e.event_name] = event_counts.get(e.event_name, 0) + 1
        for event_name, count in sorted(event_counts.items()):
            rows.append([chain_name, event_name, count])
    return tabulate(rows, headers=["Chain", "Event", "Count"], tablefmt="plain")


def format_guard_config_report(
    config: MultichainGuardConfig,
    events: dict[int, list[DecodedGuardEvent]],
    chain_web3: dict[int, Web3] | None = None,
    token_cache: TokenDiskCache | None = None,
    known_labels: dict[str, str] | None = None,
) -> str:
    """Format a full multichain guard config report as a Unicode tree.

    Renders the configuration as a hierarchical tree with Unicode
    box-drawing characters.  Root chains (those not reachable as CCTP
    destinations from other chains) appear at the top level.  CCTP
    destination chains are rendered as nested subtrees under their
    source chain's "CCTP bridges" section.

    :param config:
        Structured multichain guard configuration.

    :param events:
        Raw events per chain (for event-based address label resolution).

    :param chain_web3:
        Optional ``{chain_id: Web3}`` for token/contract name resolution.

    :param token_cache:
        Optional disk cache for token metadata.

    :param known_labels:
        Optional ``{checksummed_address: label}`` for pre-known addresses.

    :return:
        Complete multi-line tree report string.
    """
    lines: list[str] = []
    lines.append(f"Guard configuration for Safe {config.safe_address}")
    lines.append("\u2502")

    # Find root chains — chains with a Lagoon vault are always roots (source
    # chains).  In bidirectional CCTP deployments both chains point at each
    # other, so we cannot simply exclude "all CCTP destinations".  Instead,
    # treat Lagoon-vault chains as roots and nest their CCTP destinations
    # under them.  Chains without Lagoon vaults that are also not CCTP
    # destinations of any root chain remain top-level too (standalone).
    lagoon_chains = {cid for cid, cfg in config.chains.items() if cfg.lagoon_vaults}
    root_cctp_dests: set[int] = set()
    for cid in lagoon_chains:
        # Use testnet mapping when the source chain is a testnet chain
        domain_to_chain = TESTNET_CCTP_DOMAIN_TO_CHAIN_ID if cid in TESTNET_CHAIN_IDS else CCTP_DOMAIN_TO_CHAIN_ID
        for domain in config.chains[cid].cctp_destinations:
            dest = domain_to_chain.get(domain)
            if dest:
                root_cctp_dests.add(dest)
    root_chains = [cid for cid in sorted(config.chains) if cid not in root_cctp_dests or cid in lagoon_chains]

    for i, cid in enumerate(root_chains):
        is_last = i == len(root_chains) - 1
        branch = "\u2514\u2500\u2500 " if is_last else "\u251c\u2500\u2500 "
        cont = "    " if is_last else "\u2502   "

        cfg = config.chains[cid]
        web3 = chain_web3.get(cid) if chain_web3 else None
        chain_events = events.get(cid, [])

        lines.append(f"{branch}{cfg.chain_name} (chain {cfg.chain_id})")
        lines.append(f"{cont}Safe:   {cfg.safe_address}")
        lines.append(f"{cont}Module: {cfg.module_address}")
        lines.append(f"{cont}\u2502")
        lines.append(
            format_chain_config_detailed(
                cfg,
                web3=web3,
                token_cache=token_cache,
                known_labels=known_labels,
                events=chain_events,
                prefix=cont,
                all_chain_configs=config.chains,
                all_chain_web3=chain_web3 or {},
                all_chain_events=events,
            )
        )
        if not is_last:
            lines.append("\u2502")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Event ABI helpers
# ---------------------------------------------------------------------------


def _build_event_topic_map() -> dict[str, dict]:
    """Build a mapping from topic0 hex -> event ABI entry.

    Merges events from the module ABI and all linked library ABIs.
    Only includes events in :data:`GUARD_CONFIG_EVENT_NAMES`.

    :return:
        ``{topic0_hex: abi_entry}`` where ``abi_entry`` is the JSON ABI dict.
    """
    topic_map: dict[str, dict] = {}

    for abi_file in GUARD_EVENT_ABI_FILES:
        abi_data = get_abi_by_filename(abi_file)
        # get_abi_by_filename returns the full JSON dict; extract the ABI list
        abi = abi_data.get("abi", abi_data) if isinstance(abi_data, dict) else abi_data
        for entry in abi:
            if entry.get("type") != "event":
                continue
            name = entry["name"]
            if name not in GUARD_CONFIG_EVENT_NAMES:
                continue
            # Compute topic0 = keccak256(EventName(type1,type2,...))
            input_types = ",".join(inp["type"] for inp in entry["inputs"])
            sig = f"{name}({input_types})"
            raw_hash = Web3.keccak(text=sig).hex()
            # HexBytes.hex() omits the "0x" prefix; normalise to "0x..." form
            topic0 = raw_hash if raw_hash.startswith("0x") else "0x" + raw_hash
            topic_map[topic0] = entry

    return topic_map


def _decode_event_from_log(log: dict, topic_map: dict[str, dict]) -> DecodedGuardEvent | None:
    """Decode a single log entry using the topic map.

    :param log:
        A web3 log dict with keys: ``address``, ``topics``, ``data``,
        ``blockNumber``, ``transactionHash``, ``logIndex``.

    :param topic_map:
        topic0 hex -> ABI entry mapping from :func:`_build_event_topic_map`.

    :return:
        Decoded event, or ``None`` if the topic0 is not a guard config event.
    """
    if not log.get("topics"):
        return None

    topic0 = log["topics"][0]
    if isinstance(topic0, bytes):
        topic0 = topic0.hex()
    if not topic0.startswith("0x"):
        topic0 = "0x" + topic0

    abi_entry = topic_map.get(topic0)
    if abi_entry is None:
        return None

    # Separate indexed vs non-indexed inputs
    indexed_inputs = [inp for inp in abi_entry["inputs"] if inp.get("indexed", False)]
    non_indexed_inputs = [inp for inp in abi_entry["inputs"] if not inp.get("indexed", False)]

    args = {}

    # Decode indexed parameters from topics[1:]
    topics = log["topics"]
    for i, inp in enumerate(indexed_inputs):
        if i + 1 >= len(topics):
            break
        topic_val = topics[i + 1]
        if isinstance(topic_val, bytes):
            topic_val = topic_val.hex()
        if isinstance(topic_val, str) and not topic_val.startswith("0x"):
            topic_val = "0x" + topic_val
        args[inp["name"]] = _decode_indexed_value(inp["type"], topic_val)

    # Decode non-indexed parameters from data
    data = log.get("data", "0x")
    if isinstance(data, bytes):
        data = data.hex()
    if isinstance(data, str) and not data.startswith("0x"):
        data = "0x" + data

    if non_indexed_inputs and data and data != "0x":
        types = [inp["type"] for inp in non_indexed_inputs]
        decoded = eth_abi.decode(types, bytes.fromhex(data[2:]))
        for inp, val in zip(non_indexed_inputs, decoded):
            if inp["type"] == "address":
                args[inp["name"]] = Web3.to_checksum_address(val)
            elif inp["type"] == "bytes4":
                args[inp["name"]] = "0x" + val.hex()
            elif inp["type"] == "string":
                args[inp["name"]] = val
            elif inp["type"] == "bool":
                args[inp["name"]] = bool(val)
            elif inp["type"].startswith("uint"):
                args[inp["name"]] = int(val)
            else:
                args[inp["name"]] = val

    tx_hash = log.get("transactionHash", "")
    if isinstance(tx_hash, bytes):
        tx_hash = "0x" + tx_hash.hex()
    elif isinstance(tx_hash, str) and not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash

    block_number = log.get("blockNumber", 0)
    if isinstance(block_number, str):
        block_number = int(block_number, 16) if block_number.startswith("0x") else int(block_number)

    log_index = log.get("logIndex", 0)
    if isinstance(log_index, str):
        log_index = int(log_index, 16) if log_index.startswith("0x") else int(log_index)

    return DecodedGuardEvent(
        event_name=abi_entry["name"],
        args=args,
        block_number=block_number,
        transaction_hash=tx_hash,
        log_index=log_index,
    )


def _decode_indexed_value(type_name: str, topic_hex: str):
    """Decode a single indexed event parameter from its topic hex."""
    raw = bytes.fromhex(topic_hex[2:]) if topic_hex.startswith("0x") else bytes.fromhex(topic_hex)

    if type_name == "address":
        # Address is right-padded in 32 bytes
        return Web3.to_checksum_address("0x" + raw[-20:].hex())
    elif type_name == "bool":
        return int.from_bytes(raw, "big") != 0
    elif type_name.startswith("uint"):
        return int.from_bytes(raw, "big")
    elif type_name.startswith("int"):
        # Signed integer
        val = int.from_bytes(raw, "big")
        bits = int(type_name[3:]) if len(type_name) > 3 else 256
        if val >= (1 << (bits - 1)):
            val -= 1 << bits
        return val
    elif type_name == "bytes4":
        return "0x" + raw[:4].hex()
    elif type_name == "string" or type_name == "bytes":
        # Indexed dynamic types are keccak256 hashes — cannot decode
        return topic_hex
    else:
        return topic_hex


# ---------------------------------------------------------------------------
# Module resolution
# ---------------------------------------------------------------------------


def resolve_trading_strategy_module(
    web3: Web3,
    safe_address: HexAddress,
) -> HexAddress | None:
    """Find the TradingStrategyModuleV0 from a Safe's enabled modules.

    Calls ``Safe.retrieve_modules()`` and probes each module with
    ``getTradingStrategyModuleVersion()`` to identify our guard.

    :param web3:
        Web3 connection to the chain where the Safe is deployed.

    :param safe_address:
        Address of the Gnosis Safe multisig.

    :return:
        Address of the TradingStrategyModuleV0, or ``None`` if not found.
    """
    client = create_safe_ethereum_client(web3)
    safe = Safe(Web3.to_checksum_address(safe_address), client)

    try:
        modules = safe.retrieve_modules()
    except Exception as e:
        logger.warning("Failed to retrieve modules for Safe %s: %s", safe_address, e)
        return None

    logger.info("Safe %s has %d module(s): %s", safe_address, len(modules), modules)

    version_selector = Web3.keccak(text="getTradingStrategyModuleVersion()")[:4]

    for module_address in modules:
        probe_call = EncodedCall.from_keccak_signature(
            function="getTradingStrategyModuleVersion",
            address=Web3.to_checksum_address(module_address),
            signature=version_selector,
            data=b"",
            extra_data={},
        )

        try:
            version_bytes = probe_call.call(web3, block_identifier="latest")
            version = version_bytes.decode("utf-8")
            logger.info(
                "Module %s identified as TradingStrategyModuleV0 %s",
                module_address,
                version,
            )
            return Web3.to_checksum_address(module_address)
        except (ValueError, ContractLogicError, UnicodeDecodeError):
            # Not a TradingStrategyModuleV0
            continue

    logger.warning("No TradingStrategyModuleV0 found on Safe %s", safe_address)
    return None


# ---------------------------------------------------------------------------
# Event reading — Hypersync backend
# ---------------------------------------------------------------------------


async def _fetch_guard_events_hypersync_async(
    client: hypersync.HypersyncClient,
    module_address: HexAddress,
    topic_map: dict[str, dict],
    recv_timeout: float = 90.0,
) -> list[DecodedGuardEvent]:
    """Read guard config events using Hypersync streaming.

    :param client:
        Configured Hypersync client for the target chain.

    :param module_address:
        TradingStrategyModuleV0 contract address to scan.

    :param topic_map:
        topic0 → ABI entry mapping.

    :param recv_timeout:
        Timeout for each Hypersync recv() call.

    :return:
        List of decoded guard events sorted by (block_number, log_index).
    """
    assert hypersync is not None, "hypersync package is required"

    topic0_list = list(topic_map.keys())

    query = hypersync.Query(
        from_block=0,
        logs=[
            hypersync.LogSelection(
                address=[module_address.lower()],
                topics=[topic0_list],
            )
        ],
        field_selection=hypersync.FieldSelection(
            block=[BlockField.NUMBER, BlockField.TIMESTAMP],
            log=[
                LogField.BLOCK_NUMBER,
                LogField.LOG_INDEX,
                LogField.ADDRESS,
                LogField.TRANSACTION_HASH,
                LogField.TOPIC0,
                LogField.TOPIC1,
                LogField.TOPIC2,
                LogField.TOPIC3,
                LogField.DATA,
            ],
        ),
    )

    receiver = await client.stream(query, hypersync.StreamConfig())
    events: list[DecodedGuardEvent] = []

    while True:
        res = await asyncio.wait_for(receiver.recv(), timeout=recv_timeout)
        if res is None:
            break

        if res.data.logs:
            for log in res.data.logs:
                # Convert Hypersync log format to web3-style dict
                web3_log = {
                    "address": log.address,
                    "topics": log.topics,
                    "data": log.data or "0x",
                    "blockNumber": log.block_number,
                    "transactionHash": log.transaction_hash,
                    "logIndex": log.log_index,
                }
                event = _decode_event_from_log(web3_log, topic_map)
                if event is not None:
                    events.append(event)

    events.sort(key=lambda e: (e.block_number, e.log_index))
    return events


def _fetch_guard_events_hypersync(
    client: hypersync.HypersyncClient,
    module_address: HexAddress,
    topic_map: dict[str, dict],
) -> list[DecodedGuardEvent]:
    """Synchronous wrapper around the async Hypersync event reader."""
    return asyncio.run(_fetch_guard_events_hypersync_async(client, module_address, topic_map))


# ---------------------------------------------------------------------------
# Event reading — web3 RPC backend (for Anvil fork testing)
# ---------------------------------------------------------------------------


def _fetch_guard_events_web3(
    web3: Web3,
    module_address: HexAddress,
    topic_map: dict[str, dict],
    from_block: int = 0,
) -> list[DecodedGuardEvent]:
    """Read guard config events using :func:`~eth_defi.event_reader.reader.read_events`.

    Uses the project's standard event reading API with chunked
    ``eth_getLogs`` calls, retry logic, and proper error handling.
    Fallback for Anvil forks where Hypersync indexing is unavailable.

    :param web3:
        Web3 connection to the chain.

    :param module_address:
        TradingStrategyModuleV0 contract address.

    :param topic_map:
        topic0 → ABI entry mapping.

    :param from_block:
        Starting block for the scan.  On Anvil forks pass a block near
        the deployment to avoid scanning the full forked history.

    :return:
        List of decoded guard events sorted by (block_number, log_index).
    """
    end_block = web3.eth.block_number

    if from_block > end_block:
        return []

    # Build a Filter for read_events().
    # Filter.topics normally maps topic0 → ContractEvent, but we use
    # topic0 → ABI entry dict for our own decoding.
    event_filter = Filter(
        topics=topic_map,
        bloom=None,
        contract_address=Web3.to_checksum_address(module_address),
    )

    events: list[DecodedGuardEvent] = []
    for log in read_events(
        web3,
        start_block=from_block,
        end_block=end_block,
        filter=event_filter,
        extract_timestamps=None,
        chunk_size=10_000,
    ):
        event = _decode_event_from_log(log, topic_map)
        if event is not None:
            events.append(event)

    events.sort(key=lambda e: (e.block_number, e.log_index))
    return events


# ---------------------------------------------------------------------------
# Main public functions
# ---------------------------------------------------------------------------


def fetch_guard_config_events(
    safe_address: HexAddress,
    web3: Web3,
    hypersync_client: hypersync.HypersyncClient | None = None,
    chain_web3: dict[int, Web3] | None = None,
    follow_cctp: bool = True,
    from_block: int | dict[int, int] = 0,
) -> tuple[dict[int, list[DecodedGuardEvent]], dict[int, HexAddress]]:
    """Read all guard configuration events for a multichain Lagoon deployment.

    Starting from a Safe address on a single chain, resolves the
    TradingStrategyModuleV0, reads its configuration events, and
    optionally follows CCTP destination chains.

    :param safe_address:
        Address of the Gnosis Safe multisig.

    :param web3:
        Web3 connection to the starting chain.

    :param hypersync_client:
        If provided, use Hypersync for event reading (production path).
        If ``None``, falls back to ``web3.eth.get_logs()`` (Anvil testing).

    :param chain_web3:
        Optional dict of ``{chain_id: Web3}`` for CCTP-discovered chains.
        If not provided, auto-creates connections from ``JSON_RPC_*`` env vars.

    :param follow_cctp:
        Whether to follow CCTP destination domains to other chains.

    :param from_block:
        Starting block for the event scan.  Pass an ``int`` to use the same
        value for all chains, or a ``dict[chain_id, block_number]`` to specify
        per-chain starting blocks.  On Anvil forks pass recent blocks to
        avoid scanning the full forked chain history.

    :return:
        Tuple of ``(events_per_chain, module_addresses_per_chain)`` where
        ``events_per_chain`` is ``{chain_id: [DecodedGuardEvent, ...]}``
        and ``module_addresses_per_chain`` is ``{chain_id: module_address}``.
    """
    safe_address = Web3.to_checksum_address(safe_address)
    chain_id = web3.eth.chain_id
    topic_map = _build_event_topic_map()

    # Normalise from_block to a dict keyed by chain_id
    if isinstance(from_block, int):
        _from_blocks: dict[int, int] = {}
        _default_from_block = from_block
    else:
        _from_blocks = dict(from_block)
        _default_from_block = 0

    # Resolve module on the starting chain
    module_address = resolve_trading_strategy_module(web3, safe_address)
    if module_address is None:
        raise ValueError(f"No TradingStrategyModuleV0 found on Safe {safe_address} on chain {chain_id} ({get_chain_name(chain_id)})")

    logger.info(
        "Reading guard config events from chain %d (%s), module %s",
        chain_id,
        get_chain_name(chain_id),
        module_address,
    )

    # Read events from the starting chain
    start_block = _from_blocks.get(chain_id, _default_from_block)
    if hypersync_client is not None:
        events = _fetch_guard_events_hypersync(hypersync_client, module_address, topic_map)
    else:
        events = _fetch_guard_events_web3(web3, module_address, topic_map, from_block=start_block)

    logger.info("Found %d guard config events on chain %d", len(events), chain_id)

    all_events: dict[int, list[DecodedGuardEvent]] = {chain_id: events}
    module_addresses: dict[int, HexAddress] = {chain_id: module_address}

    # Follow CCTP destinations
    if follow_cctp:
        cctp_domains = set()
        for event in events:
            if event.event_name == "CCTPDestinationApproved":
                domain = event.args.get("domain")
                if domain is not None:
                    cctp_domains.add(domain)

        if cctp_domains:
            logger.info("Discovered CCTP destinations: %s", cctp_domains)

        # Use testnet domain→chain mapping when source chain is a Sepolia testnet
        domain_to_chain = TESTNET_CCTP_DOMAIN_TO_CHAIN_ID if chain_id in TESTNET_CHAIN_IDS else CCTP_DOMAIN_TO_CHAIN_ID

        for domain in sorted(cctp_domains):
            dest_chain_id = domain_to_chain.get(domain)
            if dest_chain_id is None:
                logger.warning("Unknown CCTP domain %d, skipping", domain)
                continue

            if dest_chain_id == chain_id:
                continue  # Skip self

            dest_web3 = _get_chain_web3(dest_chain_id, chain_web3)
            if dest_web3 is None:
                logger.warning(
                    "No web3 connection for chain %d (%s), skipping",
                    dest_chain_id,
                    get_chain_name(dest_chain_id),
                )
                continue

            dest_module = resolve_trading_strategy_module(dest_web3, safe_address)
            if dest_module is None:
                logger.warning(
                    "No TradingStrategyModuleV0 on chain %d (%s) for Safe %s",
                    dest_chain_id,
                    get_chain_name(dest_chain_id),
                    safe_address,
                )
                continue

            logger.info(
                "Reading guard config events from chain %d (%s), module %s",
                dest_chain_id,
                get_chain_name(dest_chain_id),
                dest_module,
            )

            # Use Hypersync for destination chains if available
            dest_start_block = _from_blocks.get(dest_chain_id, _default_from_block)
            dest_hypersync = _get_hypersync_client_for_chain(dest_chain_id) if hypersync_client is not None else None
            if dest_hypersync is not None:
                dest_events = _fetch_guard_events_hypersync(dest_hypersync, dest_module, topic_map)
            else:
                dest_events = _fetch_guard_events_web3(dest_web3, dest_module, topic_map, from_block=dest_start_block)

            logger.info("Found %d guard config events on chain %d", len(dest_events), dest_chain_id)
            all_events[dest_chain_id] = dest_events
            module_addresses[dest_chain_id] = dest_module

    return all_events, module_addresses


def build_multichain_guard_config(
    events: dict[int, list[DecodedGuardEvent]],
    safe_address: HexAddress,
    module_addresses: dict[int, HexAddress],
) -> MultichainGuardConfig:
    """Build structured guard configuration from raw events.

    Processes events chronologically per chain:
    ``*Approved`` adds to the corresponding set, ``*Removed`` removes.

    :param events:
        Raw events per chain from :func:`fetch_guard_config_events`.

    :param safe_address:
        Deterministic Safe address shared across chains.

    :param module_addresses:
        Module address per chain from :func:`fetch_guard_config_events`.

    :return:
        Structured multichain guard configuration.
    """
    safe_address = Web3.to_checksum_address(safe_address)
    chains: dict[int, ChainGuardConfig] = {}

    for chain_id, chain_events in events.items():
        module_address = module_addresses.get(chain_id, "")
        chains[chain_id] = _build_chain_config(
            chain_id,
            safe_address,
            module_address,
            chain_events,
        )

    return MultichainGuardConfig(
        safe_address=safe_address,
        chains=chains,
    )


def _build_chain_config(
    chain_id: int,
    safe_address: HexAddress,
    module_address: HexAddress,
    events: list[DecodedGuardEvent],
) -> ChainGuardConfig:
    """Process events for a single chain into a ChainGuardConfig."""

    # Use sets for add/remove tracking, convert to tuples at the end
    senders: set[HexAddress] = set()
    receivers: set[HexAddress] = set()
    assets: set[HexAddress] = set()
    any_asset = False
    approval_destinations: set[HexAddress] = set()
    withdraw_destinations: set[HexAddress] = set()
    delegation_approval_destinations: set[HexAddress] = set()
    lagoon_vaults: set[HexAddress] = set()
    erc4626_vaults: set[HexAddress] = set()
    cctp_messengers: set[HexAddress] = set()
    cctp_destinations: set[int] = set()
    cowswap_settlements: set[HexAddress] = set()
    velora_swappers: set[HexAddress] = set()
    gmx_routers: dict[HexAddress, HexAddress] = {}  # exchange -> synthetics
    gmx_markets: set[HexAddress] = set()
    hypercore_core_writers: set[HexAddress] = set()
    hypercore_deposit_wallets: set[HexAddress] = set()
    hypercore_vaults: set[HexAddress] = set()
    call_sites: set[tuple[HexAddress, str]] = set()

    for event in events:
        name = event.event_name
        args = event.args

        # Core access control
        if name == "SenderApproved":
            senders.add(args["sender"])
        elif name == "SenderRemoved":
            senders.discard(args["sender"])
        elif name == "ReceiverApproved":
            receivers.add(args["receiver"])
        elif name == "ReceiverRemoved":
            receivers.discard(args["receiver"])

        # Token management
        elif name == "AssetApproved":
            assets.add(args["asset"])
        elif name == "AssetRemoved":
            assets.discard(args["asset"])
        elif name == "AnyAssetSet":
            any_asset = args.get("value", False)

        # Transfer destinations
        elif name == "ApprovalDestinationApproved":
            approval_destinations.add(args["destination"])
        elif name == "ApprovalDestinationRemoved":
            approval_destinations.discard(args["destination"])
        elif name == "WithdrawDestinationApproved":
            withdraw_destinations.add(args["destination"])
        elif name == "WithdrawDestinationRemoved":
            withdraw_destinations.discard(args["destination"])
        elif name == "DelegationApprovalDestinationApproved":
            delegation_approval_destinations.add(args["destination"])
        elif name == "DelegationApprovalDestinationRemoved":
            delegation_approval_destinations.discard(args["destination"])

        # Protocol integrations
        elif name == "LagoonVaultApproved":
            lagoon_vaults.add(args["vault"])
        elif name == "ERC4626Approved":
            erc4626_vaults.add(args["vault"])
        elif name == "CCTPMessengerApproved":
            cctp_messengers.add(args["tokenMessenger"])
        elif name == "CCTPDestinationApproved":
            cctp_destinations.add(args["domain"])
        elif name == "CCTPDestinationRemoved":
            cctp_destinations.discard(args["domain"])
        elif name == "CowSwapApproved":
            cowswap_settlements.add(args["settlementContract"])
        elif name == "VeloraSwapperApproved":
            velora_swappers.add(args["augustusSwapper"])
        elif name == "GMXRouterApproved":
            gmx_routers[args["exchangeRouter"]] = args["syntheticsRouter"]
        elif name == "GMXMarketApproved":
            gmx_markets.add(args["market"])
        elif name == "GMXMarketRemoved":
            gmx_markets.discard(args["market"])
        elif name == "CoreWriterApproved":
            hypercore_core_writers.add(args["coreWriter"])
        elif name == "CoreDepositWalletApproved":
            hypercore_deposit_wallets.add(args["wallet"])
        elif name == "HypercoreVaultApproved":
            hypercore_vaults.add(args["vault"])
        elif name == "HypercoreVaultRemoved":
            hypercore_vaults.discard(args["vault"])

        # Call sites
        elif name == "CallSiteApproved":
            call_sites.add((args["target"], args["selector"]))
        elif name == "CallSiteRemoved":
            call_sites.discard((args["target"], args["selector"]))

    return ChainGuardConfig(
        chain_id=chain_id,
        chain_name=get_chain_name(chain_id),
        safe_address=safe_address,
        module_address=module_address,
        senders=tuple(sorted(senders)),
        receivers=tuple(sorted(receivers)),
        assets=tuple(sorted(assets)),
        any_asset=any_asset,
        approval_destinations=tuple(sorted(approval_destinations)),
        withdraw_destinations=tuple(sorted(withdraw_destinations)),
        delegation_approval_destinations=tuple(sorted(delegation_approval_destinations)),
        lagoon_vaults=tuple(sorted(lagoon_vaults)),
        erc4626_vaults=tuple(sorted(erc4626_vaults)),
        cctp_messengers=tuple(sorted(cctp_messengers)),
        cctp_destinations=tuple(sorted(cctp_destinations)),
        cowswap_settlements=tuple(sorted(cowswap_settlements)),
        velora_swappers=tuple(sorted(velora_swappers)),
        gmx_routers=tuple(sorted((k, v) for k, v in gmx_routers.items())),
        gmx_markets=tuple(sorted(gmx_markets)),
        hypercore_core_writers=tuple(sorted(hypercore_core_writers)),
        hypercore_deposit_wallets=tuple(sorted(hypercore_deposit_wallets)),
        hypercore_vaults=tuple(sorted(hypercore_vaults)),
        call_sites=tuple(sorted(call_sites)),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_chain_web3(
    chain_id: int,
    chain_web3: dict[int, Web3] | None,
) -> Web3 | None:
    """Get a web3 connection for a chain, from the override dict or env vars."""
    if chain_web3 and chain_id in chain_web3:
        return chain_web3[chain_id]

    # Auto-create from environment variables
    try:
        rpc_url = read_json_rpc_url(chain_id)
        web3 = create_multi_provider_web3(rpc_url)
        logger.info(
            "Auto-created web3 connection for chain %d (%s) from env var",
            chain_id,
            get_chain_name(chain_id),
        )
        return web3
    except (ValueError, AssertionError) as e:
        logger.debug("Cannot create web3 for chain %d: %s", chain_id, e)
        return None


def _get_hypersync_client_for_chain(chain_id: int) -> hypersync.HypersyncClient | None:
    """Create a Hypersync client for a given chain, if supported."""
    if hypersync is None:
        return None

    try:
        from eth_defi.hypersync.server import (  # Conditional import: hypersync extras may not be installed
            get_hypersync_server,
            is_hypersync_supported_chain,
        )

        if not is_hypersync_supported_chain(chain_id):
            return None

        url = get_hypersync_server(chain_id)
        api_key = os.environ.get("HYPERSYNC_API_KEY")
        config = hypersync.ClientConfig(url=url)
        if api_key:
            config = hypersync.ClientConfig(url=url, bearer_token=api_key)
        return hypersync.HypersyncClient(config)
    except Exception as e:
        logger.debug("Cannot create Hypersync client for chain %d: %s", chain_id, e)
        return None
