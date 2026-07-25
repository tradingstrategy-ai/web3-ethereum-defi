# Contract ABIs in eth_defi

This directory holds every contract ABI (and, for repo-integrated contracts,
compiler artifact) used by `eth_defi`. **Read this file before fetching a new
ABI or loading/using any ABI file** — it explains what ABIs are, how to obtain
verified ABIs, how to store them here, and which helper you should call for each
role an ABI plays.

## What an EVM ABI is

An **ABI** (Application Binary Interface) is the JSON description of a smart
contract's callable surface: its functions, events, custom errors and their
argument types. The EVM itself only understands raw calldata and 32-byte words;
the ABI is the schema that lets a client:

- **encode** a call — the first 4 bytes of calldata are the *function selector*
  `keccak256("name(type1,type2,...)")[:4]`, followed by ABI-encoded arguments;
- **decode** return values, event `data`/`topics`, and revert payloads (a custom
  error is encoded exactly like a function call: its 4-byte selector plus
  ABI-encoded fields — this is how a revert such as `0x5945ea56` is turned back
  into `InsufficientAmount()`);
- **identify** events — an event's `topic0` is
  `keccak256("Event(type1,type2,...)")`.

An ABI is *not* the contract. It carries no addresses and no state. You still
need the deployed address (and a Web3 connection) to actually call anything.

### Proxies — the single most common mistake

Most modern deployments are **proxies**: the address you interact with
(ERC-1967 / transparent / beacon proxy) holds storage and delegates logic to a
separate **implementation** contract. The proxy's own verified ABI only shows
proxy plumbing (`upgradeTo`, `admin`, …), *not* the business methods. Always
resolve the proxy to its implementation and commit the **implementation ABI**
(explorers expose the implementation address; see below). Record both addresses
in the protocol README.

## Directory layout

- **Top level** `eth_defi/abi/*.json` — shared, cross-protocol contracts
  (`ERC20MockDecimals.json`, `ChainlinkAggregatorV2V3Interface.json`, …).
- **Per protocol** `eth_defi/abi/<protocol>/*.json` — protocol-specific
  interfaces (`lagoon/`, `ipor/`, `plutus/`, `accountable/`, …). Use
  `eth_defi/abi/lagoon/` as the structural reference.
- **Per-protocol `README.md`** — each protocol directory that holds an
  externally-fetched ABI must record its **canonical source**: the chain, the
  proxy address, the implementation address, the explorer/Sourcify URL and the
  fetch date. See `eth_defi/abi/yieldnest/README.md`,
  `eth_defi/abi/plutus/README.md` and `eth_defi/abi/accountable/README.md` for
  the expected format.

### File format

`get_contract()` accepts either shape (see below):

- a **bare ABI array** — the "copy ABI to clipboard" / explorer `getabi` output
  (typical for externally-fetched verified contracts);
- a **solc/Forge compiler artifact** object with `abi`, `bytecode`,
  `linkReferences` keys (used for repo-integrated contracts you deploy).

Store externally-fetched ABIs as the bare array. Keep repo-compiled contracts as
the full compiler artifact so their `bytecode` is available for deployment.

## Fetching a verified ABI

Prefer verified sources in this order: **Sourcify** (decentralised, chain-neutral)
→ **Etherscan-family explorer** (broadest coverage) → **the project's GitHub**.
Always resolve proxy → implementation, and always verify the ABI actually
contains the selectors you expect (compute `keccak` of the signatures) before
committing it. Prefer a Python snippet over `curl` for explorer reads.

### 1. Etherscan v2 unified API (recommended)

One API key works across all supported chains (Ethereum, Arbitrum, Base, BNB,
Polygon, Avalanche, Monad, …) via a `chainid` parameter. Keys come from the
environment (`ETHERSCAN_API_KEY`, plus chain-specific keys such as
`ARBISCAN_API_KEY` / `BASE_ETHERSCAN_API_KEY`); never hardcode them.

- Direct ABI: `GET https://api.etherscan.io/v2/api?chainid={chain}&module=contract&action=getabi&address={addr}&apikey={key}`
- Source + proxy resolution:
  `...&action=getsourcecode&address={addr}` returns `Proxy` and
  `Implementation`; when `Proxy == "1"`, re-fetch the ABI for the
  `Implementation` address.

```python
import json, os, urllib.parse, urllib.request

def fetch_abi(chain_id: int, address: str) -> list:
    key = os.environ["ETHERSCAN_API_KEY"]
    base = "https://api.etherscan.io/v2/api"

    def get(action: str, addr: str) -> dict:
        q = urllib.parse.urlencode({"chainid": chain_id, "module": "contract", "action": action, "address": addr, "apikey": key})
        with urllib.request.urlopen(f"{base}?{q}", timeout=30) as r:
            return json.loads(r.read())

    src = get("getsourcecode", address)["result"][0]
    target = src["Implementation"] if src.get("Proxy") == "1" and src.get("Implementation") else address
    abi_raw = get("getabi", target)["result"]
    return json.loads(abi_raw)  # write this to eth_defi/abi/<protocol>/<Contract>.json
```

### 2. Sourcify

Sourcify hosts decentralised, source-verified metadata (the ABI lives inside the
Solidity metadata JSON at `output.abi`). No API key.

- `GET https://repo.sourcify.dev/contracts/full_match/{chainId}/{address}/metadata.json`
  (fall back to `partial_match/`), then read `metadata["output"]["abi"]`.
- Or the API server: `https://sourcify.dev/server/files/any/{chainId}/{address}`.
- Resolve the proxy's implementation address first (from the explorer or the
  proxy's `implementation()` storage slot), then fetch the implementation.

### 3. GitHub / project export

Some protocols do not verify on an explorer but publish ABIs or interfaces in
their contracts repo (or a docs/SDK export). Commit the application-exported or
repo-exported interface JSON and record the exact source URL and commit/tag in
the protocol README. For a narrowly-scoped adapter that only needs a handful of
stable no-argument view methods, using canonical 4-byte selectors directly (see
`EncodedCall` below) is acceptable instead of committing a full ABI — link the
authoritative source in the module docstring and cover every decoded value with
a fork test.

### Repo-integrated contracts — rebuild with the Makefile, do not fetch

For contracts whose Solidity source lives in this repository (or vendored under
`contracts/`), do **not** fetch the ABI from an explorer — regenerate it with
the compiler so the ABI and bytecode stay in sync with the source. The
`Makefile` targets each compile a project under `contracts/<name>/` (via
`forge build`, `yarn`/`npm`, or `pnpm`) and copy the resulting artifacts into
the matching `eth_defi/abi/<name>/` directory. Never hand-edit a generated
artifact; edit the Solidity and re-run the target.

Common targets (run with `make <target>`):

| Target | Rebuilds into | Source |
|--------|---------------|--------|
| `guard` | `eth_defi/abi/guard/` | GuardV0 / SimpleVaultV0 (`contracts/guard`, forge) |
| `safe-integration` | `eth_defi/abi/safe-integration/` | TradingStrategyModuleV0 Safe/Zodiac module (forge) |
| `terms-of-service` | `eth_defi/abi/terms-of-service/` | Terms-of-service acceptance (forge) |
| `in-house` | `eth_defi/abi/` | Web3-Eth-Defi integration contracts (forge; depends on `enzyme`) |
| `lagoon` | `eth_defi/abi/lagoon/` | Lagoon integration contracts |
| `enzyme` | `eth_defi/abi/enzyme/` | Enzyme protocol (from its GitHub source) |
| `sushi` | `eth_defi/abi/sushi/` | Sushiswap mocks (yarn) |
| `uniswapv3` / `copy-uniswapv3-abi` | `eth_defi/abi/uniswap_v3/` | Uniswap v3 core + periphery (yarn) |
| `aavev3` / `aavev2` / `aavev3_old` | `eth_defi/abi/aave_v3/`, `aave_v2/`, `aave_v3_old/` | Aave deployments |
| `dhedge`, `centre`, `1delta`, `velvet`, `orderly` | `eth_defi/abi/<name>/` | Respective protocol integrations |

Aggregate and housekeeping targets:

- `make compile-projects-and-prepare-abi` — clean and rebuild the full set
  (`clean-abi` + `sushi in-house guard safe-integration copy-uniswapv3-abi
  aavev3 enzyme dhedge centre 1delta`). Use after a Solidity or dependency
  change that touches multiple integrations.
- `make clean-abi` — remove generated ABI outputs before a fresh rebuild.

Building integrated contracts needs the relevant toolchains installed (Foundry
`forge`, plus `yarn`/`npm`/`pnpm` for the JS-built projects). Inspect the target
in the `Makefile` before running it, since some (`enzyme`, `aavev3`) install
dependencies and can take several minutes.

## Using ABI files — API reference

All loaders live in `eth_defi.abi` and are cached in-process. Pass the
repo-relative filename (e.g. `"lagoon/Vault.json"`); a leading slash means an
absolute filesystem path.

| Role | Helper | Notes |
|------|--------|-------|
| Load the raw ABI/artifact dict | `get_abi_by_filename(fname)` | Returns the parsed JSON (array or `{abi, bytecode}`); cached. |
| Bind an ABI to a **deployed address** (reads/calls) | `get_deployed_contract(web3, fname, address)` | The everyday call — returns a `web3` `Contract` bound to `address` and registers it for tracing. |
| Get a **Contract class** (for deployment / not-yet-deployed) | `get_contract(web3, fname, bytecode=None)` | Handles both bare-array and solc-artifact files; carries `bytecode` for deployment. |
| Contract needing **Forge library linking** | `get_contract_with_forge_libraries(web3, fname, library_addresses)` | Resolves `linkReferences`; pass `ZERO_ADDRESS` when the library is never called on this chain. |
| Contract needing **Hardhat library linking** | `get_linked_contract(...)` | Hardhat `link_references` variant. |
| Standard **ERC-4626** vault binding | `eth_defi.erc_4626.core.get_deployed_erc_4626_contract(web3, address)` | Uses the fixed `lagoon/IERC4626.json` interface; use when you only need the ERC-4626 surface. |
| **ABI-less** encoded call (classification probes, batched multicall) | `eth_defi.event_reader.multicall_batcher.EncodedCall.from_keccak_signature(address, signature, function, data, extra_data)` | Encodes a call from a raw 4-byte selector without loading an ABI; the canonical pattern for detection probes and dense multicall reads. |
| Event **topic0** signature | `get_topic_signature_from_event(event)` | `keccak` of the event signature for log filtering/decoding. |
| Function **selector** | `get_function_selector(func)` | 4-byte selector of a bound `ContractFunction`. |
| **Encode** a call by signature | `encode_function_call(func, args)` / `encode_with_signature(signature, args)` | Build raw calldata. |
| **Decode** outputs / args | `decode_function_output(func, data)` / `decode_function_args(func, data)` | Turn raw return/calldata into Python values. |

### Typical patterns

Bind a protocol vault to its address and call it:

```python
from eth_defi.abi import get_deployed_contract

vault = get_deployed_contract(web3, "yieldnest/Vault.json", "0x01ba69727e2860b37bc1a2bd56999c1afb4c15d8")
max_redeem = vault.functions.maxRedeem(owner).call()
```

Load a custom-error selector for typed preflight/decoding (fragment ≤ 1–2
entries may stay inline; larger sets belong in the committed ABI):

```python
from hexbytes import HexBytes

# keccak("ExceededMaxRedeem(address,uint256,uint256)")[:4]
EXCEEDED_MAX_REDEEM_SELECTOR = HexBytes("0xb8b8b59c")
```

Protocol-detection probe without an ABI:

```python
from web3 import Web3
from eth_defi.event_reader.multicall_batcher import EncodedCall

probe = EncodedCall.from_keccak_signature(
    address=address,
    signature=Web3.keccak(text="someUniqueGetter()")[0:4],
    function="someUniqueGetter",
    data=b"",
    extra_data=None,
)
```

## Rules recap (see `CLAUDE.md` → ABIs)

- Store ABIs under `eth_defi/abi/<protocol>/`; load through the helpers above.
- Do not inline ABIs in Python beyond a one- or two-entry fragment.
- For a proxy, commit the **implementation** ABI, not the proxy ABI.
- Record the canonical source (chain, proxy, implementation, URL, date) in the
  protocol `README.md`.
- Regenerate repo-integrated contract ABIs with the compiler via the `Makefile`
  targets (e.g. `make guard safe-integration`, or `make
  compile-projects-and-prepare-abi` for the full set) — never hand-edit a
  generated artifact. Commit verified / exported JSON for external deployments.
- A fixed-block fork regression proves an ABI decodes for the tested route; it
  does not assert a later proxy upgrade is identical.
