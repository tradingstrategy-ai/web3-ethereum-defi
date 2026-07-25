"""Mapping between our vault protocol slugs and Xerberus protocol entity ids.

Pools match by ``(chain_id, address)`` and need no mapping. Protocol-level
fallback and the top-level ``xerberus_protocols`` export use this table.

Unmapped protocols (value ``None``) have no known Xerberus equivalent after
an agent review of the live registry. Missing keys are treated as unmapped
by callers that use ``.get(slug)``.

**Do not** invent mappings with regex, normalised string equality, or
fuzzy name matching. Xerberus entity ids (``morpho-v1``, ``spark-savings-v2``,
``usd-ai``, …) are curated identifiers; agents must inspect registry rows
(and product identity) before editing this table. See
``README-xerberus.md`` (Protocol mappings).
"""

#: Mapping from our vault protocol slug to Xerberus protocol ``entity_id``.
#:
#: - ``None`` means the registry was inspected and no Xerberus equivalent is known.
#: - A string value is the Xerberus registry protocol id from
#:   ``GET /registry/scores?type=protocol``.
#: - Each mapping has a comment with the date and how it was confirmed.
#:
#: Reviewed against live registry 2026-07-25 (50 protocol entities).
XERBERUS_PROTOCOL_MAPPINGS: dict[str, str | None] = {
    # --- Matched (agent-inspected registry entity_id / product name) ---
    # 2026-07-25 — registry ``aave-v3`` / Aave v3
    "aave": "aave-v3",
    # 2026-07-25 — registry ``altura-v1`` / Altura
    "altura": "altura-v1",
    # 2026-07-25 — registry ``cap-v1`` / Cap
    "cap": "cap-v1",
    # 2026-07-25 — registry ``d2-finance`` / D2 Finance
    "d2-finance": "d2-finance",
    # 2026-07-25 — registry ``dolomite`` / Dolomite
    "dolomite": "dolomite",
    # 2026-07-25 — registry ``ember-v1`` / Ember Protocol
    "ember": "ember-v1",
    # 2026-07-25 — registry ``ethena-v1`` / Ethena
    "ethena": "ethena-v1",
    # 2026-07-25 — registry ``euler-v2`` / Euler v2
    "euler": "euler-v2",
    # 2026-07-25 — registry ``fluid-v1`` / Fluid
    "fluid": "fluid-v1",
    # 2026-07-25 — registry ``frankencoin`` / Frankencoin
    "frankencoin": "frankencoin",
    # 2026-07-25 — registry ``gearbox-v3`` / Gearbox v3
    "gearbox": "gearbox-v3",
    # 2026-07-25 — registry ``infinifi-v1`` / infiniFi v1
    "infinifi": "infinifi-v1",
    # 2026-07-25 — registry ``ipor-fusion-v1`` / IPOR Fusion v1
    "ipor-fusion": "ipor-fusion-v1",
    # 2026-07-25 — registry ``lagoon-v0`` / Lagoon Finance v0
    "lagoon-finance": "lagoon-v0",
    # 2026-07-25 — registry ``maple-v2`` / Maple Finance v2
    "maple": "maple-v2",
    # 2026-07-25 — registry ``midas-v1`` / Midas
    "midas": "midas-v1",
    # 2026-07-25 — registry ``morpho-v1`` / Morpho (primary; ``morpho-v2`` also listed)
    "morpho": "morpho-v1",
    # 2026-07-25 — registry ``ostium`` / Ostium
    "ostium": "ostium",
    # 2026-07-25 — registry ``royco-dawn-v1`` / Royco Dawn
    "royco": "royco-dawn-v1",
    # 2026-07-25 — registry ``securitize-v1`` / Securitize
    "securitize": "securitize-v1",
    # 2026-07-25 — registry ``silo-v2`` / Silo v2 (our metadata slug is silo-finance)
    "silo-finance": "silo-v2",
    # 2026-07-25 — registry ``spark-v1`` / Spark v1 (distinct from ``spark-savings-v2``)
    "spark": "spark-v1",
    # 2026-07-25 — registry ``spiko`` / Spiko
    "spiko": "spiko",
    # 2026-07-25 — registry ``usd-ai`` / USD.ai (our slug usdai)
    "usdai": "usd-ai",
    # --- Explicitly no match after registry review ---
    # 2026-07-25 — no Yearn entity among 50 registry protocols
    "yearn": None,
}

#: Xerberus registry protocol ``entity_id`` values we intentionally do **not**
#: map to any of our vault protocol metadata slugs, with the reason.
#:
#: Keys must be entity ids from ``GET /registry/scores?type=protocol`` that are
#: absent from :py:data:`XERBERUS_PROTOCOL_MAPPINGS` values. Revisit when we add
#: a matching vault protocol YAML or Xerberus renames the entity.
#:
#: Reviewed 2026-07-25 against 50 registry protocols (24 mapped, 26 unmapped).
XERBERUS_UNMAPPED_PROTOCOL_REASONS: dict[str, str] = {
    # --- Related to a mapped protocol (secondary Xerberus listing) ---
    "morpho-v2": ("Secondary Morpho listing (Morpho V2); our slug morpho maps to morpho-v1 as the primary Morpho registry score."),
    "spark-savings-v2": ("Spark Savings product line; our slug spark maps to spark-v1 (Spark Lend / core), not the savings-specific entity."),
    # --- Infrastructure, bridges, oracles (not our vault protocol taxonomy) ---
    "across-v3": "Cross-chain bridge; we have no Across vault-protocol metadata slug.",
    "chainlink": "Oracle / data infrastructure; not a vault protocol in our metadata set.",
    "eigenlayer": "Restaking infrastructure; we have no EigenLayer vault-protocol metadata slug.",
    "hop-protocol": "Cross-chain bridge; we have no Hop vault-protocol metadata slug.",
    "layerzero-v2": "Messaging / interoperability infrastructure; not a vault protocol slug we track.",
    "polygon-pos-bridge": "Chain bridge infrastructure; not a vault protocol in our metadata set.",
    "stargate-v2": "Cross-chain bridge; we have no Stargate vault-protocol metadata slug.",
    "wormhole": "Cross-chain messaging / bridge; we have no Wormhole vault-protocol metadata slug.",
    # --- Liquid staking / LRT style (no matching vault protocol YAML) ---
    "kelp": "Kelp liquid restaking; we have no kelp vault-protocol metadata slug.",
    "lido-v2": "Lido liquid staking; we have no lido vault-protocol metadata slug.",
    # --- DeFi protocols without a matching our slug / YAML ---
    "circle": ("Circle issuer entity; our circle-usyc / usyc metadata is the USYC product, not generic Circle, so we do not map either slug to this registry row."),
    "compound-v3": "Compound v3; we have no compound vault-protocol metadata slug.",
    "convex-v1": "Convex Finance; we have no convex vault-protocol metadata slug.",
    "curve-v2": ("Curve Finance; we have no curve vault-protocol metadata slug (llama-lend is a different product and must not be aliased here)."),
    "digift-v1": "DigiFT; we have no digift vault-protocol metadata slug.",
    "ebisu-v1": "Ebisu Finance; we have no ebisu vault-protocol metadata slug.",
    "gmx-v2": "GMX V2; we have no gmx vault-protocol metadata YAML in this repository.",
    "knox-finance": "Knox; we have no knox vault-protocol metadata slug.",
    "makina-finance": "Makina Finance; we have no makina vault-protocol metadata slug.",
    "moonwell-v1": "Moonwell; we have no moonwell vault-protocol metadata slug.",
    "pendle-v2": "Pendle v2; we have no pendle vault-protocol metadata slug.",
    "reservoir-v1": "Reservoir; we have no reservoir vault-protocol metadata slug.",
    "stake-dao-v2": "Stake DAO v2; we have no stake-dao vault-protocol metadata slug.",
    "strata-v1": "Strata; we have no strata vault-protocol metadata slug.",
}


def reverse_xerberus_protocol_mappings() -> dict[str, str]:
    """Build Xerberus entity id → our protocol slug reverse map.

    :return:
        Reverse mapping for non-null forward entries.
    """
    return {v: k for k, v in XERBERUS_PROTOCOL_MAPPINGS.items() if v is not None}
