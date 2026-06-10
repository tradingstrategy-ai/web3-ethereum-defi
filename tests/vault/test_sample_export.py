"""Test sample export core3_protocols and curators filtering."""

import json
from pathlib import Path

from eth_defi.vault.sample_export import generate_sample_json


def test_sample_json_filters_core3_protocols_and_curators(tmp_path: Path):
    """Sample JSON export filters core3_protocols and curators to Ethereum-only slugs.

    1. Create a source JSON with Ethereum and non-Ethereum vaults plus core3_protocols, curators and provenance metadata
    2. Generate sample JSON (Ethereum-only filter)
    3. Assert only protocol slugs present in Ethereum vaults are kept in core3_protocols
    4. Assert only curator slugs present in Ethereum vaults are kept in curators
    5. Assert the exporter version stamp metadata is carried over unchanged
    """
    # 1. Create source JSON with mixed chains, core3_protocols, curators and provenance metadata
    source_data = {
        "generated_at": "2026-06-08T00:00:00Z",
        "metadata": {
            "version": {
                "tag": "v0.31",
                "commit_message": "feat: stamp version",
                "commit_hash": "4cea3aa3deadbeef",
            },
        },
        "core3_protocols": {
            "morpho": {"slug": "morpho", "name": "Morpho", "pol": {"score": 32.0}},
            "fluid": {"slug": "instadapp", "name": "Fluid", "pol": {"score": 45.0}},
            "gains-network": {"slug": "gains-network", "name": "Gains", "pol": {"score": 60.0}},
        },
        "curators": {
            "gauntlet": {
                "slug": "gauntlet",
                "name": "Gauntlet",
                "short_description": "Gauntlet short description.",
                "long_description": "Gauntlet long description.",
                "recent_posts": [],
            },
            "re7-labs": {
                "slug": "re7-labs",
                "name": "RE7 Labs",
                "short_description": "RE7 Labs short description.",
                "long_description": "RE7 Labs long description.",
                "recent_posts": [],
            },
            "gains-network": {
                "slug": "gains-network",
                "name": "Gains Network",
                "short_description": "Gains Network short description.",
                "long_description": "Gains Network long description.",
                "recent_posts": [],
            },
        },
        "vaults": [
            {"chain_id": 1, "protocol_slug": "morpho", "curator_slug": "gauntlet", "name": "Morpho Vault A"},
            {"chain_id": 1, "protocol_slug": "morpho", "curator_slug": "gauntlet", "name": "Morpho Vault B"},
            {"chain_id": 1, "protocol_slug": "fluid", "curator_slug": "re7-labs", "name": "Fluid Vault"},
            {"chain_id": 42161, "protocol_slug": "gains-network", "curator_slug": "gains-network", "name": "Gains Vault on Arbitrum"},
        ],
    }

    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps(source_data), encoding="utf-8")

    output_path = tmp_path / "sample.json"

    # 2. Generate sample (filters to chain_id=1 only)
    count = generate_sample_json(source_path, output_path)

    # 3. Read and verify
    result = json.loads(output_path.read_text(encoding="utf-8"))

    expected_ethereum_vault_count = 3
    assert count == expected_ethereum_vault_count
    assert len(result["vaults"]) == expected_ethereum_vault_count

    # core3_protocols should only have morpho and fluid (Ethereum vaults)
    assert "morpho" in result["core3_protocols"]
    assert "fluid" in result["core3_protocols"]
    # gains-network is Arbitrum only — should be excluded
    assert "gains-network" not in result["core3_protocols"]

    # 4. curators should only have gauntlet and re7-labs (Ethereum vaults)
    assert "gauntlet" in result["curators"]
    assert "re7-labs" in result["curators"]
    assert result["curators"]["gauntlet"]["short_description"] == "Gauntlet short description."
    assert result["curators"]["gauntlet"]["long_description"] == "Gauntlet long description."
    assert result["curators"]["re7-labs"]["short_description"] == "RE7 Labs short description."
    assert result["curators"]["re7-labs"]["long_description"] == "RE7 Labs long description."
    # gains-network curator is Arbitrum only — should be excluded
    assert "gains-network" not in result["curators"]

    assert result["generated_at"] == "2026-06-08T00:00:00Z"

    # 5. Provenance metadata is carried over so the sample matches the full export shape
    assert result["metadata"] == {
        "version": {
            "tag": "v0.31",
            "commit_message": "feat: stamp version",
            "commit_hash": "4cea3aa3deadbeef",
        },
    }
