# Webshare proxy

## What is Webshare

[Webshare](https://www.webshare.io/) is a proxy service used by this repository
through the helper module `eth_defi.event_reader.webshare`.

The helper loads proxy credentials from the Webshare API, converts them to
formats used by `requests` and Playwright, and rotates through the proxy pool
when remote services rate limit or block requests.

Proxy support is optional. If `WEBSHARE_API_KEY` is not set, the helper returns
`None` or an empty list and callers continue without proxies.

## Why do we use it

Some upstream services used by data collection scripts rate limit, throttle, or
block repeated requests from a single IP address. Webshare gives us a managed
proxy pool so long-running scanners can distribute requests across different
exit IPs.

The proxy layer also tracks failed proxies locally. When a proxy fails, it is
placed in a grace period and skipped by later runs. This avoids retrying known
bad proxies on every scan cycle.

## Where do we use it

The shared implementation lives in:

```text
eth_defi/event_reader/webshare.py
```

Known callers include:

- `eth_defi/feed/collector.py` for proxy-backed feed fetches.
- `eth_defi/vault/scan_all_chains.py` for vault scanning.
- `eth_defi/hyperliquid/session.py` for Hyperliquid HTTP sessions.
- `scripts/hyperliquid/sync-trade-history.py` for trade history syncing.
- `scripts/hyperliquid/high-freq-vault-metrics.py` for high-frequency vault metric fetching.
- `scripts/erc-4626/scan-vault-posts.py` for vault post scanning.

Enable proxies with:

```shell
export WEBSHARE_API_KEY=your_webshare_api_key_here
```

The proxy mode defaults to `backbone`. Override it with:

```shell
export WEBSHARE_PROXY_MODE=backbone
```

Supported modes are `backbone` and `direct`.

## Limitations

Webshare does not guarantee that every proxy in the pool is usable for every
target service. Individual proxies may be blocked, rate limited, slow, or fail
TLS/HTTP negotiation.

The local failure state is conservative. A proxy that fails is skipped for the
configured grace period, currently 14 days. If every proxy gets marked failed,
callers fall back to direct connections or return no proxy URLs.

The current Webshare API request reads the first page of up to 100 proxies. If
the account has more proxies than that, the helper does not currently paginate
through the full account pool.

The proxy state file is local to the machine running the scanner. Clearing it on
one host does not clear status on another host.

## Diagnostics and how to reset

The proxy state file is:

```shell
~/.tradingstrategy/webshare-proxy-state.json
```

When all proxies are blocked, the log includes a warning with reset
instructions. To inspect the blocked proxies and reset them interactively, run:

```shell
poetry run python scripts/erc-4626/reset-proxy-state.py
```

The script prints the blocked proxy IDs, failure timestamps, failure reasons,
and failure counts. Confirm with `y` when prompted to clear all blocked proxy
state.

For a non-interactive reset, delete the state file:

```shell
rm ~/.tradingstrategy/webshare-proxy-state.json
```

After reset, the next run fetches the Webshare proxy list again and treats all
returned valid proxies as available until new failures are recorded.
