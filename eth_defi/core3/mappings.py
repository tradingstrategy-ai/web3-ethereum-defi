"""Mapping between our vault protocol slugs and Core3 project slugs.

Our vault protocol metadata lives in ``eth_defi/data/vaults/metadata/*.yaml``
and uses its own slug system (e.g. ``ipor-fusion``, ``lagoon-finance``).

Core3 uses CoinGecko-style slugs (e.g. ``instadapp`` for Fluid,
``syrup`` for Maple). This module provides the canonical mapping
between the two.

Unmapped protocols (value ``None``) have no known Core3 equivalent.

To update this mapping, run::

    poetry run python scripts/core3/update-core3-mappings.py

See :doc:`README-core3` for details.
"""

#: Mapping from our vault protocol slug to Core3 project slug.
#:
#: - ``None`` means no Core3 equivalent is known.
#: - A string value is the Core3 slug for that protocol.
#: - Each mapping has a comment with the date it was added and
#:   how it was identified.
CORE3_MAPPINGS: dict[str, str | None] = {
    # 2026-06-03 — exact slug match in Core3 database
    "ethena": "ethena",
    # 2026-06-03 — exact slug match in Core3 database
    "euler": "euler",
    # 2026-06-03 — exact slug match in Core3 database
    "dolomite": "dolomite",
    # 2026-06-03 — exact slug match in Core3 database
    "gains-network": "gains-network",
    # 2026-06-03 — exact slug match in Core3 database
    "gearbox": "gearbox",
    # 2026-06-03 — exact slug match in Core3 database
    "harvest-finance": "harvest-finance",
    # 2026-06-03 — exact slug match in Core3 database
    "inverse-finance": "inverse-finance",
    # 2026-06-03 — exact slug match in Core3 database
    "morpho": "morpho",
    # 2026-06-03 — exact slug match in Core3 database
    "resolv": "resolv",
    # 2026-06-03 — exact slug match in Core3 database
    "sky": "sky",
    # 2026-06-03 — exact slug match in Core3 database
    "superform": "superform",
    # 2026-06-03 — exact slug match in Core3 database
    "truefi": "truefi",
    # 2026-06-03 — exact slug match in Core3 database
    "lighter": "lighter",
    # 2026-06-03 — exact slug match in Core3 database
    "altura": "altura",
    # 2026-06-03 — exact slug match in Core3 database
    "hyperliquid": "hyperliquid",
    # 2026-06-03 — Core3 uses "centrifuge-2" (CoinGecko slug convention)
    "centrifuge": "centrifuge-2",
    # 2026-06-03 — Core3 lists Fluid under its original name Instadapp
    "fluid": "instadapp",
    # 2026-06-03 — Core3 uses the Frax Share token slug
    "frax-finance": "frax-share",
    # 2026-06-03 — Core3 uses the Maple Finance rebranded token slug "syrup"
    "maple": "syrup",
    # 2026-06-03 — Core3 uses "spark-2" (CoinGecko slug convention)
    "spark": "spark-2",
    # 2026-06-03 — Core3 uses "yearn-finance" (CoinGecko slug convention)
    "yearn": "yearn-finance",
}
