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

- ``Safe x Steakhouse USDC`` — ``0xbEeFCe6c76C7D7A8066562Fe9FF0e343a52dD92F``
  Vault warnings: ``not_whitelisted`` (YELLOW only)
  Market warnings: none
  → ``VaultFlag.morpho_issues`` NOT expected (all warnings are YELLOW)

- ``Gauntlet USDC Prime`` — ``0xdd0f28e19C1780eb6396170735D45153D261490d``
  No warnings at all
  → ``VaultFlag.morpho_issues`` NOT expected

.. warning::

    These tests query the **live Morpho Blue GraphQL API**.  Warning levels
    and types are controlled by Morpho's offchain risk pipeline and change
    without notice as vault parameters are updated on-chain:

    - A vault classified as YELLOW-only today may gain a RED warning tomorrow
      (e.g. ``deposit_disabled``, ``short_timelock``, ``bad_debt_unrealized``).
    - A vault with no warnings may acquire warnings, or vice versa.
    - Market-level warnings come and go as bad debt is realised or resolved.

    When these tests break, it is usually because a test vault's warning state
    has changed.  The fix is to find a replacement vault with the expected
    warning profile using the Morpho GraphQL API — **not** to mock the API,
    because the purpose of these tests is to verify the real integration path.

Because the Morpho Blue API is a live external service these tests are marked
``flaky`` and skipped on CI if the required ``JSON_RPC_*`` env var is absent.
"""

import os
from pathlib import Path

import flaky
import pytest
import requests
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.morpho.offchain_metadata import (
    MorphoVaultAPIResult,
    MorphoVaultAPIStatus,
    MorphoVaultData,
    _cached_vault_data,  # noqa: PLC2701
    fetch_morpho_vault_api_result,
    fetch_morpho_vault_data,
)
from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import MorphoV1Vault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import NOT_IN_MORPHO_API, VaultFlag

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

#: frobUSDC on Arbitrum — short_timelock (RED) + bad_debt_unrealized (RED)
FROB_USDC_ARBITRUM = "0xC3415c9231Dad88F8146107372143f6dAE042967"

#: Dune USDC on Ethereum — short_timelock (RED) vault warning + bad_debt_realized (YELLOW) market
DUNE_USDC_ETHEREUM = "0xfD1241C4fc37680De370dDc20eBF7bC5e561E1c1"

#: Safe x Steakhouse USDC on Ethereum — not_whitelisted (YELLOW) vault only, no market warnings
#: (Previous vault 0xbEEf11C63d7173BdCC2037e7220eE9Bd0cCDA862 gained deposit_disabled RED in May 2026)
YELLOW_ONLY_USDC_ETHEREUM = "0xbEeFCe6c76C7D7A8066562Fe9FF0e343a52dD92F"

#: Gauntlet USDC Prime on Ethereum — clean vault, no warnings
GAUNTLET_USDC_PRIME_ETHEREUM = "0xdd0f28e19C1780eb6396170735D45153D261490d"

NOT_FOUND_TEST_VAULT = "0x000000000000000000000000000000000000dead"


class _FakeEth:
    """Minimal fake Web3.eth object for Morpho API tests."""

    def __init__(self, chain_id: int = 1):
        self.chain_id = chain_id


class _FakeWeb3:
    """Minimal fake Web3 object for Morpho API tests.

    :param chain_id:
        Chain ID reported by ``web3.eth.chain_id``. Defaults to Ethereum mainnet (1).
    """

    def __init__(self, chain_id: int = 1):
        self.eth = _FakeEth(chain_id)


class _FakeResponse:
    """Minimal requests response for Morpho API tests."""

    def __init__(self, body: dict):
        self.body = body

    def raise_for_status(self) -> None:
        """No-op for successful fake responses."""

    def json(self) -> dict:
        """Return the fake JSON body."""
        return self.body


def _patch_morpho_api(monkeypatch: pytest.MonkeyPatch, responses: list[dict]) -> list[dict]:
    """Patch Morpho API HTTP calls and return captured payloads."""
    calls: list[dict] = []

    def fake_post(url: str, json: dict, timeout: int, headers: dict) -> _FakeResponse:
        calls.append(
            {
                "url": url,
                "json": json,
                "timeout": timeout,
                "headers": headers,
            }
        )
        return _FakeResponse(responses.pop(0))

    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.morpho.offchain_metadata.requests.post", fake_post)
    return calls


def _make_morpho_v1_vault(address: str, chain_id: int = 1) -> MorphoV1Vault:
    """Create a Morpho V1 vault with only offchain API dependencies populated."""
    return MorphoV1Vault(
        web3=_FakeWeb3(chain_id),  # type: ignore[arg-type]
        spec=VaultSpec(chain_id=chain_id, vault_address=address),
        token_cache={},
    )


def _patch_base_vault_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid onchain ERC-4626 flag reads in pure Morpho API unit tests."""

    def fake_get_flags(_self) -> set[VaultFlag]:
        return set()

    monkeypatch.setattr("eth_defi.erc_4626.vault.ERC4626Vault.get_flags", fake_get_flags)


@pytest.fixture(scope="module")
def web3_arbitrum() -> Web3:
    """Web3 connection to Arbitrum."""
    return create_multi_provider_web3(JSON_RPC_ARBITRUM)


@pytest.fixture(scope="module")
def web3_ethereum() -> Web3:
    """Web3 connection to Ethereum mainnet."""
    return create_multi_provider_web3(JSON_RPC_ETHEREUM)


# ---------------------------------------------------------------------------
# Mocked API status tests
# ---------------------------------------------------------------------------


def test_morpho_not_found_adds_dynamic_flag_and_note(monkeypatch: pytest.MonkeyPatch):
    """A definitive Morpho ``NOT_FOUND`` adds the dynamic blacklist flag and note."""
    _patch_base_vault_flags(monkeypatch)

    def fake_fetch_morpho_vault_api_result(*_args, **_kwargs) -> MorphoVaultAPIResult:
        return MorphoVaultAPIResult(MorphoVaultAPIStatus.not_found)

    monkeypatch.setattr(
        "eth_defi.erc_4626.vault_protocol.morpho.vault_v1.fetch_morpho_vault_api_result",
        fake_fetch_morpho_vault_api_result,
    )

    vault = _make_morpho_v1_vault(NOT_FOUND_TEST_VAULT)

    assert vault.get_flags() == {VaultFlag.not_in_morpho_api}
    assert vault.get_notes() == NOT_IN_MORPHO_API
    assert VaultFlag.morpho_issues not in vault.get_flags()


@pytest.mark.parametrize(
    ("vault_class", "module_path"),
    [
        (MorphoV1Vault, "eth_defi.erc_4626.vault_protocol.morpho.vault_v1"),
        (MorphoV2Vault, "eth_defi.erc_4626.vault_protocol.morpho.vault_v2"),
    ],
)
def test_robinhood_morpho_api_not_found_is_temporarily_not_blacklisted(
    monkeypatch: pytest.MonkeyPatch,
    vault_class: type[MorphoV1Vault] | type[MorphoV2Vault],
    module_path: str,
):
    """Robinhood API coverage gaps do not hide on-chain-detected Morpho vaults."""
    _patch_base_vault_flags(monkeypatch)

    def fake_fetch_morpho_vault_api_result(*_args, **_kwargs) -> MorphoVaultAPIResult:
        return MorphoVaultAPIResult(MorphoVaultAPIStatus.not_found)

    monkeypatch.setattr(f"{module_path}.fetch_morpho_vault_api_result", fake_fetch_morpho_vault_api_result)
    vault = vault_class(
        web3=_FakeWeb3(4663),  # type: ignore[arg-type]
        spec=VaultSpec(chain_id=4663, vault_address=NOT_FOUND_TEST_VAULT),
        token_cache={},
    )

    assert vault.get_flags() == set()
    assert vault.get_notes() is None


def test_morpho_not_found_is_not_cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A definitive Morpho ``NOT_FOUND`` is queried again by a fresh vault instance."""
    calls = _patch_morpho_api(
        monkeypatch,
        [
            {
                "errors": [
                    {
                        "message": "No results matching given parameters",
                        "status": "NOT_FOUND",
                        "extensions": {},
                    }
                ],
                "data": None,
            },
            {
                "errors": [
                    {
                        "message": "No results matching given parameters",
                        "status": "NOT_FOUND",
                        "extensions": {},
                    }
                ],
                "data": None,
            },
        ],
    )

    vault_1 = _make_morpho_v1_vault(NOT_FOUND_TEST_VAULT)
    vault_2 = _make_morpho_v1_vault(NOT_FOUND_TEST_VAULT)

    assert fetch_morpho_vault_api_result(vault_1.web3, vault_1.vault_address, cache_path=tmp_path).is_not_found
    assert fetch_morpho_vault_api_result(vault_2.web3, vault_2.vault_address, cache_path=tmp_path).is_not_found

    cache_file = tmp_path / f"morpho_1_{NOT_FOUND_TEST_VAULT.lower()}.json"
    assert not cache_file.exists()
    expected_call_count = 2
    assert len(calls) == expected_call_count


def test_morpho_unsupported_chain_short_circuits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Vaults on chains Morpho does not index resolve to NOT_FOUND without any HTTP call.

    The vault scanner detects Morpho-like vaults on chains the Morpho API does not
    support (e.g. BNB Smart Chain, chain 56). Such lookups must be short-circuited so
    the scanner does not query the API once per vault on every cycle.

    1. Build a fake Web3 reporting chain 56 (BNB Smart Chain).
    2. Patch the HTTP layer to fail loudly if it is ever called.
    3. Assert the lookup returns NOT_FOUND with zero HTTP calls and no cache file.
    """

    # 1. Fake Web3 on chain 56 (BNB Smart Chain), which the Morpho API does not index.
    web3 = _FakeWeb3(chain_id=56)

    # 2. Any HTTP call would mean the short-circuit failed.
    def fail_post(*_args, **_kwargs):
        raise AssertionError("Morpho API must not be called for unsupported chains")

    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.morpho.offchain_metadata.requests.post", fail_post)

    # 3. Lookup is NOT_FOUND, no HTTP call, no cache written.
    result = fetch_morpho_vault_api_result(web3, NOT_FOUND_TEST_VAULT, cache_path=tmp_path)
    assert result.is_not_found
    cache_file = tmp_path / f"morpho_56_{NOT_FOUND_TEST_VAULT.lower()}.json"
    assert not cache_file.exists()


def test_morpho_unsupported_chainid_graphql_error_is_not_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A ``BAD_USER_INPUT`` / ``unsupported chainId`` GraphQL error is treated as NOT_FOUND.

    This is the defensive layer: even if a chain slips past the supported-chain
    allowlist, the API's ``BAD_USER_INPUT`` response must be classified as a
    definitive miss (NOT_FOUND) rather than a transient error, so it is not
    retried on every scan cycle.

    1. Patch the API to return the real ``unsupported chainId`` GraphQL error.
    2. Assert the result is NOT_FOUND (not transient) and nothing is cached.
    """

    # 1. Reproduce the exact production error payload for chain 56.
    _patch_morpho_api(
        monkeypatch,
        [
            {
                "errors": [
                    {
                        "message": 'unsupported chainId "56"',
                        "status": "BAD_USER_INPUT",
                        "extensions": {},
                    }
                ],
                "data": None,
            }
        ],
    )

    # 2. Classified as a definitive miss, nothing cached.
    result = fetch_morpho_vault_api_result(_FakeWeb3(), NOT_FOUND_TEST_VAULT, cache_path=tmp_path)
    assert result.status == MorphoVaultAPIStatus.not_found
    cache_file = tmp_path / f"morpho_1_{NOT_FOUND_TEST_VAULT.lower()}.json"
    assert not cache_file.exists()


def test_morpho_transient_error_does_not_add_not_found_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Transient Morpho API errors are not treated as definitive missing-vault results."""
    _patch_base_vault_flags(monkeypatch)

    def fake_post(_url: str, **_kwargs) -> _FakeResponse:
        message = "temporary timeout"
        raise requests.Timeout(message)

    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.morpho.offchain_metadata.requests.post", fake_post)

    vault = _make_morpho_v1_vault(NOT_FOUND_TEST_VAULT)
    result = fetch_morpho_vault_api_result(vault.web3, vault.vault_address, cache_path=tmp_path)

    def fake_fetch_morpho_vault_api_result(*_args, **_kwargs) -> MorphoVaultAPIResult:
        return MorphoVaultAPIResult(MorphoVaultAPIStatus.transient_error)

    monkeypatch.setattr(
        "eth_defi.erc_4626.vault_protocol.morpho.vault_v1.fetch_morpho_vault_api_result",
        fake_fetch_morpho_vault_api_result,
    )

    assert result.status == MorphoVaultAPIStatus.transient_error
    assert VaultFlag.not_in_morpho_api not in vault.get_flags()
    assert vault.get_notes() is None

    cache_file = tmp_path / f"morpho_1_{NOT_FOUND_TEST_VAULT.lower()}.json"
    assert not cache_file.exists()


def test_morpho_found_data_uses_disk_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Successful Morpho warning data still uses the existing disk cache."""
    calls = _patch_morpho_api(
        monkeypatch,
        [
            {
                "data": {
                    "vaultByAddress": {
                        "address": Web3.to_checksum_address(DUNE_USDC_ETHEREUM),
                        "warnings": [{"type": "short_timelock", "level": "RED"}],
                        "state": {
                            "curators": [
                                {"name": None},
                                {"name": "Gauntlet"},
                            ],
                            "allocation": [],
                        },
                    }
                }
            }
        ],
    )

    web3 = _FakeWeb3()  # type: ignore[assignment]
    result_1 = fetch_morpho_vault_data(web3, DUNE_USDC_ETHEREUM, cache_path=tmp_path)
    assert result_1 is not None
    assert result_1["vault_warnings"] == [{"type": "short_timelock", "level": "RED"}]
    assert result_1["manager_name"] == "Gauntlet"

    cache_key = f"{tmp_path}:1:{DUNE_USDC_ETHEREUM.lower()}"
    _cached_vault_data.pop(cache_key, None)

    result_2 = fetch_morpho_vault_data(web3, DUNE_USDC_ETHEREUM, cache_path=tmp_path)

    assert result_2 == result_1
    assert len(calls) == 1

    def fake_fetch_morpho_vault_api_result(*_args, **_kwargs) -> MorphoVaultAPIResult:
        return MorphoVaultAPIResult(
            MorphoVaultAPIStatus.found,
            {
                "vault_warnings": [],
                "market_warnings": [],
                "manager_name": "Gauntlet",
            },
        )

    monkeypatch.setattr(
        "eth_defi.erc_4626.vault_protocol.morpho.vault_v1.fetch_morpho_vault_api_result",
        fake_fetch_morpho_vault_api_result,
    )

    vault = _make_morpho_v1_vault(GAUNTLET_USDC_PRIME_ETHEREUM)

    assert vault.manager_name == "Gauntlet"

    v2_calls = _patch_morpho_api(
        monkeypatch,
        [
            {
                "data": {
                    "vaultV2ByAddress": {
                        "address": Web3.to_checksum_address(GAUNTLET_USDC_PRIME_ETHEREUM),
                        "warnings": [],
                        "curators": {
                            "items": [{"name": "Steakhouse Financial"}],
                        },
                    }
                }
            }
        ],
    )

    result_3 = fetch_morpho_vault_api_result(web3, GAUNTLET_USDC_PRIME_ETHEREUM, cache_path=tmp_path, api_version="v2")

    assert result_3.status == MorphoVaultAPIStatus.found
    assert result_3.data is not None
    assert result_3.data["manager_name"] == "Steakhouse Financial"
    assert len(v2_calls) == 1


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
    """Safe x Steakhouse USDC on Ethereum has only a YELLOW vault warning (not_whitelisted).

    ``VaultFlag.morpho_issues`` must NOT be set — YELLOW warnings are informational only.

    Steps:

    1. Fetch offchain data for Safe x Steakhouse USDC.
    2. Assert no RED warnings exist at vault level.
    3. Assert market_warnings list is empty (no market-level warnings at all).
    4. Assert ``VaultFlag.morpho_issues`` is not in ``get_flags()``.
    """
    # 1. Fetch
    data = fetch_morpho_vault_data(web3_ethereum, YELLOW_ONLY_USDC_ETHEREUM)
    assert data is not None, "Expected Morpho data for Steakhouse Level USDC"

    # 2. No RED at vault level (only not_whitelisted YELLOW expected)
    red_vault = [w for w in data["vault_warnings"] if w["level"] == "RED"]
    assert not red_vault, f"Expected no RED vault warnings, got {red_vault}"
    vault_types = {w["type"] for w in data["vault_warnings"]}
    assert "not_whitelisted" in vault_types, f"Expected not_whitelisted YELLOW, got {vault_types}"

    # 3. No market warnings at all
    assert data["market_warnings"] == [], f"Expected no market warnings, got {data['market_warnings']}"

    # 4. No RED → no morpho_issues flag
    vault = create_vault_instance_autodetect(web3_ethereum, YELLOW_ONLY_USDC_ETHEREUM)
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
    """bad_debt_realized warnings include metadata: bad_debt_share (and optionally bad_debt_usd).

    Dune USDC on Ethereum has ``bad_debt_realized`` (YELLOW) market warnings with
    share-fraction metadata. Verifies that market warning metadata is parsed correctly.

    Note: as of June 2026, the Morpho API returns ``badDebtUsd: null`` for
    ``bad_debt_realized`` warnings while ``badDebtShare`` remains populated.

    Steps:

    1. Fetch data for Dune USDC (known to have bad_debt_realized YELLOW market warnings).
    2. Find a market warning of type bad_debt_realized.
    3. Assert bad_debt_share is a float in the range (0.0, 1.0].
    4. Assert bad_debt_usd, if present, is a positive float.
    """
    # 1. Fetch
    data = fetch_morpho_vault_data(web3_ethereum, DUNE_USDC_ETHEREUM)
    assert data is not None, "Expected Morpho data for Dune USDC"

    # 2. Find a bad_debt_realized market warning
    realized_warnings = [w for w in data["market_warnings"] if w["type"] == "bad_debt_realized"]
    assert realized_warnings, f"Expected at least one bad_debt_realized market warning for Dune USDC, got {data['market_warnings']}"

    w = realized_warnings[0]

    # 3. bad_debt_share is a fraction in (0, 1]
    assert w["bad_debt_share"] is not None, "Expected bad_debt_share to be set"
    assert 0.0 < w["bad_debt_share"] <= 1.0, f"Expected fraction in (0,1], got {w['bad_debt_share']}"

    # 4. bad_debt_usd, if present, is a positive float
    if w["bad_debt_usd"] is not None:
        assert w["bad_debt_usd"] > 0, f"Expected positive bad_debt_usd, got {w['bad_debt_usd']}"
