# Aave Multi-Chain Liquidation WebSocket

Real-time WebSocket server that streams Aave V3 liquidations from multiple chains using Envio Hypersync.

## Quick Start

### Local Development

```bash
poetry install --extras data --extras hypersync
poetry run python scripts/aave-liquidations/websocket_server_hypersync.py
```

Server starts at: `ws://localhost:8001`

### Connect from Bot

```python
import asyncio
import websockets
import json

async def subscribe():
    async with websockets.connect("ws://100.64.0.5:8001") as ws:
        async for message in ws:
            liq = json.loads(message)
            print(f"[{liq['chain']}] Liquidation: {liq['tx_hash']}")

asyncio.run(subscribe())
```

## WebSocket Message Format

```json
{
  "chain": "ethereum",
  "chain_id": 1,
  "block_number": 19400000,
  "tx_hash": "0xabc123...",
  "log_index": 5,
  "timestamp": 1730188800,
  "timestamp_iso": "2025-10-29T10:00:00+00:00",
  "collateral_asset": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
  "debt_asset": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
  "user": "0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
  "debt_to_cover": "1250500000",
  "liquidated_collateral_amount": "850000000000000000",
  "liquidator": "0x3a010D129d71fE9C77A0E8c9FB06DceB9F45b18D",
  "receive_a_token": false
}
```

## Supported Chains

- Ethereum (chain_id: 1)
- Arbitrum (chain_id: 42161)
- Optimism (chain_id: 10)
- Polygon (chain_id: 137)
- Base (chain_id: 8453)
- Avalanche (chain_id: 43114)
- BSC (chain_id: 56)

## Architecture

```
Freqtrade Bot (Tailscale) <-- ws://100.x.x.x:8001 --> WebSocket Server (Docker) --> Hypersync (7 chains)
```

## Docker Deployment

### Using Docker Compose (Recommended)

Docker Compose automatically loads RPC URLs from your `.env` file:

```bash
cd /path/to/web3-ethereum-defi/scripts/aave-liquidations

docker-compose up -d
docker-compose logs -f
```

To stop:
```bash
docker-compose down
```

### Manual Docker Build

```bash
cd /path/to/web3-ethereum-defi

docker build -t aave-liquidation-ws -f scripts/aave-liquidations/Dockerfile .

docker run -d \
  -p 8001:8001 \
  --env-file .env \
  --name aave-liquidation-ws \
  --restart unless-stopped \
  aave-liquidation-ws

docker logs -f aave-liquidation-ws
```

### Connect via Tailscale

From your Freqtrade server, connect using the Tailscale IP:

```python
ws://100.64.x.x:8001
```

Replace `100.64.x.x` with your server's Tailscale IP (check with `tailscale ip`).

## Files

- `websocket_server_hypersync.py` - WebSocket server
- `test_hypersync_delay.py` - Delay test
- `Dockerfile` - Container image
- `docker-compose.yml` - Docker Compose config
