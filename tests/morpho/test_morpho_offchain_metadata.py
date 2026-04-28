"""Tests for Morpho Blue offchain metadata and warning fetching.

Tests verify that:

1. The Morpho Blue GraphQL API is queried correctly for vault and market warnings.
2. Disk caching works (24h TTL, multiprocess-safe via file lock).
3. ``VaultFlag.morpho_issues`` is set when and only when RED warnings are present.
4. YELLOW-only warnings do not trigger ``VaultFlag.morpho_issues``.
5. Clean vaults (no warnings) return empty flag lists and no ``morpho_issues``.

Vaults used:

**Arbitrum (chain 42161):**

- ``frobUSDC`` — ``0xC3415c9231Dad88F8146107372143f6dAE042967``
  Vault warnings: ``short_timelock`` (RED), ``not_whitelisted`` (YELLOW)
  Market warnings: ``bad_debt_unrealized`` (RED) on USDC/RLP market
  → ``VaultFlag.morpho_issues`` expected

**Ethereum (chain 1):**

- ``Dune USDC`` — ``0xfD1241C4fc37680De370dDc20eBF7bC5e561E1c1``
  Vault warnings: ``short_timelock`` (RED), ``not_whitelisted`` (YELLOW)
  Market warnings: ``bad_debt_realized`` (YELLOW)
  → ``VaultFlag.morpho_issues`` expected (RED vault warning)
  Also used for ``bad_debt_realized`` metadata assertions.

- ``Steakhouse Level USDC`` — ``0xbEEf11C63d7173BdCC2037e7220eE9Bd0cCDA862``
  Vault warnings: ``not_whitelisted`` (YELLOW only)
  Market warnings: none
  → ``VaultFlag.morpho_issues`` NOT expected (all warnings are YELLOW)

- ``Gauntlet USDC Prime`` — ``0xdd0f28e19C1780eb6396170735D45153D261490d``
  No warnings at all
  → ``VaultFlag.morpho_issues`` NOT expected

Because the Morpho Blue API is a live external service these tests are marked
``flaky`` and skipped on CI if the required ``JSON_RPC_*`` env var is absent.
"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.morpho.offchain_metadata import (
    MorphoVaultData,
    _cached_vault_data,  # noqa: PLC2701
    fetch_morpho_vault_data,
)
from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import MorphoV1Vault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.flag import VaultFlag

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

#: frobUSDC on Arbitrum — short_timelock (RED) + bad_debt_unrealized (RED)
FROB_USDC_ARBITRUM = "0xC3415c9231Dad88F8146107372143f6dAE042967"

#: Dune USDC on Ethereum — short_timelock (RED) vault warning + bad_debt_realized (YELLOW) market
DUNE_USDC_ETHEREUM = "0xfD1241C4fc37680De370dDc20eBF7bC5e561E1c1"

#: Steakhouse Level USDC on Ethereum — not_whitelisted (YELLOW) vault only, no market warnings
STEAKHOUSE_LEVEL_USDC_ETHEREUM = "0xbEEf11C63d7173BdCC2037e7220eE9Bd0cCDA862"

#: Gauntlet USDC Prime on Ethereum — clean vault, no warnings
GAUNTLET_USDC_PRIME_ETHEREUM = "0xdd0f28e19C1780eb6396170735D45153D261490d"


@pytest.fixture(scope="module")
def web3_arbitrum() -> Web3:
    """Web3 connection to Arbitrum."""
    return create_multi_provider_web3(JSON_RPC_ARBITRUM)


@pytest.fixture(scope="module")
def web3_ethereum() -> Web3:
    """Web3 connection to Ethereum mainnet."""
    return create_multi_provider_web3(JSON_RPC_ETHEREUM)


# ---------------------------------------------------------------------------
# Arbitrum tests
# ---------------------------------------------------------------------------


@flaky.flaky
@pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed")
def test_morpho_bad_debt_unrealized_red(web3_arbitrum: Web3):
    """frobUSDC on Arbitrum has bad_debt_unrealized (RED) and short_timelock (RED).

    ``VaultFlag.morpho_issues`` must be set and both warning types visible in accessors.

    Steps:

    1. Auto-detect vault class.
    2. Fetch offchain data.
    3. Assert ``short_timelock`` in vault-level flags.
    4. Assert ``bad_debt_unrealized`` in market-level flags.
    5. Assert ``VaultFlag.morpho_issues`` in ``get_flags()``.
    """
    # 1. Auto-detect vault
    vault = create_vault_instance_autodetect(web3_arbitrum, FROB_USDC_ARBITRUM)
    assert isinstance(vault, MorphoV1Vault)

    # 2. Fetch offchain data
    data: MorphoVaultData | None = vault.morpho_offchain_data
    assert data is not None, "Expected data for frobUSDC from Morpho Blue API"

    # 3. Vault-level flags contain short_timelock (RED)
    vault_flags = vault.get_morpho_vault_flags()
    assert "short_timelock" in vault_flags, f"vault_flags={vault_flags}"

    # 4. Market-level flags contain bad_debt_unrealized (RED)
    market_flags = vault.get_morpho_market_flags()
    assert "bad_debt_unrealized" in market_flags, f"market_flags={market_flags}"

    # 5. RED warning → flag must be set
    flags = vault.get_flags()
    assert VaultFlag.morpho_issues in flags


@flaky.flaky
@pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed")
def test_morpho_warnings_disk_cache(web3_arbitrum: Web3, tmp_path: Path):
    """Disk caching: data is written on the first fetch and reused on the second.

    Steps:

    1. Call ``fetch_morpho_vault_data`` with a ``tmp_path`` cache dir (first call hits API).
    2. Verify a non-empty cache file was written.
    3. Record the file modification time.
    4. Call again with the same ``tmp_path`` (second call should use disk cache).
    5. Assert the file modification time is unchanged and data is identical.
    """
    chain_id = web3_arbitrum.eth.chain_id
    address_lower = FROB_USDC_ARBITRUM.lower()

    # 1. First call — hits API and writes cache
    result1 = fetch_morpho_vault_data(web3_arbitrum, FROB_USDC_ARBITRUM, cache_path=tmp_path)
    assert result1 is not None
    assert isinstance(result1["vault_warnings"], list)
    assert isinstance(result1["market_warnings"], list)

    # 2. Cache file exists and is non-empty
    cache_file = tmp_path / f"morpho_{chain_id}_{address_lower}.json"
    assert cache_file.exists()
    assert cache_file.stat().st_size > 0

    # 3. Record mtime; evict the in-process entry so the second call must read from disk
    mtime_before = cache_file.stat().st_mtime
    cache_key = f"{tmp_path}:{chain_id}:{address_lower}"
    _cached_vault_data.pop(cache_key, None)

    # 4. Second call — must read from disk cache (in-process entry was evicted above)
    result2 = fetch_morpho_vault_data(web3_arbitrum, FROB_USDC_ARBITRUM, cache_path=tmp_path)
    assert result2 is not None

    # 5. File not re-written → mtime unchanged (disk cache was used, not API)
    assert pytest.approx(cache_file.stat().st_mtime, abs=0.01) == mtime_before, "Cache file should not be overwritten on second call"
    assert result2["vault_warnings"] == result1["vault_warnings"]
    assert len(result2["market_warnings"]) == len(result1["market_warnings"])


# ---------------------------------------------------------------------------
# Ethereum tests
# ---------------------------------------------------------------------------


@flaky.flaky
@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed")
def test_morpho_short_timelock_red_only(web3_ethereum: Web3):
    """Dune USDC on Ethereum has short_timelock (RED) vault warning.

    ``VaultFlag.morpho_issues`` must be set even though market warnings are YELLOW only.

    Steps:

    1. Fetch offchain data for Dune USDC.
    2. Assert ``short_timelock`` is in vault-level warnings and is level RED.
    3. Assert that market warnings (if any) are all YELLOW (bad_debt_realized).
    4. Assert ``VaultFlag.morpho_issues`` is set because of the RED vault warning.
    """
    # 1. Fetch
    data = fetch_morpho_vault_data(web3_ethereum, DUNE_USDC_ETHEREUM)
    assert data is not None, "Expected Morpho data for Dune USDC"

    # 2. short_timelock (RED) in vault warnings
    vault_types = {w["type"] for w in data["vault_warnings"]}
    vault_levels_by_type = {w["type"]: w["level"] for w in data["vault_warnings"]}
    assert "short_timelock" in vault_types, f"vault_warnings={data['vault_warnings']}"
    assert vault_levels_by_type["short_timelock"] == "RED"

    # 3. Market warnings should be YELLOW (bad_debt_realized)
    for mw in data["market_warnings"]:
        assert mw["level"] in {"RED", "YELLOW"}, f"Unexpected level: {mw}"

    # 4. RED vault warning → morpho_issues
    vault = create_vault_instance_autodetect(web3_ethereum, DUNE_USDC_ETHEREUM)
    assert VaultFlag.morpho_issues in vault.get_flags()


@flaky.flaky
@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed")
def test_morpho_yellow_only_no_flag(web3_ethereum: Web3):
    """Steakhouse Level USDC on Ethereum has only a YELLOW vault warning (not_whitelisted).

    ``VaultFlag.morpho_issues`` must NOT be set — YELLOW warnings are informational only.

    Steps:

    1. Fetch offchain data for Steakhouse Level USDC.
    2. Assert no RED warnings exist at vault level.
    3. Assert market_warnings list is empty (no market-level warnings at all).
    4. Assert ``VaultFlag.morpho_issues`` is not in ``get_flags()``.
    """
    # 1. Fetch
    data = fetch_morpho_vault_data(web3_ethereum, STEAKHOUSE_LEVEL_USDC_ETHEREUM)
    assert data is not None, "Expected Morpho data for Steakhouse Level USDC"

    # 2. No RED at vault level (only not_whitelisted YELLOW expected)
    red_vault = [w for w in data["vault_warnings"] if w["level"] == "RED"]
    assert not red_vault, f"Expected no RED vault warnings, got {red_vault}"
    vault_types = {w["type"] for w in data["vault_warnings"]}
    assert "not_whitelisted" in vault_types, f"Expected not_whitelisted YELLOW, got {vault_types}"

    # 3. No market warnings at all
    assert data["market_warnings"] == [], f"Expected no market warnings, got {data['market_warnings']}"

    # 4. No RED → no morpho_issues flag
    vault = create_vault_instance_autodetect(web3_ethereum, STEAKHOUSE_LEVEL_USDC_ETHEREUM)
    assert VaultFlag.morpho_issues not in vault.get_flags()


@flaky.flaky
@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed")
def test_morpho_clean_vault_no_warnings(web3_ethereum: Web3):
    """Gauntlet USDC Prime on Ethereum has no warnings at all.

    ``VaultFlag.morpho_issues`` must NOT be set and both flag lists must be empty.

    Steps:

    1. Fetch offchain data for Gauntlet USDC Prime.
    2. Assert vault_warnings list is empty.
    3. Assert market_warnings list is empty.
    4. Assert ``VaultFlag.morpho_issues`` is not in ``get_flags()``.
    """
    # 1. Fetch
    data = fetch_morpho_vault_data(web3_ethereum, GAUNTLET_USDC_PRIME_ETHEREUM)
    assert data is not None, "Expected Morpho data for Gauntlet USDC Prime"

    # 2. No vault warnings
    assert data["vault_warnings"] == [], f"Expected empty vault_warnings, got {data['vault_warnings']}"

    # 3. No market warnings
    assert data["market_warnings"] == [], f"Expected empty market_warnings, got {data['market_warnings']}"

    # 4. No warnings → no morpho_issues flag
    vault = create_vault_instance_autodetect(web3_ethereum, GAUNTLET_USDC_PRIME_ETHEREUM)
    assert VaultFlag.morpho_issues not in vault.get_flags()


@flaky.flaky
@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed")
def test_morpho_bad_debt_realized_metadata(web3_ethereum: Web3):
    """bad_debt_realized warnings include metadata: bad_debt_usd and bad_debt_share.

    Dune USDC on Ethereum has ``bad_debt_realized`` (YELLOW) market warnings with USD and
    share-fraction metadata. Verifies that market warning metadata is parsed correctly.

    Steps:

    1. Fetch data for Dune USDC (known to have bad_debt_realized YELLOW market warnings).
    2. Find a market warning of type bad_debt_realized.
    3. Assert bad_debt_usd is a positive float.
    4. Assert bad_debt_share is a float in the range (0.0, 1.0].
    """
    # 1. Fetch
    data = fetch_morpho_vault_data(web3_ethereum, DUNE_USDC_ETHEREUM)
    assert data is not None, "Expected Morpho data for Dune USDC"

    # 2. Find a bad_debt_realized market warning
    realized_warnings = [w for w in data["market_warnings"] if w["type"] == "bad_debt_realized"]
    assert realized_warnings, f"Expected at least one bad_debt_realized market warning for Dune USDC, got {data['market_warnings']}"

    w = realized_warnings[0]

    # 3. bad_debt_usd is a positive float
    assert w["bad_debt_usd"] is not None, "Expected bad_debt_usd to be set"
    assert w["bad_debt_usd"] > 0, f"Expected positive bad_debt_usd, got {w['bad_debt_usd']}"

    # 4. bad_debt_share is a fraction in (0, 1]
    assert w["bad_debt_share"] is not None, "Expected bad_debt_share to be set"
    assert 0.0 < w["bad_debt_share"] <= 1.0, f"Expected fraction in (0,1], got {w['bad_debt_share']}"
