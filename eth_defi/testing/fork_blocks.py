"""Canonical per-chain fork blocks for shared-fork characterisation tests.

Tests that only read (characterise) a vault historically pin a fixed
``fork_block_number`` for reproducibility. Historically each test picked its own
arbitrary block, so tests on the same chain could not share one Anvil fork. To
let many tests share a single fork (via
:class:`eth_defi.testing.anvil_fork_pool.AnvilForkPool`), we **normalise** them
onto one canonical block per chain.

The canonical block is the last block **at or immediately before midnight UTC**
on a recent date (2026-07-24 00:00:00 UTC). A recent midnight is:

- **recent**, so vaults integrated and live today generally have state at this
  block — but this is not a guarantee for a specific vault; validate per vault
  (read the values) before normalising a test onto it;
- **fixed and reproducible** (unlike ``latest``), so value assertions stay
  stable and the Anvil fork RPC cache stays warm and dense — every same-chain
  characterisation test replaying reads for one block means the on-disk RPC
  cache covers them all, so warm runs hit the upstream archive far less;
- **shared**, so all normalised tests on a chain reuse one fork.

If a test's vault did not exist (or has no meaningful state) at the block below,
or a value invariant no longer holds there (e.g. an over-utilised lending
vault), give that test its own block. When bumping a constant to a newer
midnight, re-run the affected tests to refresh any block-dependent assertions.

Chains without archive history (e.g. Monad) cannot use a fixed historical block
and must not be normalised here.

Blocks captured by binary-searching the archive node for the last block with
``timestamp <= 2026-07-24T00:00:00Z``.
"""

#: Ethereum mainnet (chain id 1).
ETHEREUM_MIDNIGHT_BLOCK = 25_598_869

#: Arbitrum One (chain id 42161).
ARBITRUM_MIDNIGHT_BLOCK = 487_039_644

#: Base (chain id 8453).
BASE_MIDNIGHT_BLOCK = 49_030_926

#: BNB Smart Chain (chain id 56).
BINANCE_MIDNIGHT_BLOCK = 111_758_906

#: Polygon PoS (chain id 137).
POLYGON_MIDNIGHT_BLOCK = 90_764_135

#: Avalanche C-Chain (chain id 43114).
AVALANCHE_MIDNIGHT_BLOCK = 91_076_594

#: HyperEVM / Hyperliquid (chain id 999).
HYPERLIQUID_MIDNIGHT_BLOCK = 41_271_954

#: Plasma (chain id 9745).
PLASMA_MIDNIGHT_BLOCK = 27_910_844

#: Sonic (chain id 146).
SONIC_MIDNIGHT_BLOCK = 76_403_589

#: Berachain (chain id 80094).
BERACHAIN_MIDNIGHT_BLOCK = 23_918_239

#: Canonical midnight block keyed by chain id, for programmatic lookup.
MIDNIGHT_BLOCKS: dict[int, int] = {
    1: ETHEREUM_MIDNIGHT_BLOCK,
    56: BINANCE_MIDNIGHT_BLOCK,
    137: POLYGON_MIDNIGHT_BLOCK,
    146: SONIC_MIDNIGHT_BLOCK,
    999: HYPERLIQUID_MIDNIGHT_BLOCK,
    8453: BASE_MIDNIGHT_BLOCK,
    9745: PLASMA_MIDNIGHT_BLOCK,
    42161: ARBITRUM_MIDNIGHT_BLOCK,
    43114: AVALANCHE_MIDNIGHT_BLOCK,
    80094: BERACHAIN_MIDNIGHT_BLOCK,
}


def get_midnight_block(chain_id: int) -> int:
    """Return the canonical midnight fork block for a chain id.

    :param chain_id:
        EVM chain id, e.g. ``1`` for Ethereum mainnet.

    :return:
        Fixed block number to fork at (see module docstring).

    :raises KeyError:
        If the chain has no canonical midnight block recorded (e.g. a chain
        without archive history, or one not yet added here).
    """
    return MIDNIGHT_BLOCKS[chain_id]
