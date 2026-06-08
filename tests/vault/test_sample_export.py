"""Test sample export core3_protocols filtering."""

import json
from pathlib import Path

from eth_defi.vault.sample_export import generate_sample_json


def test_sample_json_filters_core3_protocols(tmp_path: Path):
    """Sample JSON export filters core3_protocols to only Ethereum protocol slugs.

    1. Create a source JSON with Ethereum and non-Ethereum vaults plus core3_protocols
    2. Generate sample JSON (Ethereum-only filter)
    3. Assert only protocol slugs present in Ethereum vaults are kept in core3_protocols
    """
    # 1. Create source JSON with mixed chains and core3_protocols
    source_data = {
        "generated_at": "2026-06-08T00:00:00Z",
        "core3_protocols": {
            "morpho": {"slug": "morpho", "name": "Morpho", "pol": {"score": 32.0}},
            "fluid": {"slug": "instadapp", "name": "Fluid", "pol": {"score": 45.0}},
            "gains-network": {"slug": "gains-network", "name": "Gains", "pol": {"score": 60.0}},
        },
        "vaults": [
            {"chain_id": 1, "protocol_slug": "morpho", "name": "Morpho Vault A"},
            {"chain_id": 1, "protocol_slug": "morpho", "name": "Morpho Vault B"},
            {"chain_id": 1, "protocol_slug": "fluid", "name": "Fluid Vault"},
            {"chain_id": 42161, "protocol_slug": "gains-network", "name": "Gains Vault on Arbitrum"},
        ],
    }

    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps(source_data), encoding="utf-8")

    output_path = tmp_path / "sample.json"

    # 2. Generate sample (filters to chain_id=1 only)
    count = generate_sample_json(source_path, output_path)

    # 3. Read and verify
    result = json.loads(output_path.read_text(encoding="utf-8"))

    assert count == 3
    assert len(result["vaults"]) == 3

    # core3_protocols should only have morpho and fluid (Ethereum vaults)
    assert "morpho" in result["core3_protocols"]
    assert "fluid" in result["core3_protocols"]
    # gains-network is Arbitrum only — should be excluded
    assert "gains-network" not in result["core3_protocols"]

    assert result["generated_at"] == "2026-06-08T00:00:00Z"
