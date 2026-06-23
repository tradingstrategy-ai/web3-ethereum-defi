"""Test Hypersync vault lead discovery flaky head handling."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from eth_defi.erc_4626.discovery_base import LeadScanReport, VaultDiscoveryBase
from eth_defi.erc_4626.hypersync_discovery import HypersyncCrappedOut, HypersyncVaultDiscover
from eth_defi.hypersync.hypersync_timestamp import HypersyncFlaky


def _create_discover(rpc_height: int = 200) -> HypersyncVaultDiscover:
    """Create a vault discover instance with mocked dependencies."""
    web3 = MagicMock()
    web3.eth.chain_id = 1868
    web3.eth.block_number = rpc_height
    return HypersyncVaultDiscover(
        web3=web3,
        web3factory=MagicMock(),
        client=MagicMock(),
    )


def test_hypersync_vault_discovery_clips_end_block_to_available_height():
    """Verify lead discovery does not ask Hypersync for unavailable head blocks."""
    discover = _create_discover(rpc_height=24537799)

    with patch(
        "eth_defi.erc_4626.hypersync_discovery.get_hypersync_block_height_with_retries",
        return_value=24537791,
    ):
        clipped_end_block = discover.clip_end_block_to_available_height(
            start_block=24483147,
            end_block=24537799,
        )

    assert clipped_end_block == 24537791


def test_hypersync_vault_discovery_scan_uses_clipped_end_block():
    """Verify the base discovery workflow sees the clipped end block."""
    discover = _create_discover(rpc_height=200)

    with (
        patch(
            "eth_defi.erc_4626.hypersync_discovery.get_hypersync_block_height_with_retries",
            return_value=150,
        ),
        patch.object(
            VaultDiscoveryBase,
            "scan_vaults",
            return_value=LeadScanReport(end_block=150),
        ) as base_scan,
    ):
        report = discover.scan_vaults(
            start_block=100,
            end_block=200,
            display_progress=False,
        )

    assert report.end_block == 150
    base_scan.assert_called_once_with(100, 150, display_progress=False)


def test_hypersync_vault_discovery_scan_noops_when_no_blocks_available():
    """Verify an up-to-date scanner does not fail when Hypersync has no new blocks."""
    discover = _create_discover(rpc_height=100)

    with (
        patch(
            "eth_defi.erc_4626.hypersync_discovery.get_hypersync_block_height_with_retries",
            return_value=100,
        ),
        patch.object(
            VaultDiscoveryBase,
            "scan_vaults",
        ) as base_scan,
    ):
        report = discover.scan_vaults(
            start_block=100,
            end_block=120,
            display_progress=False,
        )

    assert report.end_block == 100
    assert report.old_leads == 0
    base_scan.assert_not_called()


def test_hypersync_vault_discovery_height_check_failure_is_wrapped():
    """Verify Hypersync height failures are converted to discovery errors."""
    discover = _create_discover(rpc_height=200)

    with patch(
        "eth_defi.erc_4626.hypersync_discovery.get_hypersync_block_height_with_retries",
        side_effect=HypersyncFlaky("rate limited"),
    ):
        with pytest.raises(HypersyncCrappedOut, match="rate limited"):
            discover.scan_vaults(
                start_block=100,
                end_block=200,
                display_progress=False,
            )


def test_hypersync_vault_discovery_scan_uses_shared_height_retry():
    """Verify discovery asks the shared helper to retry height checks."""
    discover = _create_discover(rpc_height=200)

    with patch(
        "eth_defi.erc_4626.hypersync_discovery.get_hypersync_block_height_with_retries",
        return_value=150,
    ) as height_check:
        discover.clip_end_block_to_available_height(
            start_block=100,
            end_block=200,
        )

    height_check.assert_called_once()
    kwargs = height_check.call_args.kwargs
    assert kwargs["attempts"] == 3
    assert kwargs["retry_sleep"] == 30
    assert kwargs["reason"] == "vault-lead-discovery"


def test_hypersync_vault_discovery_next_block_range_error_is_retryable():
    """Verify the production receiver pagination error becomes retryable."""
    discover = _create_discover()
    receiver = MagicMock()
    receiver.recv = AsyncMock(side_effect=RuntimeError("inner receiver\n\nCaused by:\n    server returned next_block 24537791 outside the requested range [24537791..24537799)"))

    async def _run():
        with (
            patch.object(discover, "build_query", return_value=MagicMock()),
            patch(
                "eth_defi.erc_4626.hypersync_discovery.get_vault_event_topic_map",
                return_value={},
            ),
            patch(
                "eth_defi.erc_4626.hypersync_discovery.open_hypersync_stream",
                new_callable=AsyncMock,
                return_value=receiver,
            ),
        ):
            with pytest.raises(HypersyncCrappedOut, match="stream pagination failed"):
                await discover.scan_potential_vaults(
                    start_block=24483147,
                    end_block=24537791,
                    display_progress=False,
                )

    asyncio.run(_run())


def test_hypersync_vault_discovery_stream_setup_next_block_range_error_is_retryable():
    """Verify stream setup pagination errors are retryable."""
    discover = _create_discover()

    async def _run():
        with (
            patch.object(discover, "build_query", return_value=MagicMock()),
            patch(
                "eth_defi.erc_4626.hypersync_discovery.get_vault_event_topic_map",
                return_value={},
            ),
            patch(
                "eth_defi.erc_4626.hypersync_discovery.open_hypersync_stream",
                new_callable=AsyncMock,
                side_effect=RuntimeError("inner receiver\n\nCaused by:\n    server returned next_block 24537791 outside the requested range [24537791..24537799)"),
            ),
        ):
            with pytest.raises(HypersyncCrappedOut, match="stream pagination failed"):
                await discover.scan_potential_vaults(
                    start_block=24483147,
                    end_block=24537791,
                    display_progress=False,
                )

    asyncio.run(_run())


def test_hypersync_vault_discovery_fetch_leads_retries_flaky_error():
    """Verify the sync wrapper retries retryable discovery failures."""
    discover = _create_discover()
    report = LeadScanReport()
    discover.scan_potential_vaults = AsyncMock(
        side_effect=[
            HypersyncCrappedOut("temporary Hypersync failure"),
            report,
        ]
    )

    with patch(
        "eth_defi.erc_4626.hypersync_discovery.asyncio.sleep",
        new_callable=AsyncMock,
    ) as sleep_mock:
        result = discover.fetch_leads(
            start_block=1,
            end_block=2,
            display_progress=False,
            attempts=2,
            retry_sleep=1,
        )

    assert result is report
    assert discover.scan_potential_vaults.await_count == 2
    sleep_mock.assert_awaited_once_with(1)
