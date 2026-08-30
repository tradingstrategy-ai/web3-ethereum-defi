"""Fixed scope for the one-off Rysk Premium production migration.

The recurring scanner discovers Rysk pools from onchain events and does not
use this address list. These targets exist only so the metadata repair and
historical backfill operate on the same reviewed production scope.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from eth_typing import HexAddress


@dataclass(slots=True, frozen=True)
class RyskMigrationPool:
    """Describe one reviewed pool in the one-off Rysk migration.

    Deployment blocks were resolved by archive-state binary search on
    2026-08-25. The metadata repair uses their block timestamps for accurate
    first-seen values, while the history migration uses the blocks as its
    address-specific lower boundaries.
    """

    #: EVM chain containing the pool.
    chain_id: int
    #: Pool and ERC-20 LP share-token address.
    address: HexAddress
    #: Inclusive proxy deployment block.
    deployment_block: int


#: Exact public Rysk Premium scope reviewed against the application catalogue
#: on 2026-08-25. Issuer-labelled internal pools are deliberately absent.
RYSK_MIGRATION_POOLS: tuple[RyskMigrationPool, ...] = (
    RyskMigrationPool(1, HexAddress("0x7b258f15a5b981f97eca4794bdeedd3aa24ea423"), 25_503_534),
    RyskMigrationPool(1, HexAddress("0x06e6bc81c15a5d73fc35b79ff67ff57d258d77c8"), 25_588_359),
    RyskMigrationPool(1, HexAddress("0x1195826418541cb3e80a22ef5736a6794393c91a"), 25_596_788),
    RyskMigrationPool(1, HexAddress("0xce930ac025cc5675ec49cba71cc5ed0c7518c19a"), 25_595_913),
    RyskMigrationPool(999, HexAddress("0x0fe45639d2d4f8c3c999946a44c287fcff5fa541"), 28_694_205),
    RyskMigrationPool(999, HexAddress("0xd1ee594e67ef8e09903961d735ab7ad3009522f9"), 28_783_082),
    RyskMigrationPool(999, HexAddress("0xa26801f689fbdf0ff96eff52077b958d1062ba85"), 36_242_300),
    RyskMigrationPool(999, HexAddress("0xca5b1d5d204c6a69f91d643332f4d3a0cfb2bc50"), 43_148_959),
)

#: Fixed migration order and RPC coverage.
RYSK_MIGRATION_CHAIN_IDS: tuple[int, ...] = tuple(dict.fromkeys(pool.chain_id for pool in RYSK_MIGRATION_POOLS))


def iter_rysk_migration_pools(chain_id: int | None = None) -> Iterator[RyskMigrationPool]:
    """Iterate the reviewed one-off migration targets.

    The filter is an internal composition helper, not an operator-selectable
    migration argument. Both production entry points always iterate all
    reviewed chains.

    :param chain_id:
        Optional chain filter used by one stage of the fixed migration.
    :return:
        Reviewed targets in deterministic chain and declaration order.
    """

    return (pool for pool in RYSK_MIGRATION_POOLS if chain_id is None or pool.chain_id == chain_id)


def parse_rysk_migration_dry_run(value: str | None) -> bool:
    """Parse the sole migration-specific operator choice.

    The migration defaults to the safe, non-persistent mode. Invalid values
    fail before any production state is read or written.

    :param value:
        Raw ``DRY_RUN`` environment value.
    :return:
        ``True`` for dry-run mode and ``False`` for the persistent run.
    :raises ValueError:
        If the value is not a recognised boolean literal.
    """

    if value is None:
        return True
    normalised = value.strip().lower()
    if normalised in {"1", "true", "yes"}:
        return True
    if normalised in {"0", "false", "no"}:
        return False
    raise ValueError(f"DRY_RUN must be true or false, got {value!r}")
