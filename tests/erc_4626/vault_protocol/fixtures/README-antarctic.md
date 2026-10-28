# Antarctic transport fixtures

Recorded on 2026-09-30 from the authenticated Envio Arbitrum Hypersync endpoint and configured Arbitrum JSON-RPC providers. No API keys or provider URL credentials are stored.

`antarctic-settlements.json` contains 40 actual raw/decoded events: the first two observations of each product and recent observations after Unix timestamp `1780000000`, selected from a full implementation read of 980 settlements through block `510301932`. This subset is a deterministic pipeline fixture, not a complete history or a schedule claim. Each record retains the manager-routed LP identity, block/hash/time, transaction/log index, original event bytes and exact uint256 amounts. Bootstrap and redemption diagnostics are included.

`antarctic-rpc.json` records 20 token/manager call responses at block `510301932`. The test provider rejects unknown RPC methods and never supplies event discovery through JSON-RPC. Live authenticated integration tests complement recorded transport and assert both reviewed transaction identities independently.
