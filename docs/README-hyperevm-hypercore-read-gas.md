# HyperEVM HyperCore read gas and `-32003 out of gas` multicall failures

This document explains why HyperEVM (Hyperliquid, chain id 999) historical vault
price scans abort with `-32003 out of gas: gas required exceeds: 300000000`, why the
address named in the failing batch is **not** a broken contract, and why we do not
blacklist it.

It backs the line comments in
[`eth_defi/event_reader/multicall_batcher.py`](../eth_defi/event_reader/multicall_batcher.py)
(`WTF_RETRY_EXCEPTIONS_MESSAGE_CLUES`, the `MulticallRetryable` fallback),
[`eth_defi/erc_4626/vault_protocol/hyperdrive_hl/vault.py`](../eth_defi/erc_4626/vault_protocol/hyperdrive_hl/vault.py),
[`eth_defi/erc_4626/classification.py`](../eth_defi/erc_4626/classification.py) and
[`eth_defi/vault/risk.py`](../eth_defi/vault/risk.py).

It is the gas-accounting sibling of
[`README-hyperevm-goldsky-failure.md`](README-hyperevm-goldsky-failure.md), which
covers the eRPC consensus (`not enough agreement among responses`) failure mode on
the same chain.

## Symptom

`vault-scanner-looped` loops on the same HyperEVM blocks, rotating providers and
shrinking the batch, without ever finishing the chain:

```
Switched RPC providers edge.goldsky.com -> hyperliquid-mainnet.g.alchemy.com,
cause: Last exception: Multicall failed for chain 999
Block 44,367,203, batch size: 40: {'code': -32003, 'message': 'out of gas: gas required exceeds: 600000000'}
...
Block 44,320,403, batch size: 13: {'code': -32003, 'message': 'out of gas: gas required exceeds: 300000000'}
```

Observed over blocks 44,291,603 – 44,367,203 on 2026-08-28, against the
`edge.goldsky.com` / `hyperliquid-mainnet.g.alchemy.com` / `lb.drpc.live` fallback
mix. The reduced batches printed 11 and then 3 vault addresses, which is not enough
to identify the culprit by reading the log alone.

## Isolating the address

The failing `eth_call` payloads in the log are Multicall3
`tryBlockAndAggregate(false, calls)` (`0x399542e9`) blobs, so they can be replayed
verbatim, address group by address group, against each configured provider:

| Batch replayed at block 44,367,203 | goldsky | dRPC | Alchemy |
|------------------------------------|---------|------|---------|
| full 40-call batch                 | out of gas | out of gas | ok |
| full 40-call batch minus `0x4d0fF6a0…` | ok | ok | ok |
| 13-call reduced batch              | out of gas | out of gas | ok |
| `0x4d0fF6a0…` 4 calls, duplicated ×2 | out of gas | out of gas | ok |
| every other address, calls duplicated ×16 | ok | ok | ok |

Group bisection of the 13-call batch (`A` = `0x4aBFd796…`, `S` = `0x4d0fF6a0…`,
`B` = `0x7188D14A…`): `A` ok, `S` ok, `B` ok, `A+S` ok, `A+B` ok, `S+B` fails — and
`S` alone with its four calls duplicated fails. So a single address accounts for the
whole failure:

**`0x4d0fF6a0DD9f7316b674Fb37993A3Ce28BEA340e` — Hyperdrive Liquid Staked Hype
(`HYPED`)**, an ERC-1967 proxy whose implementation
`0x6CA870794cd307243FCc8711899e46C74B2D3f2f` is source-verified as
`StakingVaultUpgradeable` (solc 0.8.28, fetched through the Etherscan v2 unified API
with `chainid=999`).

Only three of its selectors are affected, and `totalSupply()` never is:

| Selector | Method | Behaviour |
|----------|--------|-----------|
| `0x01e1d114` | `totalAssets()` | heavy / reverts historically |
| `0x07a2d13a` | `convertToAssets(uint256)` | heavy / reverts historically |
| `0x402d267d` | `maxDeposit(address)` | heavy / reverts historically |
| `0x18160ddd` | `totalSupply()` | always cheap, always works |

## Root cause — the vault reads HyperCore through precompiles

The verified source explains the split exactly. `totalAssets()` delegates to
`CoreControllerLib`:

```solidity
function totalAssets(StakingVaultUpgradeable.StakingStorage storage $) public view returns (uint256 total) {
  address[] memory proxies = $.proxies.values();
  uint256 blockNumber = compositeBlockNumber();
  for (uint256 i = 0; i < proxies.length; i++) {
    total += totalAssets($, proxies[i], blockNumber);
  }
  total += address(this).balance - $.totalClaimableAssets;
}
```

and each per-proxy step reads live HyperCore state:

```solidity
total += Wei.wrap(CoreReaderLib.readSpotBalance(proxy, $.tokenIndex).total).fromWei();
CoreReaderLib.DelegatorSummary memory delegatorSummary = CoreReaderLib.readDelegatorSummary(proxy);
total += Wei.wrap(delegatorSummary.delegated).fromWei();
total += Wei.wrap(delegatorSummary.undelegated).fromWei();
total += Wei.wrap(delegatorSummary.totalPendingWithdrawal).fromWei();
```

`compositeBlockNumber()` is itself a HyperCore read:

```solidity
function compositeBlockNumber() private view returns (uint256) {
  return (uint256(CoreReaderLib.readL1BlockNumber()) << 128) | uint128(block.number);
}
```

`CoreReaderLib` (`@ambitlabs/hypercore`) staticcalls the HyperCore read precompiles:

| Precompile | Constant | Used by |
|------------|----------|---------|
| `0x…0801` | `PRECOMPILE_ADDRESS_SPOT_BALANCE` | `readSpotBalance()` |
| `0x…0805` | `PRECOMPILE_ADDRESS_DELEGATOR_SUMMARY` | `readDelegatorSummary()` |
| `0x…0809` | `PRECOMPILE_ADDRESS_L1_BLOCK_NUMBER` | `readL1BlockNumber()` |

`getProxies()` on the live vault returns **3** staking proxies, so one
`totalAssets()` performs **1 + 2 × 3 = 7 HyperCore precompile staticcalls**.
`maxDeposit()` calls `totalAssets()` directly and `convertToAssets()` reaches it
through `ERC7535Upgradable`, whereas `totalSupply()` is plain ERC-20 storage. That
is precisely the observed pattern: HyperCore-dependent selectors misbehave, the pure
EVM selector never does.

### Evidence 1 — the historical revert is a HyperCore read failure

At historical blocks the call reverts with `0x18c34104`, which is
`keccak("ReadFailure(address)")[:4]` — the custom error `CoreReaderLib` raises when
a precompile staticcall fails. Its decoded argument is
`0x0000000000000000000000000000000000000809`, the L1 block-number precompile, i.e.
the very first HyperCore read in `totalAssets()`.

Availability is a per-node, per-moment property rather than a property of the block:

```
hyperliquid-mainnet.g.alchemy.com  head      -> ok, 2616.51 HYPE
hyperliquid-mainnet.g.alchemy.com  head-200  -> ReadFailure(0x…0809)
edge.goldsky.com                   head      -> ReadFailure(0x…0809)
edge.goldsky.com                   head-200  -> ok, 2616.51 HYPE
lb.drpc.org                        head      -> ReadFailure(0x…0809)
```

A binary search on Alchemy on 2026-08-28 put the boundary at a single block:
`totalAssets()` reverted at every block up to 44,372,819 and succeeded from
44,372,820 — about two minutes behind the head. HyperCore state is served from the
node's live view, so **no provider can reconstruct this vault's NAV for an arbitrary
past block**, the same way Monad cannot serve arbitrary-depth historical state.

The already-blacklisted Hyperdrive HLP (`0x6ED613E8…`) and Gamma Symphony
(`0x2b37f356…`) entries in `eth_defi/vault/risk.py` record the same `0x18c34104`
revert; this document is the explanation of what that error actually is.

### Evidence 2 — the gas figure is provider accounting, not real execution

Real execution of `totalAssets()` is cheap. Binary searching the `gas` field of a
direct `eth_call` on Alchemy at a block where the HyperCore read succeeds gives
**~117,000 gas** for the whole call, i.e. roughly 17k per HyperCore read.

The numbers the scanner trips over come from the providers' gas accounting for
those precompile staticcalls:

| Provider | Measurement |
|----------|-------------|
| Alchemy | `eth_call` executes in ~117k gas; `estimate_gas` of the same call inside Multicall3 reports 2,843,730 |
| goldsky | 4 copies of `totalAssets()` fit in one multicall under the 300M cap, 5 do not → ≥60M attributed per call |
| dRPC | same as goldsky; `maxDeposit()` is heavy there too |

On goldsky and dRPC the rejection is independent of the cap we send: caps of 1M,
10M, 30M, 100M and 299M are all answered with
`out of gas: gas required exceeds: <cap>`. So the node decides up front that the
batch needs more gas than allowed; it is not a real EVM out-of-gas during
execution. A handful of HyperCore reads is enough to push a normal 40-call scanner
batch over a 300M/600M cap, which is why the failure looks random, flaps between
fallbacks, and survives batch-size reduction until the batch happens to exclude
this vault.

## Why we do not blacklist it

Same conclusion as Cause A / Cause B in
[`README-hyperevm-goldsky-failure.md`](README-hyperevm-goldsky-failure.md): the
contract is not dead or cooked.

- Hyperdrive is a real, DefiLlama-listed HyperEVM protocol, and this specific
  implementation is source-verified (`StakingVaultUpgradeable`), contrary to the
  older "unverified contracts" note carried in our Hyperdrive metadata.
- The vault works at head: `totalAssets()` is 2,616.5 HYPE (about $218k at the
  2026-08-28 HYPE mid of $83.47), `totalSupply()` 2,555.6 HYPED, share price 1.0238
  HYPE per HYPED.
- The failure is a provider-side gas-accounting and HyperCore-state-window artefact,
  not a permanent property of the address. Alchemy executes the same batch fine.

Blacklisting would permanently drop a live vault from reports for a transient RPC
condition. The existing machinery is the mitigation: `MulticallRetryable` batch-size
reduction, provider rotation, and the HyperEVM Alchemy pin described in the
companion document.

What we do accept is that **historical NAV for HyperCore-reading vaults is only
obtainable near the head**. Backfilling their share price deep into the past is not
possible on any provider, so gaps before the HyperCore read window must not be
"repaired" by rescanning.

## Reproducing

```python
from eth_abi import encode
from eth_utils import to_checksum_address
from web3 import Web3, HTTPProvider

MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"
VAULT = to_checksum_address("0x4d0fF6a0DD9f7316b674Fb37993A3Ce28BEA340e")

# tryBlockAndAggregate(false, [(vault, totalAssets())] * n)
def payload(n: int) -> str:
    calls = [(VAULT, bytes.fromhex("01e1d114"))] * n
    return "0x399542e9" + encode(["bool", "(address,bytes)[]"], [False, calls]).hex()

web3 = Web3(HTTPProvider("https://edge.goldsky.com/..."))  # JSON_RPC_HYPERLIQUID entry
web3.eth.call({"to": MULTICALL3, "data": payload(4)}, block_identifier=44_367_203)  # ok
web3.eth.call({"to": MULTICALL3, "data": payload(8)}, block_identifier=44_367_203)  # -32003 out of gas
```

Split `JSON_RPC_HYPERLIQUID` on spaces and take one endpoint per provider; the
space-separated fallback format only works with `create_multi_provider_web3()`.

## Related

- [`README-hyperevm-goldsky-failure.md`](README-hyperevm-goldsky-failure.md) — the
  eRPC consensus failure mode on the same chain, including the Alchemy failover pin.
- [`eth_defi/erc_4626/vault_protocol/hyperdrive_hl/vault.py`](../eth_defi/erc_4626/vault_protocol/hyperdrive_hl/vault.py)
  — `HyperdriveVault`, whose docstring points here.
- [`eth_defi/vault/risk.py`](../eth_defi/vault/risk.py) — the Hyperdrive HLP and
  Gamma Symphony blacklist entries that share the `0x18c34104` revert, plus the note
  that `HYPED` is deliberately kept off that list.
