"""Canonical per-chain fork blocks for shared-fork characterisation tests.

Tests that only read (characterise) a vault historically pin a fixed
``fork_block_number`` for reproducibility. Historically each test picked its own
arbitrary block, so tests on the same chain could not share one Anvil fork. To
let many tests share a single fork (via
:class:`eth_defi.testing.anvil_fork_pool.AnvilForkPool`), we **normalise** them
onto one canonical block per chain.

The canonical block is the last block **at or immediately before midnight UTC**
on a recent date. A recent midnight is:

- **recent**, so vaults integrated and live today generally have state at this
  block — but this is not a guarantee for a specific vault; validate per vault
  (read the values) before normalising a test onto it;
- **fixed and reproducible** (unlike ``latest``), so value assertions stay
  stable and the Anvil fork RPC cache stays warm;
- **shared**, so all normalised tests on a chain reuse one fork.

If a test's vault did not exist (or has no meaningful state) at the block below,
give that test its own block. When bumping the constant to a newer midnight,
re-run the affected tests to refresh any block-dependent assertions.

Blocks captured by binary-searching the archive node for the last block with
``timestamp <= midnight``.
"""

#: Arbitrum One (chain id 42161) — last block with timestamp <= 2026-07-24T00:00:00Z.
#: Validated for the current PoC vaults (Goat, Harvest, Plutus). Validate any
#: additional vault before normalising its test onto this block.
ARBITRUM_MIDNIGHT_BLOCK = 487_039_644
