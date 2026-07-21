"""Tests for the canonical-feeder-id alias system.

Verifies that alias YAML files delegate feed collection to a
canonical feeder, produce no tracked sources, and that metadata
inheritance works correctly in curator exports.
"""

import logging
from pathlib import Path

import pytest

from eth_defi.feed.sources import (
    FEEDS_DATA_DIR,
    load_feeder_metadata,
    load_post_sources,
)
from eth_defi.vault import curator as curator_module


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
    assert alias_map["usdt-e", "stablecoin"] == "usdt"
    assert alias_map["yo", "curator"] == "yo"

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

    with pytest.raises(ValueError, match=r"canonical-feeder-id.*twitter"):
        load_post_sources(tmp_path)

    # Clean up for next sub-test
    (tmp_path / "stablecoins" / "bad-alias.yaml").unlink()

    # 2. Missing target
    _write_yaml(
        tmp_path / "stablecoins" / "ghost-alias.yaml",
        "feeder-id: ghost-alias\nname: Ghost\nrole: stablecoin\ncanonical-feeder-id: nonexistent\n",
    )

    with pytest.raises(ValueError, match=r"nonexistent.*does not match any known feeder-id"):
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

    with pytest.raises(ValueError, match=r"alias.*chains are not allowed"):
        load_post_sources(tmp_path)


def test_real_yaml_files_load_without_errors():
    """All real repository YAML files load successfully including aliases.

    1. Load from the real FEEDS_DATA_DIR.
    2. Assert no exceptions.
    3. Assert specific known aliases are present.
    """

    sources, _skipped, aliases = load_post_sources(FEEDS_DATA_DIR)
    assert len(sources) > 0
    assert len(aliases) > 0

    alias_map = {(a.feeder_id, a.role): a.canonical_feeder_id for a in aliases}

    # Same-role stablecoin aliases
    assert alias_map["usdt-e", "stablecoin"] == "usdt"
    assert alias_map["usdc-e", "stablecoin"] == "usdc"
    assert alias_map["sfrax", "stablecoin"] == "frax"
    assert alias_map["aegis-yusd", "stablecoin"] == "aegis"
    assert alias_map["jusd", "stablecoin"] == "aegis"

    # Cross-role curator -> stablecoin
    assert alias_map["ethena", "curator"] == "usde"

    # Cross-role curator -> protocol
    assert alias_map["spark", "curator"] == "spark"
    assert alias_map["ipor", "curator"] == "ipor-fusion"

    # Cross-role protocol -> stablecoin (sbold dedup)
    assert alias_map["sbold", "protocol"] == "sbold"


def test_other_links_metadata_loads(tmp_path: Path):
    """Supporting feeder metadata is accepted and normalised.

    1. Create a curator YAML with ``other-links`` evidence.
    2. Load the metadata through the shared feeder loader.
    3. Assert descriptions are preserved.
    4. Assert the link title is preserved and the URL is normalised.
    """

    yaml_path = tmp_path / "curators" / "flowdesk.yaml"
    _write_yaml(
        yaml_path,
        "feeder-id: flowdesk\nname: Flowdesk\nrole: curator\nipor-atomist: Flowdesk Labs\ncuratorwatch: https://curatorwatch.com/curator/flowdesk\nshort_description: Flowdesk is an institutional market maker.\nlong_description: |\n  Flowdesk provides market making and liquidity services.\ntwitter: flowdesk_co\nother-links:\n  - title: Morpho forum evidence\n    url: https://forum.morpho.org/t/announcing-flowdesk-ausd-rwa-strategy/2213\n",
    )

    metadata = load_feeder_metadata(yaml_path)

    assert metadata["short_description"] == "Flowdesk is an institutional market maker."
    assert metadata["long_description"] == "Flowdesk provides market making and liquidity services."
    assert metadata["ipor-atomist"] == "Flowdesk Labs"
    assert metadata["curatorwatch"] == "https://curatorwatch.com/curator/flowdesk"
    assert metadata["other-links"] == [
        {
            "title": "Morpho forum evidence",
            "url": "https://forum.morpho.org/t/announcing-flowdesk-ausd-rwa-strategy/2213",
        }
    ]


def test_metadata_inheritance_in_curator_export(tmp_path: Path):
    """Curator metadata export inherits source fields from canonical stablecoin feeder.

    1. Create stablecoins/usde.yaml with website, twitter, linkedin.
    2. Create curators/ethena.yaml as alias pointing to usde, with curator descriptions.
    3. Call build_curator_metadata_json().
    4. Assert website, twitter, linkedin are inherited (not None).
    5. Assert descriptions come from the curator alias YAML.
    6. Create a second alias whose canonical has no linkedin — assert linkedin is None.
    """

    # Full metadata canonical
    _write_yaml(
        tmp_path / "stablecoins" / "usde.yaml",
        "feeder-id: usde\nname: Ethena USDe\nrole: stablecoin\nwebsite: https://ethena.fi/\nshort_description: USDe is a synthetic dollar.\nlong_description: |\n  USDe is a stablecoin product.\ntwitter: ethena\nlinkedin: ethena-labs\n",
    )
    _write_yaml(
        tmp_path / "curators" / "ethena.yaml",
        "feeder-id: ethena\nname: Ethena\nrole: curator\ncanonical-feeder-id: usde\nshort_description: Ethena is a synthetic dollar protocol team.\nlong_description: |\n  Ethena operates the protocol behind USDe and related products.\n",
    )

    metadata = curator_module.build_curator_metadata_json(tmp_path / "curators" / "ethena.yaml")
    assert metadata["slug"] == "ethena"
    assert metadata["name"] == "Ethena"
    assert metadata["website"] == "https://ethena.fi/"
    assert metadata["short_description"] == "Ethena is a synthetic dollar protocol team."
    assert metadata["long_description"] == "Ethena operates the protocol behind USDe and related products."
    assert metadata["twitter"] == "https://x.com/ethena"
    assert metadata["linkedin"] == "https://www.linkedin.com/company/ethena-labs"
    assert metadata["logos"] == {"generic": None, "dark": None, "light": None}
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

    meta2 = curator_module.build_curator_metadata_json(tmp_path / "curators" / "abc-curator.yaml")
    assert meta2["twitter"] == "https://x.com/abctoken"
    assert meta2["linkedin"] is None


def test_curatorwatch_metadata_export_prefers_alias_value(tmp_path: Path):
    """CuratorWatch URL metadata is exported without creating a feed source.

    1. Create a canonical stablecoin feeder with one CuratorWatch URL.
    2. Create an alias curator feeder with its own CuratorWatch URL.
    3. Assert the curator export prefers the alias URL.
    4. Assert the alias still produces no tracked feed sources.
    """

    _write_yaml(
        tmp_path / "stablecoins" / "usde.yaml",
        "feeder-id: usde\nname: Ethena USDe\nrole: stablecoin\ncuratorwatch: https://curatorwatch.com/curator/usde\ntwitter: ethena\n",
    )
    _write_yaml(
        tmp_path / "curators" / "ethena.yaml",
        "feeder-id: ethena\nname: Ethena\nrole: curator\ncanonical-feeder-id: usde\ncuratorwatch: https://curatorwatch.com/curator/ethena\n",
    )

    metadata = curator_module.build_curator_metadata_json(tmp_path / "curators" / "ethena.yaml")
    sources, _skipped, aliases = load_post_sources(tmp_path)

    assert metadata["curatorwatch"] == "https://curatorwatch.com/curator/ethena"
    assert {source.feeder_id for source in sources} == {"usde"}
    assert {(alias.feeder_id, alias.role) for alias in aliases} == {("ethena", "curator")}


def test_curator_metadata_export_includes_logo_urls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Curator metadata export includes URLs for available logo variants.

    1. Create a curator YAML file.
    2. Point the curator logo directory to a temporary folder.
    3. Create ``generic`` and ``dark`` PNG variants.
    4. Assert the public metadata contains only available logo URLs.
    """

    _write_yaml(
        tmp_path / "curators" / "gauntlet.yaml",
        "feeder-id: gauntlet\nname: Gauntlet\nrole: curator\nshort_description: Gauntlet is a DeFi risk manager.\nlong_description: |\n  Gauntlet builds risk management systems.\ntwitter: gauntlet_xyz\n",
    )
    logo_dir = tmp_path / "logos" / "gauntlet"
    logo_dir.mkdir(parents=True)
    (logo_dir / "generic.png").write_bytes(b"generic")
    (logo_dir / "dark.png").write_bytes(b"dark")
    monkeypatch.setattr(curator_module, "FORMATTED_LOGOS_DIR", tmp_path / "logos")

    metadata = curator_module.build_curator_metadata_json(
        tmp_path / "curators" / "gauntlet.yaml",
        public_url="https://pub.example/",
    )

    assert metadata["logos"] == {
        "generic": "https://pub.example/curator-metadata/gauntlet/generic.png",
        "dark": "https://pub.example/curator-metadata/gauntlet/dark.png",
        "light": None,
    }
    assert metadata["short_description"] == "Gauntlet is a DeFi risk manager."
    assert metadata["long_description"] == "Gauntlet builds risk management systems."


def test_curator_metadata_uploads_logo_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Curator metadata upload includes JSON and available PNG logo variants."""

    _write_yaml(
        tmp_path / "curators" / "gauntlet.yaml",
        "feeder-id: gauntlet\nname: Gauntlet\nrole: curator\ntwitter: gauntlet_xyz\n",
    )
    logo_dir = tmp_path / "logos" / "gauntlet"
    logo_dir.mkdir(parents=True)
    (logo_dir / "generic.png").write_bytes(b"generic")
    (logo_dir / "light.png").write_bytes(b"light")
    monkeypatch.setattr(curator_module, "FORMATTED_LOGOS_DIR", tmp_path / "logos")

    uploads = []

    def fake_upload_to_r2_compressed(**kwargs):
        uploads.append(kwargs)
        return True

    monkeypatch.setattr(curator_module, "upload_to_r2_compressed", fake_upload_to_r2_compressed)

    metadata = curator_module.process_and_upload_curator_metadata(
        yaml_path=tmp_path / "curators" / "gauntlet.yaml",
        bucket_name="bucket",
        endpoint_url="https://endpoint.example",
        access_key_id="key",
        secret_access_key="secret",
        public_url="https://pub.example/",
        key_prefix="test-",
    )

    assert metadata["logos"]["generic"] == "https://pub.example/curator-metadata/gauntlet/generic.png"
    assert {upload["object_name"] for upload in uploads} == {
        "curator-metadata/test-gauntlet/metadata.json",
        "curator-metadata/test-gauntlet/generic.png",
        "curator-metadata/test-gauntlet/light.png",
    }


def test_curator_upload_result_logs_are_debug(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    """Per-curator R2 upload result logs are emitted at debug level."""

    _write_yaml(
        tmp_path / "curators" / "gauntlet.yaml",
        "feeder-id: gauntlet\nname: Gauntlet\nrole: curator\ntwitter: gauntlet_xyz\n",
    )

    logo_root = tmp_path / "logos"
    curator_logo_dir = logo_root / "gauntlet"
    curator_logo_dir.mkdir(parents=True)
    (curator_logo_dir / "generic.png").write_bytes(b"generic")

    protocol_logo_dir = logo_root / "atoma"
    protocol_logo_dir.mkdir(parents=True)
    (protocol_logo_dir / "generic.png").write_bytes(b"generic")

    monkeypatch.setattr(curator_module, "CURATORS_DATA_DIR", tmp_path / "curators")
    monkeypatch.setattr(curator_module, "FORMATTED_LOGOS_DIR", logo_root)
    monkeypatch.setattr(curator_module, "PROTOCOL_CURATOR_NAMES", {"atoma": "Atoma"})

    def fake_upload_to_r2_compressed(**kwargs):
        return not kwargs["object_name"].startswith("curator-metadata/atoma/")

    monkeypatch.setattr(curator_module, "upload_to_r2_compressed", fake_upload_to_r2_compressed)

    caplog.set_level(logging.DEBUG, logger=curator_module.logger.name)

    curator_module.process_and_upload_curator_metadata(
        yaml_path=tmp_path / "curators" / "gauntlet.yaml",
        bucket_name="bucket",
        endpoint_url="https://endpoint.example",
        access_key_id="key",
        secret_access_key="secret",
        public_url="https://pub.example/",
    )
    curator_module.upload_protocol_curator_metadata(
        bucket_name="bucket",
        endpoint_url="https://endpoint.example",
        access_key_id="key",
        secret_access_key="secret",
        public_url="https://pub.example/",
    )

    curator_records = [record for record in caplog.records if record.name == curator_module.logger.name]
    curator_messages = [record.getMessage() for record in curator_records]

    assert "Uploaded curator metadata for: gauntlet" in curator_messages
    assert "Uploaded generic logo for curator: gauntlet" in curator_messages
    assert "Skipped unchanged protocol-curator metadata for: atoma" in curator_messages
    assert "Skipped unchanged generic logo for protocol-curator: atoma" in curator_messages
    assert all(record.levelno == logging.DEBUG for record in curator_records)
