"""Constants for the Hyperliquid integration.

Shared constants used across the Hyperliquid modules
(:py:mod:`~eth_defi.hyperliquid.daily_metrics`,
:py:mod:`~eth_defi.hyperliquid.vault_data_export`, etc.).
"""

import datetime
from decimal import Decimal
from pathlib import Path
from typing import Final

from eth_typing import HexAddress

from eth_defi.vault.fee import VaultFeeMode

#: Synthetic in-house chain ID for Hypercore (Hyperliquid's native non-EVM layer).
#:
#: Added to :py:data:`eth_defi.chain.CHAIN_NAMES` as ``9999: "Hypercore"``.
HYPERCORE_CHAIN_ID: int = 9999

#: Default path for Hyperliquid daily metrics DuckDB database.
HYPERLIQUID_DAILY_METRICS_DATABASE = Path.home() / ".tradingstrategy" / "vaults" / "hyperliquid-vaults.duckdb"

#: Default path for Hyperliquid high-frequency metrics DuckDB database.
HYPERLIQUID_HIGH_FREQ_METRICS_DATABASE = Path.home() / ".tradingstrategy" / "vaults" / "hyperliquid-vaults-hf.duckdb"

#: Default scan interval for high-frequency mode.
#:
#: This is the *scan trigger* cadence (how often the cron job polls the
#: ``vaultDetails`` API), **not** the data resolution. The API serves the
#: ``day`` period at a fixed ~20 min resolution and downsamples older points
#: as they age (``week`` ~3h, ``month`` ~10.5h, ``allTime`` ~weekly). ~20 min
#: is therefore a hard floor — polling faster than this yields no finer data.
#: The 4h default only needs to stay ``<= 24h`` so each run snapshots the
#: ``day`` window before its points age out and get coarsened; 4h is a 6x
#: safety margin against missed runs. See
#: ``scripts/hyperliquid/README-hyperliquid-vaults-high-frequency.md``.
HYPERLIQUID_HIGH_FREQ_DEFAULT_INTERVAL: datetime.timedelta = datetime.timedelta(hours=4)

#: Fixed performance fee (profit share) for Hyperliquid native vault leaders.
#:
#: Legacy user-created Hyperliquid native vaults use a fixed 10% profit share
#: to the vault leader. Protocol vaults (e.g. HLP) have no leader profit share;
#: trading and funding costs still affect the vault account value.
#:
#: Source: https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/hypercore-vaults-legacy
HYPERLIQUID_VAULT_PERFORMANCE_FEE: float = 0.10

#: Fee mode for Hyperliquid native vaults.
#:
#: The leader's 10% profit share is deducted from depositor profits at withdrawal time.
#: The vault account value and PnL history therefore represent the return before
#: this investor-facing withdrawal fee. This matches
#: :py:attr:`~eth_defi.vault.fee.VaultFeeMode.externalised`, allowing the
#: analytics pipeline to calculate the investor's net return from the 10% share.
#:
#: Source: https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/hypercore-vaults-legacy
#: Source: https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/for-vault-depositors-legacy
HYPERLIQUID_VAULT_FEE_MODE: VaultFeeMode = VaultFeeMode.externalised

#: Lockup period for user-created Hyperliquid vaults.
#:
#: After depositing into a user vault, followers must wait 1 day
#: before they can withdraw.
#:
#: Source: https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/for-vault-depositors
HYPERLIQUID_USER_VAULT_LOCKUP: datetime.timedelta = datetime.timedelta(days=1)

#: Lockup period for Hyperliquid protocol vaults (HLP and sub-vaults).
#:
#: After depositing into the HLP or its child vaults, followers must
#: wait 4 days before they can withdraw.
#:
#: Source: https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/for-vault-depositors
HYPERLIQUID_PROTOCOL_VAULT_LOCKUP: datetime.timedelta = datetime.timedelta(days=4)


# ──────────────────────────────────────────────
# HyperCore <> HyperEVM bridge fees
# ──────────────────────────────────────────────

#: Fee margin reserved when bridging the full HyperCore spot balance
#: back to HyperEVM via ``sendAsset`` (CoreWriter action ID 13).
#:
#: Hyperliquid charges a bridge fee on HyperCore spot **before** the
#: linked token settles on HyperEVM.  The fee equals 200k gas at the
#: base gas price of the next HyperEVM block.  HYPE is consumed first
#: if available; otherwise the fee is taken in USDC.
#:
#: Source: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/hypercore-less-than-greater-than-hyperevm-transfers
#:
#: In manual mainnet verification (2026-03-23), bridging 9 USDC in tx
#: `0x82c7ca18fed4952dfdfdffb4e7565cc768c2ab14fe7533bb42a0734cfdf36b16
#: <https://hyperevmscan.io/tx/0x82c7ca18fed4952dfdfdffb4e7565cc768c2ab14fe7533bb42a0734cfdf36b16>`__
#: returned 9 USDC to HyperEVM while reducing spot by ~9.000783 USDC
#: (fee ~0.000783 USDC at 0.1 Gwei base gas price).
#:
#: When withdrawing the **entire** spot balance, this margin must be
#: subtracted from the withdrawal amount so enough USDC remains on spot
#: to cover the fee.  Without it the withdrawal silently fails (no error,
#: no event — USDC stays in spot).  The value (0.01 USDC) provides ~12x
#: headroom over the observed fee.
#:
#: See :py:func:`~eth_defi.hyperliquid.core_writer.compute_spot_to_evm_withdrawal_amount`.
HYPERCORE_BRIDGE_FEE_MARGIN: Decimal = Decimal("0.01")


# ──────────────────────────────────────────────
# Well-known Hyperliquid system vault addresses
# ──────────────────────────────────────────────

#: HLP (Hyperliquidity Provider) parent vault address on mainnet.
#:
#: The HLP is the main protocol-operated market-making vault.
#: It has ``relationship_type="parent"`` in the Hyperliquid API,
#: with multiple child sub-vaults that handle different strategies.
#: No performance fee (0%), 4-day lockup period.
#: Leader: ``0x677d831aef5328190852e24f13c46cac05f984e7``
#:
#: Source: Hyperliquid ``vaultDetails`` API, https://app.hyperliquid.xyz/vaults
HLP_VAULT_ADDRESS_MAINNET: HexAddress = HexAddress("0xdfc24b077bc1425ad1dea75bcb6f8158e10df303")

#: HLP (Hyperliquidity Provider) vault address on testnet.
#:
#: Testnet equivalent of :py:data:`HLP_VAULT_ADDRESS_MAINNET`.
#:
#: Source: Hyperliquid ``vaultDetails`` API (testnet)
HLP_VAULT_ADDRESS_TESTNET: HexAddress = HexAddress("0xa15099a30bbf2e68942d6f4c43d70d04faeab0a0")

#: HLP child vault: Strategy A.
#:
#: One of the HLP sub-vaults (``relationship_type="child"``).
#: Parent: :py:data:`HLP_VAULT_ADDRESS_MAINNET`.
HLP_STRATEGY_A_ADDRESS: HexAddress = HexAddress("0x010461c14e146ac35fe42271bdc1134ee31c703a")

#: HLP child vault: Strategy B.
#:
#: One of the HLP sub-vaults (``relationship_type="child"``).
#: Parent: :py:data:`HLP_VAULT_ADDRESS_MAINNET`.
HLP_STRATEGY_B_ADDRESS: HexAddress = HexAddress("0x31ca8395cf837de08b24da3f660e77761dfb974b")

#: HLP child vault: Strategy X.
#:
#: One of the HLP sub-vaults (``relationship_type="child"``).
#: Parent: :py:data:`HLP_VAULT_ADDRESS_MAINNET`.
HLP_STRATEGY_X_ADDRESS: HexAddress = HexAddress("0x469f690213c467c39a23efacfd2816896009d7d8")

#: HLP child vault: HLP Liquidator.
#:
#: One of the HLP sub-vaults (``relationship_type="child"``).
#: Parent: :py:data:`HLP_VAULT_ADDRESS_MAINNET`.
HLP_LIQUIDATOR_ADDRESS: HexAddress = HexAddress("0x2e3d94f0562703b25c83308a05046ddaf9a8dd14")

#: HLP child vault: HLP Liquidator 2.
#:
#: One of the HLP sub-vaults (``relationship_type="child"``).
#: Parent: :py:data:`HLP_VAULT_ADDRESS_MAINNET`.
HLP_LIQUIDATOR_2_ADDRESS: HexAddress = HexAddress("0xb0a55f13d22f66e6d495ac98113841b2326e9540")

#: HLP child vault: HLP Liquidator 3.
#:
#: One of the HLP sub-vaults (``relationship_type="child"``).
#: Parent: :py:data:`HLP_VAULT_ADDRESS_MAINNET`.
HLP_LIQUIDATOR_3_ADDRESS: HexAddress = HexAddress("0x5e177e5e39c0f4e421f5865a6d8beed8d921cb70")

#: HLP child vault: HLP Liquidator 4.
#:
#: One of the HLP sub-vaults (``relationship_type="child"``).
#: Parent: :py:data:`HLP_VAULT_ADDRESS_MAINNET`.
HLP_LIQUIDATOR_4_ADDRESS: HexAddress = HexAddress("0x2ed5c4484ea3ff8b57d5f2fb152a40d9f2b68308")

#: Standalone Liquidator protocol vault on mainnet.
#:
#: Listed under "Protocol Vaults" on the Hyperliquid UI but has
#: ``relationship_type="normal"`` in the API (not a child of HLP).
#: Leader: ``0xfc13878222c06e7cc043841027c893a4c9f180c9``
#:
#: Source: https://app.hyperliquid.xyz/vaults
LIQUIDATOR_VAULT_ADDRESS: HexAddress = HexAddress("0x63c621a33714ec48660e32f2374895c8026a3a00")

#: All HLP child vault addresses on mainnet.
#:
#: These are the sub-vaults that execute specific strategies
#: on behalf of the HLP parent vault.
HLP_CHILD_VAULT_ADDRESSES: set[HexAddress] = {
    HLP_STRATEGY_A_ADDRESS,
    HLP_STRATEGY_B_ADDRESS,
    HLP_STRATEGY_X_ADDRESS,
    HLP_LIQUIDATOR_ADDRESS,
    HLP_LIQUIDATOR_2_ADDRESS,
    HLP_LIQUIDATOR_3_ADDRESS,
    HLP_LIQUIDATOR_4_ADDRESS,
}

#: Set of all well-known Hyperliquid system vault addresses (mainnet).
#:
#: Includes the HLP parent vault, all HLP child vaults, and the
#: standalone Liquidator protocol vault. These are protocol-operated
#: vaults with special properties (no fees, longer lockup periods,
#: parent/child relationships).
#: Useful for filtering out system vaults from user-created vaults.
HYPERLIQUID_SYSTEM_VAULT_ADDRESSES: set[HexAddress] = {
    HLP_VAULT_ADDRESS_MAINNET,
    LIQUIDATOR_VAULT_ADDRESS,
} | HLP_CHILD_VAULT_ADDRESSES


#: Reviewed HyperCore-reading targets requiring isolated requests on chain 999.
#:
#: The all-chains scanner selects this policy in its main entrypoint and passes
#: it to generic readers; importing a reader alone must not enable chain policy.
#: This is a batching policy, not an admission or risk exclusion. Entries below
#: come from the 2026-08-28 investigation and the address-specific evidence in
#: ``eth_defi/vault/risk.py``. Existing blacklists still take precedence; those
#: entries stay dormant until a separately reviewed valuation path enables them.
#: Mixed-batch addresses in the 2026-10-03 logs are not proof of culpability and
#: are deliberately not added without bisection or verified HyperCore dependency.
#: See ``docs/README-hyperevm-hypercore-read-gas.md`` for provider gas accounting.
#:
#: Maintenance guidance
#: --------------------
#:
#: The purpose is to keep robust contracts in normal Multicall batches while
#: containing HyperCore precompile gas pressure to the affected target. The
#: default isolated limit is one encoded subcall, not one vault: a vault with
#: four methods can therefore cost four RPC requests. Keep additions narrow so
#: the workaround does not unnecessarily increase normal scanning costs.
#:
#: Before adding an entry, identify the actual chain-999 call target from a
#: trace or a bounded replay/bisection of the failing scanner payload. Record
#: the affected selectors, provider, source block and failure, and establish
#: either the HyperCore dependency or reproducible batch gas amplification.
#: Merely appearing in a failed mixed batch, sharing a protocol name, or having
#: an archive gap, timeout or rate-limit error does not justify inclusion.
#: Use the lowercase deployed proxy address passed to Multicall, wrapped in
#: ``HexAddress``; an implementation address would not match proxy calls.
#:
#: Place a separate evidence comment immediately above each address. For new
#: entries and evidence updates, use this format, stating verified findings and
#: any unresolved limitations::
#:
#:     # Protocol / vault (verified YYYY-MM-DD):
#:     # Trigger: selectors, provider host, source block and observed failure.
#:     # Evidence: trace/precompile or replay/bisection; investigation doc or PR.
#:     # Policy: why isolation helps; active or blacklisted, with history limits.
#:     HexAddress("0x<lowercase deployed target address>"),
#:
#: Link the supporting investigation in the comment and update the gas document
#: above when the behaviour or provider limits change. Record provider hosts
#: rather than credential-bearing RPC URLs. Preserve existing risk exclusions:
#: adding an entry must never remove its blacklist or promise historical NAV
#: availability. Historical row preservation uses the owning vault address;
#: a helper-only entry cannot safely defer an unlisted owner's observations.
#: Review that ownership and preservation path before adding helper targets.
#:
#: Validate additions with the real scanner selectors at a fixed source block,
#: comparing mixed and isolated reads alongside a cheap pure-EVM control. Use
#: the supplied ``JSON_RPC_HYPERLIQUID`` endpoints and bounded requests, without
#: changing production prices or reader state. The manual command documented in
#: ``scripts/erc-4626/check-hyperevm-greylist.py`` checks HYPED and a control;
#: it does not automatically exercise every entry, so new targets need their
#: own probes. Keep batch-planning and saved-row-preservation coverage intact.
#:
#: Remove an entry only after repeatable checks show that its formerly failing
#: mixed payload works under the scanner's provider settings and relevant block
#: range. A successful single call at head, or silence while a vault remains
#: blacklisted, is not evidence that batching is safe. Record the removal's
#: evidence in the investigation document or PR for future regressions.
HYPEREVM_MULTICALL_GREYLIST: Final[frozenset[HexAddress]] = frozenset(
    {
        # Hyperdrive HYPED: replay/bisection proved that repeated totalAssets,
        # convertToAssets and maxDeposit calls exceed Goldsky/dRPC gas caps despite
        # cheap execution. Seven HyperCore reads per valuation; totalSupply is cheap.
        HexAddress("0x4d0ff6a0dd9f7316b674fb37993a3ce28bea340e"),
        # Hyperdrive HLP: debug_traceCall confirmed the 0x0809 L1-block precompile
        # dependency. Remains blacklisted for unreadable history; isolation must
        # not implicitly restore historical admission. See the gas document above.
        HexAddress("0x6ed613e86e8d0b6617e445f17323ac0162ff6ce6"),
        # Gamma Symphony: debug_traceCall confirmed the same 0x0809 dependency.
        # Remains blacklisted for unreadable history; isolation must not implicitly
        # restore historical admission. See the gas document above.
        HexAddress("0x2b37f3566933e4dbe59c6b86bedbc91c1e04d774"),
        # Raga rHYPE AccountMarginSummary (0x080f): two copies of the four scanner
        # probes exhaust Goldsky/dRPC gas accounting. Current-state execution is
        # cheap, but head-200 fails on all providers; its blacklist remains intact.
        HexAddress("0xa4ab2aa522234a2ea2713ebade0fec069e4f3a95"),
        # RatesETF RATES Withdrawable (0x0803): combining its valuation probes with
        # another affected vault exhausts the same caps; historical state is absent.
        HexAddress("0xda482b56c85da2ec8e59d65ec4b1f9a6b414061e"),
        # Separate Raga rHYPE proxy, SpotBalance (0x0801): duplicated scanner probes
        # exhaust both providers. Do not confuse it with the AccountMargin proxy.
        HexAddress("0x77f1652d969dd56a75a2cb1a7c60fb7c314d71a3"),
        # HFY USD0: tracing and runtime failure strings establish SpotBalance plus
        # MarkPx/Position/Withdrawable dependencies. Keep its existing blacklist.
        HexAddress("0xd3f41dac84594332e4ff3c7fd2242deaf7857e79"),
        # Altcopy Index: trace-confirmed spot balance and eight vault-equity reads
        # (0x0801/0x0802); batching amplifies precompile pressure. Remains blacklisted.
        HexAddress("0xf8f7c57fb94cc1f7f2c77dc29b5216c4d3c3125d"),
    }
)
