"""Tests for the canonical-feeder-id alias system.

Verifies that alias YAML files delegate feed collection to a
canonical feeder, produce no tracked sources, and that metadata
inheritance works correctly in curator exports.
"""

from pathlib import Path

import pytest

from eth_defi.feed.sources import (
    FEEDS_DATA_DIR,
    load_feeder_metadata,
    load_post_sources,
)


def _write_yaml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_alias_loading(tmp_path: Path):
    """Alias YAML delegates to canonical feeder, produces no sources, and works across roles.

    1. Write a canonical stablecoin YAML with a twitter source.
    2. Write a same-role alias pointing to it.
    3. Write a cross-role alias (curator) pointing to a protocol feeder with the same slug.
    4. Call load_post_sources().
    5. Assert only canonical feeders' sources are returned.
    6. Assert both aliases appear in the aliases list.
    7. Assert feeders_skipped does not count aliases.
    """

    # 1. Canonical stablecoin feeder
    _write_yaml(
        tmp_path / "stablecoins" / "usdt.yaml",
        "feeder-id: usdt\nname: Tether USD\nrole: stablecoin\ntwitter: tether\n",
    )

    # 2. Same-role alias
    _write_yaml(
        tmp_path / "stablecoins" / "usdt-e.yaml",
        "feeder-id: usdt-e\nname: Bridged USDT\nrole: stablecoin\ncanonical-feeder-id: usdt\n",
    )

    # 3. Cross-role: protocol is the real feeder, curator is an alias with same slug
    _write_yaml(
        tmp_path / "protocols" / "yo.yaml",
        "feeder-id: yo\nname: Yo\nrole: protocol\ntwitter: yieldprotocol\n",
    )
    _write_yaml(
        tmp_path / "curators" / "yo.yaml",
        "feeder-id: yo\nname: Yo\nrole: curator\ncanonical-feeder-id: yo\n",
    )

    # 4. Load
    sources, skipped, aliases = load_post_sources(tmp_path)

    # 5. Only canonical feeders' sources (usdt + yo protocol)
    source_ids = {s.feeder_id for s in sources}
    assert source_ids == {"usdt", "yo"}

    # 6. Both aliases present
    alias_map = {(a.feeder_id, a.role): a.canonical_feeder_id for a in aliases}
    assert alias_map[("usdt-e", "stablecoin")] == "usdt"
    assert alias_map[("yo", "curator")] == "yo"

    # 7. No aliases counted as skipped
    assert skipped == 0


def test_alias_validation_errors(tmp_path: Path):
    """Alias validation rejects mutual exclusion violations, missing targets, and chains.

    1. Assert alias with feed source fields (twitter) raises ValueError.
    2. Assert alias pointing to nonexistent feeder raises ValueError.
    3. Assert alias-to-alias chain raises ValueError.
    """

    # 1. Mutual exclusion: canonical-feeder-id + twitter
    _write_yaml(
        tmp_path / "stablecoins" / "usdt.yaml",
        "feeder-id: usdt\nname: Tether USD\nrole: stablecoin\ntwitter: tether\n",
    )
    _write_yaml(
        tmp_path / "stablecoins" / "bad-alias.yaml",
        "feeder-id: bad-alias\nname: Bad\nrole: stablecoin\ncanonical-feeder-id: usdt\ntwitter: tether\n",
    )

    with pytest.raises(ValueError, match="canonical-feeder-id.*twitter"):
        load_post_sources(tmp_path)

    # Clean up for next sub-test
    (tmp_path / "stablecoins" / "bad-alias.yaml").unlink()

    # 2. Missing target
    _write_yaml(
        tmp_path / "stablecoins" / "ghost-alias.yaml",
        "feeder-id: ghost-alias\nname: Ghost\nrole: stablecoin\ncanonical-feeder-id: nonexistent\n",
    )

    with pytest.raises(ValueError, match="nonexistent.*does not match any known feeder-id"):
        load_post_sources(tmp_path)

    (tmp_path / "stablecoins" / "ghost-alias.yaml").unlink()

    # 3. Alias-to-alias chain: a -> b -> c
    _write_yaml(
        tmp_path / "stablecoins" / "c.yaml",
        "feeder-id: c\nname: Real C\nrole: stablecoin\ntwitter: realc\n",
    )
    _write_yaml(
        tmp_path / "stablecoins" / "b.yaml",
        "feeder-id: b\nname: Alias B\nrole: stablecoin\ncanonical-feeder-id: c\n",
    )
    _write_yaml(
        tmp_path / "stablecoins" / "a.yaml",
        "feeder-id: a\nname: Alias A\nrole: stablecoin\ncanonical-feeder-id: b\n",
    )

    with pytest.raises(ValueError, match="alias.*chains are not allowed"):
        load_post_sources(tmp_path)


def test_real_yaml_files_load_without_errors():
    """All real repository YAML files load successfully including aliases.

    1. Load from the real FEEDS_DATA_DIR.
    2. Assert no exceptions.
    3. Assert specific known aliases are present.
    """

    sources, skipped, aliases = load_post_sources(FEEDS_DATA_DIR)
    assert len(sources) > 0
    assert len(aliases) > 0

    alias_map = {(a.feeder_id, a.role): a.canonical_feeder_id for a in aliases}

    # Same-role stablecoin aliases
    assert alias_map[("usdt-e", "stablecoin")] == "usdt"
    assert alias_map[("usdc-e", "stablecoin")] == "usdc"
    assert alias_map[("sfrax", "stablecoin")] == "frax"

    # Cross-role curator -> stablecoin
    assert alias_map[("ethena", "curator")] == "usde"

    # Cross-role curator -> protocol
    assert alias_map[("spark", "curator")] == "spark"
    assert alias_map[("ipor", "curator")] == "ipor-fusion"

    # Cross-role protocol -> stablecoin (sbold dedup)
    assert alias_map[("sbold", "protocol")] == "sbold"


def test_other_links_metadata_loads(tmp_path: Path):
    """Supporting feeder links are accepted and normalised.

    1. Create a curator YAML with ``other-links`` evidence.
    2. Load the metadata through the shared feeder loader.
    3. Assert the link title is preserved and the URL is normalised.
    """

    yaml_path = tmp_path / "curators" / "flowdesk.yaml"
    _write_yaml(
        yaml_path,
        "feeder-id: flowdesk\nname: Flowdesk\nrole: curator\ntwitter: flowdesk_co\nother-links:\n  - title: Morpho forum evidence\n    url: https://forum.morpho.org/t/announcing-flowdesk-ausd-rwa-strategy/2213\n",
    )

    metadata = load_feeder_metadata(yaml_path)

    assert metadata["other-links"] == [
        {
            "title": "Morpho forum evidence",
            "url": "https://forum.morpho.org/t/announcing-flowdesk-ausd-rwa-strategy/2213",
        }
    ]


def test_metadata_inheritance_in_curator_export(tmp_path: Path):
    """Curator metadata export inherits fields from canonical stablecoin feeder.

    1. Create stablecoins/usde.yaml with website, twitter, linkedin.
    2. Create curators/ethena.yaml as alias pointing to usde.
    3. Call build_curator_metadata_json().
    4. Assert website, twitter, linkedin are inherited (not None).
    5. Create a second alias whose canonical has no linkedin — assert linkedin is None.
    """

    from eth_defi.vault.curator import build_curator_metadata_json

    # Full metadata canonical
    _write_yaml(
        tmp_path / "stablecoins" / "usde.yaml",
        "feeder-id: usde\nname: Ethena USDe\nrole: stablecoin\nwebsite: https://ethena.fi/\ntwitter: ethena\nlinkedin: ethena-labs\n",
    )
    _write_yaml(
        tmp_path / "curators" / "ethena.yaml",
        "feeder-id: ethena\nname: Ethena\nrole: curator\ncanonical-feeder-id: usde\n",
    )

    metadata = build_curator_metadata_json(tmp_path / "curators" / "ethena.yaml")
    assert metadata["slug"] == "ethena"
    assert metadata["name"] == "Ethena"
    assert metadata["website"] == "https://ethena.fi/"
    assert metadata["twitter"] == "https://x.com/ethena"
    assert metadata["linkedin"] == "https://www.linkedin.com/company/ethena-labs"
    assert metadata["canonical_feeder_id"] == "usde"

    # 5. Partial metadata canonical — no linkedin field
    _write_yaml(
        tmp_path / "stablecoins" / "abc.yaml",
        "feeder-id: abc\nname: ABC Token\nrole: stablecoin\nwebsite: https://abc.xyz/\ntwitter: abctoken\n",
    )
    _write_yaml(
        tmp_path / "curators" / "abc-curator.yaml",
        "feeder-id: abc-curator\nname: ABC Curator\nrole: curator\ncanonical-feeder-id: abc\n",
    )

    meta2 = build_curator_metadata_json(tmp_path / "curators" / "abc-curator.yaml")
    assert meta2["twitter"] == "https://x.com/abctoken"
    assert meta2["linkedin"] is None
