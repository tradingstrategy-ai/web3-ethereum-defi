from types import SimpleNamespace

from web3 import Web3

from eth_defi.erc_4626.vault_protocol.lagoon.config_event_scanner import (
    ChainGuardConfig,
    DecodedGuardEvent,
    GuardEventScanInfo,
    MultichainGuardConfig,
    build_multichain_guard_config,
    fetch_guard_config_events,
    format_guard_config_markdown,
    format_guard_config_report,
)


def test_build_guard_config_tracks_latest_lagoon_settlement_limit():
    """Track capped Lagoon configuration and its legacy unlimited reset."""
    safe_address = Web3.to_checksum_address("0x1000000000000000000000000000000000000001")
    module_address = Web3.to_checksum_address("0x2000000000000000000000000000000000000002")
    vault = Web3.to_checksum_address("0x3000000000000000000000000000000000000003")
    asset = Web3.to_checksum_address("0x4000000000000000000000000000000000000004")
    pending_silo = Web3.to_checksum_address("0x5000000000000000000000000000000000000005")

    events = [
        DecodedGuardEvent(
            event_name="LagoonVaultApproved",
            args={"vault": vault, "notes": "capped"},
            block_number=1,
            transaction_hash="0x01",
            log_index=0,
        ),
        DecodedGuardEvent(
            event_name="LagoonSettlementLimitSet",
            args={
                "vault": vault,
                "asset": asset,
                "pendingSilo": pending_silo,
                "maxSettlementAmount": 8_000_000,
                "enabled": True,
                "notes": "capped",
            },
            block_number=1,
            transaction_hash="0x01",
            log_index=1,
        ),
        DecodedGuardEvent(
            event_name="LagoonSettlementCooldownSet",
            args={
                "vault": vault,
                "settlementCooldown": 43_200,
                "notes": "capped",
            },
            block_number=1,
            transaction_hash="0x01",
            log_index=2,
        ),
    ]
    config = build_multichain_guard_config(
        events={8453: events},
        safe_address=safe_address,
        module_addresses={8453: module_address},
    )

    chain_config = config.chains[8453]
    assert chain_config.lagoon_vaults == (vault,)
    assert len(chain_config.lagoon_settlement_limits) == 1
    limit = chain_config.lagoon_settlement_limits[0]
    assert limit.vault == vault
    assert limit.asset == asset
    assert limit.pending_silo == pending_silo
    assert limit.max_settlement_amount == 8_000_000
    assert limit.settlement_cooldown == 43_200
    assert limit.enabled
    assert "8000000 raw units" in config.format_human_readable()
    assert "43200s cooldown" in config.format_human_readable()

    events.append(
        DecodedGuardEvent(
            event_name="LagoonVaultApproved",
            args={"vault": vault, "notes": "reset to unlimited"},
            block_number=2,
            transaction_hash="0x02",
            log_index=0,
        )
    )
    reset_config = build_multichain_guard_config(
        events={8453: events},
        safe_address=safe_address,
        module_addresses={8453: module_address},
    )
    assert reset_config.chains[8453].lagoon_settlement_limits == ()
    assert f"{vault}: unlimited" in reset_config.format_human_readable()


def test_fetch_guard_config_events_falls_back_to_rpc_and_records_scan_metadata(monkeypatch):
    safe_address = "0x1000000000000000000000000000000000000001"
    module_address = "0x2000000000000000000000000000000000000002"
    start_block = 120_000
    end_block = 123_456
    captured: dict[str, tuple] = {}

    web3 = SimpleNamespace(
        eth=SimpleNamespace(
            chain_id=42161,
            block_number=end_block,
        )
    )

    monkeypatch.setattr(
        "eth_defi.erc_4626.vault_protocol.lagoon.config_event_scanner.resolve_trading_strategy_module",
        lambda web3, safe_address: Web3.to_checksum_address(module_address),
    )
    monkeypatch.setattr(
        "eth_defi.erc_4626.vault_protocol.lagoon.config_event_scanner._build_event_topic_map",
        lambda: {},
    )

    def fake_fetch_guard_events_hypersync(client, module_address, topic_map, from_block=0, to_block=None):
        captured["hypersync"] = (from_block, to_block, module_address)
        return []

    def fake_fetch_guard_events_web3(web3, module_address, topic_map, from_block=0):
        captured["rpc"] = (from_block, module_address)
        return [
            DecodedGuardEvent(
                event_name="SenderApproved",
                args={"sender": "0x3000000000000000000000000000000000000003"},
                block_number=123_000,
                transaction_hash="0xabc",
                log_index=1,
            )
        ]

    monkeypatch.setattr(
        "eth_defi.erc_4626.vault_protocol.lagoon.config_event_scanner._fetch_guard_events_hypersync",
        fake_fetch_guard_events_hypersync,
    )
    monkeypatch.setattr(
        "eth_defi.erc_4626.vault_protocol.lagoon.config_event_scanner._fetch_guard_events_web3",
        fake_fetch_guard_events_web3,
    )

    events, module_addresses, scan_info = fetch_guard_config_events(
        safe_address=safe_address,
        web3=web3,
        hypersync_client=object(),
        follow_cctp=False,
        from_block={42161: start_block},
        include_scan_metadata=True,
    )

    assert captured["hypersync"] == (start_block, end_block, Web3.to_checksum_address(module_address))
    assert captured["rpc"] == (start_block, Web3.to_checksum_address(module_address))
    assert module_addresses[42161] == Web3.to_checksum_address(module_address)
    assert len(events[42161]) == 1
    assert scan_info[42161].backend == "rpc"
    assert scan_info[42161].from_block == start_block
    assert scan_info[42161].to_block == end_block
    assert scan_info[42161].fallback_reason == "fallback after hypersync returned 0 events"


def test_fetch_guard_config_events_uses_explicit_module_override(monkeypatch):
    safe_address = "0x1000000000000000000000000000000000000001"
    new_module_address = "0x3000000000000000000000000000000000000003"

    web3 = SimpleNamespace(
        eth=SimpleNamespace(
            chain_id=42161,
            block_number=123_456,
        )
    )

    def fail_if_resolved(*args, **kwargs):
        raise AssertionError("Safe module resolution should be skipped when an explicit module override is supplied")

    monkeypatch.setattr(
        "eth_defi.erc_4626.vault_protocol.lagoon.config_event_scanner.resolve_trading_strategy_module",
        fail_if_resolved,
    )
    monkeypatch.setattr(
        "eth_defi.erc_4626.vault_protocol.lagoon.config_event_scanner._build_event_topic_map",
        lambda: {},
    )

    captured: dict[str, tuple] = {}

    def fake_fetch_guard_events_web3(web3, module_address, topic_map, from_block=0):
        captured["rpc"] = (from_block, module_address)
        return [
            DecodedGuardEvent(
                event_name="SenderApproved",
                args={"sender": "0x4000000000000000000000000000000000000004"},
                block_number=123_000,
                transaction_hash="0xdef",
                log_index=1,
            )
        ]

    monkeypatch.setattr(
        "eth_defi.erc_4626.vault_protocol.lagoon.config_event_scanner._fetch_guard_events_web3",
        fake_fetch_guard_events_web3,
    )

    events, module_addresses, scan_info = fetch_guard_config_events(
        safe_address=safe_address,
        web3=web3,
        follow_cctp=False,
        from_block={42161: 120_000},
        include_scan_metadata=True,
        module_addresses_override={42161: new_module_address},
    )

    assert captured["rpc"] == (120_000, Web3.to_checksum_address(new_module_address))
    assert module_addresses[42161] == Web3.to_checksum_address(new_module_address)
    assert len(events[42161]) == 1
    assert scan_info[42161].backend == "rpc"


def test_guard_report_includes_backend_and_block_range():
    safe_address = Web3.to_checksum_address("0x1000000000000000000000000000000000000001")
    module_address = Web3.to_checksum_address("0x2000000000000000000000000000000000000002")

    config = MultichainGuardConfig(
        safe_address=safe_address,
        chains={
            42161: ChainGuardConfig(
                chain_id=42161,
                chain_name="Arbitrum",
                safe_address=safe_address,
                module_address=module_address,
                senders=(),
                receivers=(),
                assets=(),
                any_asset=True,
                approval_destinations=(),
                withdraw_destinations=(),
                delegation_approval_destinations=(),
                lagoon_vaults=("0x4000000000000000000000000000000000000004",),
                erc4626_vaults=(),
                cctp_messengers=(),
                cctp_destinations=(),
                cowswap_settlements=(),
                velora_swappers=(),
                gmx_routers=(),
                gmx_markets=(),
                hypercore_core_writers=(),
                hypercore_deposit_wallets=(),
                hypercore_vaults=(),
                lighter_contracts=(),
                call_sites=(),
            )
        },
    )
    scan_info = {
        42161: GuardEventScanInfo(
            chain_id=42161,
            backend="rpc",
            from_block=120_000,
            to_block=123_456,
        )
    }

    report = format_guard_config_report(
        config=config,
        events={42161: []},
        scan_info=scan_info,
    )
    markdown = format_guard_config_markdown(
        config=config,
        events={42161: []},
        scan_info=scan_info,
    )

    assert "Backend: rpc" in report
    assert "Vault:  0x4000000000000000000000000000000000000004" in report
    assert "120,000 -> 123,456" in report
    assert "**Backend**: `rpc`" in markdown
    assert "**Vault**:" in markdown
    assert "0x4000000000000000000000000000000000000004" in markdown
    assert "**Block range**: `120,000 -> 123,456`" in markdown
