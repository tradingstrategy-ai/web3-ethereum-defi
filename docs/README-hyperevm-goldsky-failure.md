# HyperEVM goldsky eRPC consensus failure

This document explains why HyperEVM (Hyperliquid, chain id 999) vault scans fail
with the eRPC error `not enough agreement among responses`, why retrying does not
help, and the failover special case we added to work around it.

It backs the line comments in
[`eth_defi/event_reader/multicall_batcher.py`](../eth_defi/event_reader/multicall_batcher.py)
(`resolve_hyperevm_consensus_failover`, `pin_fallback_provider_by_host`,
`ERPC_CONSENSUS_DISAGREEMENT_CLUE`, `HYPEREVM_CHAIN_ID`).

## Symptom

The vault discovery / price scan aborts the whole HyperEVM chain with:

```
eth_defi.vault.scan_all_chains  ERROR  Hyperliquid: Out of multicall retries, even
after dropping multicall batch size to 1 and switching providers, bailing out.
...
chain 999, block 37,395,809, batch size: 1.
Exception: MulticallRetryable: Multicall failed for chain 999
{'code': -32603, 'message': 'not enough agreement among responses'}
```

All five retries (including batch-size-1 and provider switches) fail with the same
`-32603 not enough agreement among responses`, then the scan bails out.

## Root cause

The scan's primary HyperEVM provider is **goldsky's eRPC endpoint
(`edge.goldsky.com`) running in consensus mode** (`x-erpc-consensus-slots: 5`). It
fans each `eth_call` out to ~5 upstream nodes and only returns a result when enough
of them return **byte-identical** responses. The upstreams seen in the response
headers:

```
x-erpc-upstreams: systx-quicknode-hyperevm; standard-quicknode-hyperevm;
                  systx-chainstack-hyperevm; standard-chainstack-hyperevm;
                  systx-nirvana-hyperevm-us-chi-1
```

For some vaults these upstreams **genuinely disagree**, so eRPC returns
`not enough agreement among responses`. There are two independent reasons the
upstreams return different bytes for the same call, both verified on-chain by
comparing a single node (Alchemy, `hyperliquid-mainnet.g.alchemy.com`) against the
goldsky consensus endpoint:

### Cause A — node-dependent return values (HyperCore-oracle vaults)

`totalAssets()` / `convertToAssets()` on oracle-priced vaults read **live HyperCore
state** (mark/oracle price) via precompiles. Each upstream reads its own momentary
HyperCore view, so the returned value differs between nodes and consensus fails —
**at every block, including `latest`**, while constant reads pass:

```
block=latest (goldsky consensus)
  decimals()    : OK
  totalSupply() : OK
  totalAssets() : FAIL  not enough agreement among responses
```

### Cause B — divergent revert serialisation past the execution window

Probe calls that revert get serialised inconsistently across upstreams. For the
identical call at a historical block one node returns `code 3 execution reverted`
while another returns
`-32603 InvalidTransaction(Revert(RevertError { output: None }))`. This kicks in
**past a sharp ~tip-126 boundary** — these RPC nodes only keep full execution state
for ~128 recent blocks; beyond that the revert path diverges:

```
selector 0x2d06b331, vault 0x9b3a8f7c, single node (Alchemy), by block age:
  tip-100  : code 3 execution reverted
  tip-200  : -32603 InvalidTransaction(Revert(...))      <- divergence starts
  tip-1232 : -32603 InvalidTransaction(Revert(...))
  @ latest both nodes agree on code 3
```

The failing scan read block 37,395,809, which was **1,232 blocks behind the live
tip** — well past the ~128-block window — because the discovery scan captures
`end_block = web3.eth.block_number` at scan start
([`eth_defi/erc_4626/lead_scan_core.py`](../eth_defi/erc_4626/lead_scan_core.py),
`scan_leads`) but executes the reads ~20–40 min later, by which time the tip has
moved far ahead.

`tryBlockAndAggregate` concatenates all sub-call results into one blob, so a single
divergent sub-call (Cause A or Cause B) poisons the whole batch.

## Why retrying does not help

The disagreement is **intermittent and upstream-pool driven, not a transient
per-request glitch**. Verified across two windows hours apart:

- In one window, `totalAssets()` on `0x26672d3e`, `0x8a5b15ea`, `0x9c59a93`,
  `0x9b3a8f7c` and several full captured batches returned `NO-AGREEMENT`
  reproducibly at every block.
- Hours later the **same** read-only calls and batches all passed — nothing on
  chain had changed; the condition flipped on its own as the upstream pool
  re-synced.

So the failure clusters on the same handful of vaults (those whose reads expose
per-node variance) but is **not** a permanent property of any address. It is also
not a "block too fresh" problem — `get_almost_latest_block_number()` (tip-4) would
not avoid it, since Cause A fails at `latest` too. Retrying or randomly cycling
back onto goldsky's consensus endpoint within the ~5-retry budget cannot outrun a
multi-minute pool divergence.

Because no single address is persistently broken, these vaults are **not** added to
`_BROKEN_VAULT_CONTRACTS` (that list is for genuinely dead/cooked contracts). They
are legitimate, working vaults — blacklisting them would permanently drop real data
for a transient RPC condition.

## The fix — pin HyperEVM consensus failures to Alchemy

When a HyperEVM multicall fails with `not enough agreement among responses`, instead
of randomly switching providers (which keeps landing back on goldsky), we **pin all
retries to the Alchemy single node**, which returns a usable answer without
cross-node consensus.

Detection is deliberately narrow (`resolve_hyperevm_consensus_failover`):

1. **Chain id is 999** (`HYPEREVM_CHAIN_ID`).
2. The error contains `not enough agreement among responses`
   (`ERPC_CONSENSUS_DISAGREEMENT_CLUE`).
3. The `FallbackProvider` mix contains **both** a goldsky and an Alchemy endpoint
   (matched on `get_provider_name()` host substrings `goldsky` / `alchemy`).

If all three hold, `pin_fallback_provider_by_host(fallback_provider, "alchemy")`
deterministically selects the Alchemy provider for the retries. Otherwise the normal
random switch is used, so the change is inert on every other chain and provider mix.

### Providers / nodes involved

| Role | Endpoint | Notes |
|------|----------|-------|
| Consensus endpoint that fails | `edge.goldsky.com` | eRPC consensus mode over 5 upstreams (quicknode×2, chainstack×2, nirvana) |
| Single node we fail over to | `hyperliquid-mainnet.g.alchemy.com` | No consensus; returns one node's answer directly |
| Other single node in mix | `lb.drpc.live` / `lb.drpc.org` | Also single-node, but we pin to Alchemy per design |

### Vault addresses observed in the failure (chain 999)

Recorded for reference only — these are **working vaults**, not blacklisted:

```
0x08af2526bb162a99719754d81b7b4b21665064a0
0x0d13f5149b2e736f807f9c4b0ecdf8644e842af9
0x26672d3ef84b53ddbcd3732e9bc96c3712ad758d
0x54112417c5838470ad867c3df6ad8bbae1fd58a7
0x5baceb306193c2315ed29bb3d9d33dd335739eec
0x60c13af3b33db0348cdb8f62d4c2d62aa49f0efd
0x6714cd43536e7e242923ace3d301a3311dbca6bb
0x8a5b15ea614fbdecb19f23213625d26a3460e101
0x9b3a8f7cec208e247d97dee13313690977e24459
0x9c59a9389d8f72de2cdaf1126f36ea4790e2275e
0xc4b2aaf0d3f0ab607a5978bdd01886dd0191e339
0xc55fab3ddcab42b6dd2358fbdc59950f832f67fc
0xf44f49e6577b3934f981c6f0629d15154d2606e6
0xffeaca6f5af9a30bb22d962c105877727e331b8e
0x4107dd3e907a26ee6297bad486e887c51e6a918a   (the batch-size-1 fatal bail-out target)
```

## Related

- [`eth_defi/event_reader/multicall_batcher.py`](../eth_defi/event_reader/multicall_batcher.py)
  — retry loop and the failover helpers.
- `WTF_RETRY_EXCEPTIONS_MESSAGE_CLUES` in the same file still classifies
  `not enough agreement among responses` as retryable; the HyperEVM pin makes those
  retries actually productive instead of cycling back onto goldsky.
