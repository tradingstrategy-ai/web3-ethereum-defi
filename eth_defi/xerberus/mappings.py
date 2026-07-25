"""Mapping between our vault protocol slugs and Xerberus protocol entity ids.

Pools match by ``(chain_id, address)`` and need no mapping. Protocol-level
fallback and the top-level ``xerberus_protocols`` export use this table.

Unmapped protocols (value ``None``) have no known Xerberus equivalent.

Update after a successful registry scan by reviewing
``scripts/xerberus/update-xerberus-mappings.py`` output.
"""

#: Mapping from our vault protocol slug to Xerberus protocol ``entity_id``.
#:
#: - ``None`` means no Xerberus equivalent is known.
#: - A string value is the Xerberus registry protocol id from
#:   ``GET /registry/scores?type=protocol``.
#: - Each mapping has a comment with the date it was confirmed.
XERBERUS_PROTOCOL_MAPPINGS: dict[str, str | None] = {
    # 2026-07-25 — live registry scan entity_id match
    "morpho": "morpho-v1",
    # 2026-07-25 — live registry (Morpho V2 also exists as morpho-v2)
    "spark": "spark-v1",
    # 2026-07-25 — live registry
    "ipor-fusion": "ipor-fusion-v1",
    # 2026-07-25 — live registry
    "euler": "euler-v2",
    # 2026-07-25 — live registry
    "fluid": "fluid-v1",
    # 2026-07-25 — live registry
    "maple": "maple-v2",
    # 2026-07-25 — live registry
    "lagoon-finance": "lagoon-v0",
    # 2026-07-25 — live registry
    "ethena": "ethena-v1",
    # 2026-07-25 — live registry (entity_id equals name slug)
    "d2-finance": "d2-finance",
    # 2026-07-25 — live registry
    "frankencoin": "frankencoin",
    # 2026-07-25 — live registry
    "midas": "midas-v1",
    # 2026-07-25 — live registry
    "gearbox": "gearbox-v3",
    # 2026-07-25 — live registry (our metadata slug is silo-finance)
    "silo-finance": "silo-v2",
    # 2026-07-25 — live registry
    "gmx": "gmx-v2",
    # 2026-07-25 — live registry
    "dolomite": "dolomite",
    # 2026-07-25 — live registry
    "ostium": "ostium",
    # 2026-07-25 — live registry
    "altura": "altura-v1",
    # 2026-07-25 — live registry
    "ember": "ember-v1",
    # 2026-07-25 — live registry
    "securitize": "securitize-v1",
    # 2026-07-25 — live registry
    "spiko": "spiko",
    # 2026-07-25 — no Yearn entry in Xerberus registry at first scan
    "yearn": None,
}


def reverse_xerberus_protocol_mappings() -> dict[str, str]:
    """Build Xerberus entity id → our protocol slug reverse map.

    :return:
        Reverse mapping for non-null forward entries.
    """
    return {v: k for k, v in XERBERUS_PROTOCOL_MAPPINGS.items() if v is not None}
