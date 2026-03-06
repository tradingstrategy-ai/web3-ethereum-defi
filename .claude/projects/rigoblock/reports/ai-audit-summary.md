# Rigoblock SmartPool — AI audit summary

**Target:** Rigoblock SmartPool v4.1.1 (pool/fund management protocol)
- Proxy: `0xEfa4bDf566aE50537A507863612638680420645C` (Solidity 0.8.17)
- Implementation: `0x1DB955265B8DC18715cAb12E805F9b71Fa545420` (Solidity 0.8.28)
- Chain: Ethereum mainnet
- 45 Solidity files, ~3,143 lines (~1,629 nSLOC)

**Date:** 2026-03-05

**Auditing pipelines used:** 9 skill repos (Trail of Bits, Pashov, SCV-Scan, Forefy, QuillAI, Archethect, HackenProof, Cyfrin, Auditmos) + Slither + Aderyn static analysis

---

## Deduplicated findings (MEDIUM and above)

| # | Severity | Finding | File(s) | Detected by (count) |
|---|----------|---------|---------|---------------------|
| D-1 | **CRITICAL** | Single EOA pool owner — no multisig, no timelock | `MixinOwnerActions.sol:42-45` | TrailOfBits, Forefy, Cyfrin, SCV-Scan, HackenProof, QuillAI, Auditmos, Archethect, Pashov **(9/9)** |
| D-2 | **CRITICAL** | Fallback delegatecall to governance-approved adapters enables arbitrary storage writes | `MixinFallback.sol:28-68` | TrailOfBits, Forefy, SCV-Scan, Pashov, HackenProof, QuillAI, Cyfrin **(7/9)** |
| D-3 | **HIGH** | `updateUnitaryValue()` lacks reentrancy protection — publicly callable, writes NAV to storage | `MixinActions.sol:80-90` | TrailOfBits, Forefy, SCV-Scan, HackenProof, QuillAI, Cyfrin, Auditmos, Archethect, Pashov **(9/9)** |
| D-4 | **HIGH** | Unsafe `uint256` → `uint208` downcast silently truncates user balances | `MixinActions.sol:200,206,270,283` | SCV-Scan, TrailOfBits, Forefy, HackenProof, QuillAI, Cyfrin, Auditmos, Archethect **(8/9)** |
| D-5 | **HIGH** | `getStorageAt`/`getStorageSlotsAt` expose arbitrary storage reads with no access control | `MixinStorageAccessible.sol:10-37` | TrailOfBits, Cyfrin, SCV-Scan, Forefy, HackenProof **(5/9)** |
| D-6 | **MEDIUM** | ERC-20 `transfer`/`transferFrom`/`approve` are silent no-ops returning `false` | `MixinAbstract.sol:9-18` | TrailOfBits, Cyfrin, SCV-Scan, Forefy, HackenProof, QuillAI, Archethect, Auditmos **(8/9)** |
| D-7 | **MEDIUM** | Fee-on-transfer/rebasing tokens cause accounting discrepancies in mint | `MixinActions.sol:151` | TrailOfBits, SCV-Scan, Pashov **(3/9)** |
| D-8 | **MEDIUM** | Oracle price manipulation via flash loan during mint/burn with non-base tokens | `MixinActions.sol:163-165` | TrailOfBits, Pashov, QuillAI, Cyfrin, Forefy **(5/9)** |
| D-9 | **MEDIUM** | Virtual supply manipulation — unbounded positive inflation, 8x amplification on negative | `VirtualStorageLib.sol:24-26`, `NavImpactLib.sol:71-77` | Forefy, HackenProof, QuillAI **(3/9)** |
| D-10 | **MEDIUM** | First-depositor share inflation attack — no dead shares or virtual offset | `MixinPoolValue.sol:53-54`, `MixinActions.sol:168` | Forefy, Archethect, Auditmos **(3/9)** |
| D-11 | **MEDIUM** | `safeTransferNative` uses 2300 gas limit — fails for smart contract wallets | `SafeTransferLib.sol:17-19` | SCV-Scan, Forefy, QuillAI **(3/9)** |
| D-12 | **MEDIUM** | Owner can change spread/fees instantly — no timelock, sandwich attack on locked holders | `MixinOwnerActions.sol:58-75,168-173` | Forefy, QuillAI, Cyfrin, Auditmos **(4/9)** |
| D-13 | **MEDIUM** | Fee collector activation reset on every mint — perpetual lockout DoS | `MixinActions.sol:199-203` | HackenProof, Cyfrin **(2/9)** |
| D-14 | **MEDIUM** | Proxy constructor discards `delegatecall` success boolean | `Contract.sol:104-107` | TrailOfBits, Cyfrin **(2/9)** |
| D-15 | **MEDIUM** | Single-step ownership transfer — no `Ownable2Step` pattern | `MixinOwnerActions.sol:176-182` | Auditmos, Cyfrin **(2/9)** |
| D-16 | **MEDIUM** | Silent 0-balance fallback for failing token `balanceOf` deflates NAV | `MixinPoolValue.sol:182-187` | TrailOfBits, Auditmos **(2/9)** |
| D-17 | **MEDIUM** | NAV manipulation via direct ETH/token donation (spot balance dependency) | `MixinPoolValue.sol:170-189` | QuillAI, Archethect **(2/9)** |
| D-18 | **MEDIUM** | Divide-before-multiply precision loss in burn revenue | `MixinActions.sol:230,249` | Archethect (Slither) **(1/9)** |
| D-19 | **MEDIUM** | Read-only reentrancy — stale `unitaryValue` during NAV update window | `MixinPoolState.sol:99-106` | Auditmos **(1/9)** |
| D-20 | **MEDIUM** | `try/catch` on `IMinimumVersion` allows adapters to bypass version check | `MixinFallback.sol:42-44` | QuillAI **(1/9)** |
| D-21 | **MEDIUM** | `purgeInactiveTokensAndApps` DoS — failing app query blocks entire purge | `MixinOwnerActions.sol:77-137` | QuillAI **(1/9)** |
| D-22 | **MEDIUM** | Inconsistent token activation tracking — NAV excludes newly deposited token | `MixinOwnerActions.sol:140-154`, `MixinActions.sol:155-157` | Cyfrin **(1/9)** |
| D-23 | **MEDIUM** | Owner can selectively purge tokens to hide assets from NAV | `MixinOwnerActions.sol:77-137` | Forefy **(1/9)** |
| D-24 | **MEDIUM** | Proxy has no explicit upgrade mechanism — depends on `eUpgrade` extension | `Contract.sol:92-100` | Cyfrin **(1/9)** |
| D-25 | **MEDIUM** | `unchecked` activation timestamp with `uint48` truncation | `MixinActions.sol:186-188` | TrailOfBits, SCV-Scan **(2/9)** |
| D-26 | **MEDIUM** | Adapter fallback does not verify target address has code | `MixinFallback.sol:36-68` | HackenProof **(1/9)** |
| D-27 | **MEDIUM** | Owner can set malicious KYC provider to selectively block mints | `MixinOwnerActions.sol:157-165` | TrailOfBits **(1/9)** |

---

## Findings by severity

| Severity | Count |
|----------|-------|
| CRITICAL | 2 |
| HIGH | 3 |
| MEDIUM | 22 |
| **Total** | **27** |

---

## Cross-tool detection matrix

The table below shows which tools detected each deduplicated finding (severity MEDIUM+):

| Finding | ToB | Pashov | SCV | Forefy | Quill | Archethect | HackenP | Cyfrin | Auditmos |
|---------|:---:|:------:|:---:|:------:|:-----:|:----------:|:-------:|:------:|:--------:|
| D-1 EOA owner | C | * | * | C | M | * | M | H | M |
| D-2 Delegatecall adapters | C | M | H | H | M | — | M | M | — |
| D-3 updateUnitaryValue | H | M | H | H | M | M | M | M | M |
| D-4 uint208 truncation | H | — | C | H | M | M | M | M | M |
| D-5 Storage reads | H | — | M | M | — | — | M | H | — |
| D-6 ERC-20 no-ops | M | — | M | M | M | M | M | H | M |
| D-7 Fee-on-transfer | M | M | H | — | — | — | — | — | — |
| D-8 Oracle manipulation | M | M | — | M | M | — | M | M | — |
| D-9 Virtual supply | — | — | — | H | M | — | M | — | — |
| D-10 First depositor | — | — | — | M | — | M | — | — | M |
| D-11 2300 gas limit | — | — | M | M | M | — | — | — | — |
| D-12 Fee sandwich | — | — | — | M | M | — | — | M | M |
| D-13 Fee collector reset | — | — | — | — | — | — | M | M | — |
| D-14 Proxy success | M | — | — | — | — | — | — | M | — |
| D-15 Ownable2Step | — | — | — | — | — | — | — | M | M |
| D-16 Silent balanceOf 0 | M | — | — | — | — | — | — | — | M |
| D-17 Donation NAV | — | — | — | — | M | M | — | — | — |
| D-18 Divide-before-multiply | — | — | — | — | — | M | — | — | — |
| D-19 Read-only reentrancy | — | — | — | — | — | — | — | — | M |
| D-20 Version bypass | — | — | — | — | M | — | — | — | — |
| D-21 Purge DoS | — | — | — | — | M | — | — | — | — |
| D-22 Token activation | — | — | — | — | — | — | — | M | — |
| D-23 Purge asset hiding | — | — | — | M | — | — | — | — | — |
| D-24 No upgrade path | — | — | — | — | — | — | — | M | — |
| D-25 uint48 timestamp | M | — | M | — | — | — | — | — | — |
| D-26 No code check | — | — | — | — | — | — | M | — | — |
| D-27 KYC manipulation | M | — | — | — | — | — | — | — | — |

Legend: **C** = Critical, **H** = High, **M** = Medium, **\*** = noted/discussed but not separate finding, **—** = not detected

---

## Tool detection statistics

| Tool | Findings detected | Unique findings | CRITICAL | HIGH | MEDIUM |
|------|:-----------------:|:---------------:|:--------:|:----:|:------:|
| Trail of Bits | 14 | 1 (D-27) | 2 | 3 | 6 |
| Pashov | 4 | 0 | 0 | 0 | 4 |
| SCV-Scan | 9 | 0 | 1 | 3 | 5 |
| Forefy | 10 | 1 (D-23) | 1 | 4 | 5 |
| QuillAI | 12 | 2 (D-20, D-21) | 0 | 0 | 12 |
| Archethect | 6 | 1 (D-18) | 0 | 0 | 6 |
| HackenProof | 9 | 1 (D-26) | 0 | 0 | 9 |
| Cyfrin | 11 | 2 (D-22, D-24) | 0 | 2 | 8 |
| Auditmos | 11 | 1 (D-19) | 0 | 0 | 11 |

**Consensus findings** (detected by 5+ tools): D-1, D-2, D-3, D-4, D-5, D-6, D-8

---

## Key deployment risks (from deployment.md)

| Risk | Detail |
|------|--------|
| **Pool owner is EOA** | `0xcA9F5049c1Ea8FC78574f94B7Cf5bE5fEE354C31` — single key, no multisig, no timelock |
| **Authority governance** | `0xe35129A1E0BdB913CF6Fd8332E9d3533b5F41472` — controls adapter whitelist; compromise = all pools affected |
| **Implementation locked** | `0x1DB955265B8DC18715cAb12E805F9b71Fa545420` — owner = address(0), upgrade via extension only |
| **TokenJar** | `0xA0F9C380ad1E1be09046319fd907335B2B452B37` — if this is a complex contract, 2300 gas limit could block operations |
| **EVM version** | Cancun — uses transient storage (EIP-1153), which means 2300 gas stipend no longer prevents TSTORE reentrancy |

---

## Overall assessment

The Rigoblock SmartPool implementation is architecturally sophisticated with good security foundations: transient-storage reentrancy guards, minimum lockup periods, minimum order sizes, spread-based slippage protection, and ERC-7201 named storage slots. No finding enables immediate theft of funds by an unprivileged attacker.

The two **critical** risks are both **governance/trust assumptions** rather than code bugs:
1. The single EOA owner with no timelock or multisig (detected by all 9 tools)
2. The delegatecall-to-adapter trust chain depending on Authority governance

The three **high-severity** findings are genuine code-level issues:
1. Missing `nonReentrant` on `updateUnitaryValue()` (all 9 tools)
2. Unsafe `uint208` downcasts without `SafeCast` (8/9 tools)
3. Unrestricted arbitrary storage reads (5/9 tools)

The **22 medium findings** cover fee-on-transfer incompatibility, oracle manipulation vectors, first-depositor inflation, gas limit issues, and various centralisation risks.

**Strongest mitigations already in place:**
- Transient storage reentrancy guard on mint/burn
- Minimum lockup period (1-30 days) prevents flash loan attacks
- Spread mechanism provides protocol-favourable pricing
- Minimum order size reduces dust manipulation
- `NavImpactLib.validateSupply()` bounds negative virtual supply

**Most impactful recommendations:**
1. Replace EOA owner with multisig + timelock
2. Add `nonReentrant` to `updateUnitaryValue()`
3. Use `SafeCast.toUint208()` for all narrowing casts
4. Make ERC-20 stub functions revert instead of returning false
5. Implement balance-before/after pattern for fee-on-transfer tokens

---

> This audit was performed by 9 AI-powered skill-based auditing pipelines using Claude Code. AI analysis cannot guarantee the absence of vulnerabilities. Professional human audits, bug bounty programmes, and on-chain monitoring are strongly recommended.
