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
- **A `README.md` in every ABI folder** — maintain a Markdown inventory with
  one row per JSON file and the columns **Filename**, **Source**, **Fetched at
  (UTC)** and **Reason to fetch**. Link the exact Sourcify/Etherscan source;
  record the chain, deployment address, implementation address where applicable,
  and the actual retrieval timestamp. Do not confuse a source verification or
  Git import date with the fetch time. Mark missing legacy provenance explicitly
  as unrecorded instead of fabricating it.

| Filename | Source | Fetched at (UTC) | Reason to fetch |
| --- | --- | --- | --- |
| `Contract.json` | Exact verified Sourcify/Etherscan URL; chain and implementation address | Actual ISO 8601 retrieval timestamp | Integration methods/events/errors that require this complete ABI |

### File format

`get_contract()` accepts either shape (see below):

- a **bare ABI array** — the "copy ABI to clipboard" / explorer `getabi` output
  (typical for externally-fetched verified contracts);
- a **solc/Forge compiler artifact** object with `abi`, `bytecode`,
  `linkReferences` keys (used for repo-integrated contracts you deploy).

Store externally-fetched ABIs as the bare array. Keep repo-compiled contracts as
the full compiler artifact so their `bytecode` is available for deployment.

## Fetching a verified ABI

**Do not create stub, minimal, hand-written or selectively reduced ABI JSON
files.** Fetch and store the complete verified ABI from **Sourcify (preferred)**
or **Etherscan**. Keep all functions, events, custom errors and tuple components;
do not rename outputs, select only the calls currently needed, or merge invented
fragments. Extracting the entire `abi` array from a verified metadata response
and formatting it as JSON is allowed; changing its contents is not.

Resolve proxy → implementation before selecting the ABI. Record the deployed
proxy and verified implementation addresses in the folder README. Multiple
implementations/modules need their own verified ABIs rather than a hand-made
combined interface. A complete proxy-plumbing ABI is not a substitute for its
implementation's business interface.

### 1. Sourcify server v2 API (preferred)

Use the current [Sourcify server API](https://docs.sourcify.dev/docs/api/), not
the retired v1 `/files/any` endpoint. The lookup returns the complete verified
ABI, compilation identity and proxy-resolution information:

```python
import json
from pathlib import Path

import requests

chain_id = 8453
implementation = "0x30a9A9654804F1e5b3291a86E83EdeD7cF281618"
url = f"https://sourcify.dev/server/v2/contract/{chain_id}/{implementation}"
response = requests.get(
    url,
    params={"fields": "abi,proxyResolution,compilation"},
    timeout=30,
)
response.raise_for_status()
verified = response.json()
# Inspect proxyResolution first; fetch the implementation if this is a proxy.
Path("eth_defi/abi/euler/EVault.json").write_text(
    json.dumps(verified["abi"], indent=2) + "\n", encoding="utf-8"
)
```

A missing Sourcify verification is a reason to try Etherscan, not to author a
local substitute. Store the source URL, chain, resolved addresses, retrieval time
and reason in the folder inventory. A source's `verifiedAt` is not its fetch time.

### 2. Etherscan v2 unified API (fallback)

Use Etherscan when Sourcify does not have the verified contract. The unified API
accepts a `chainid` and supports `module=contract` with `action=getsourcecode`
and `action=getabi`. `getsourcecode` supplies the proxy/implementation identity;
fetch the verified implementation's full ABI when `Proxy == "1"`.

Keys come from environment variables such as `ETHERSCAN_API_KEY`; never print
or commit their values or authenticated request URLs. Prefer Python requests
with a `params` dictionary to shell commands. Store the complete decoded
`getabi` result, not selected entries, and cite the public verified-code page
(`https://etherscan.io/address/<address>#code`) in the inventory. Retain raw
verification receipts outside Git when useful for auditing.

If neither verified source provides the ABI, report the missing verification
and leave the dependent work explicit. Do not construct JSON from Solidity
signatures, documentation or a small interface copied from project source.
Deliberate ABI-less discovery probes may still use the existing selector helpers;
document why the probe has no verified ABI. This does not permit committing a
stub ABI file.

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

Always use ABI-backed contract proxy classes when the required functions are
available in a stored ABI. Call `contract.functions.method(...).call()` for
reads and use the shared transaction/encoding helpers for writes. Let the ABI
encode arguments and decode return types instead of assembling selectors,
padding addresses or interpreting return words by hand. This keeps calls
readable and maintainable and gives malformed responses the standard decoding
errors. Preserve the caller's distinction between denial, unreadable data and
connection failure when translating those errors.

For batched reads with an available ABI, use
`EncodedCall.from_contract_call(contract.functions.method(...))`. ABI-less
selector probes are for interfaces without an available ABI; document that
reason at the call site rather than duplicating a known interface.

| Role | Helper | Notes |
|------|--------|-------|
| Load the raw ABI/artifact dict | `get_abi_by_filename(fname)` | Returns the parsed JSON (array or `{abi, bytecode}`); cached. |
| Bind an ABI to a **deployed address** (reads/calls) | `get_deployed_contract(web3, fname, address)` | The everyday call — returns a `web3` `Contract` bound to `address` and registers it for tracing. |
| Get a **Contract class** (for deployment / not-yet-deployed) | `get_contract(web3, fname, bytecode=None)` | Handles both bare-array and solc-artifact files; carries `bytecode` for deployment. |
| Contract needing **Forge library linking** | `get_contract_with_forge_libraries(web3, fname, library_addresses)` | Resolves `linkReferences`; pass `ZERO_ADDRESS` when the library is never called on this chain. |
| Contract needing **Hardhat library linking** | `get_linked_contract(...)` | Hardhat `link_references` variant. |
| Standard **ERC-4626** vault binding | `eth_defi.erc_4626.core.get_deployed_erc_4626_contract(web3, address)` | Uses the fixed `lagoon/IERC4626.json` interface; use when you only need the ERC-4626 surface. |
| ABI-backed batched call | `eth_defi.event_reader.multicall_batcher.EncodedCall.from_contract_call(func)` | Preferred for multicall reads when a stored ABI provides the function. |
| **ABI-less** encoded call (classification probes, batched multicall) | `eth_defi.event_reader.multicall_batcher.EncodedCall.from_keccak_signature(address, signature, function, data, extra_data)` | Use for probes without an available ABI; document why the ABI-backed form cannot be used. |
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

Use a known custom-error selector for typed preflight/decoding; fetch a full
verified ABI when a contract binding or error argument schema is needed:

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
- Prefer ABI-backed proxy calls and `EncodedCall.from_contract_call()` whenever
  the stored interface provides the function; do not handcraft equivalent calls.
- Never generate stub ABI JSON or inline fragments as substitutes for complete
  verified external ABIs; fetch Sourcify first, then Etherscan.
- For a proxy, commit the **implementation** ABI, not the proxy ABI.
- Maintain each folder's README inventory: Filename, Source, Fetched at (UTC),
  Reason to fetch; record chain/proxy/implementation identity and do not invent
  missing legacy provenance.
- Regenerate repo-integrated contract ABIs with the compiler via the `Makefile`
  targets (e.g. `make guard safe-integration`, or `make
  compile-projects-and-prepare-abi` for the full set) — never hand-edit a
  generated artifact. Fetch complete verified JSON for external deployments.
- A fixed-block fork regression proves an ABI decodes for the tested route; it
  does not assert a later proxy upgrade is identical.

### Withdrawal-capacity reader ABIs

The capacity readers use complete verified ABI arrays, with individual source
URLs and fetch timestamps in [Euler](euler/README.md), [Fluid](fluid/README.md)
and [IPOR](ipor/README.md). They do not contain hand-declared capacity interfaces.
The Euler binding uses the resolved EVault implementation. Fluid uses a complete
ERC-20-route ABI for `getData()` and a complete native-route ABI for the optional
`NATIVE_TOKEN_ADDRESS()` probe. IPOR uses the verified WithdrawManager ABI for
`getSharesToRelease()`; its manager address is resolved from the vault storage
slots described in the IPOR inventory. These bindings add decoding coverage,
not a claim that all available withdrawal routes have been measured.
