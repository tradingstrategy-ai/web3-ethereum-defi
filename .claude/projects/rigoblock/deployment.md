# Rigoblock SmartPool - Deployment information

**Protocol:** Rigoblock (Smart Pool / Fund Management)
**Chain:** Ethereum Mainnet
**Pool name:** alcyoneus (ALC)
**Base token:** Native ETH (address(0))
**Version:** 4.1.1
**Compiler:** Solidity 0.8.28 (implementation), 0.8.17 (proxy)
**Optimisation:** Enabled (200 runs)
**EVM version:** Cancun (transient storage used)

## Contract addresses

| Contract | Address | Source | ABI |
|----------|---------|--------|-----|
| RigoblockPoolProxy | `0xEfa4bDf566aE50537A507863612638680420645C` | [proxy/src/Contract.sol](proxy/src/Contract.sol) | [RigoblockPoolProxy.json](abi/RigoblockPoolProxy.json) |
| SmartPool (implementation) | `0x1DB955265B8DC18715cAb12E805F9b71Fa545420` | [implementation/src/](implementation/src/protocol/) | [SmartPool.json](abi/SmartPool.json) |

## Proxy pattern

- Uses **EIP-1967 transparent proxy** pattern
- Implementation slot: `0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc`
- Implementation is set by the `IRigoblockPoolProxyFactory` at proxy construction time
- The factory controls implementation upgrades (`setImplementation()`) — governed by the Rigoblock DAO

## Privileged addresses

| Contract/Role | Address | Variable | Type | Notes |
|---------------|---------|----------|------|-------|
| Pool Owner | `0xcA9F5049c1Ea8FC78574f94B7Cf5bE5fEE354C31` | `pool().owner` | **EOA** (EIP-7702 delegated to MetaMask) | **CRITICAL: Single EOA controls all owner functions** — can set fees, KYC, spread, transfer ownership, manage tokens. No multisig or timelock. |
| Fee Collector | `0xcA9F5049c1Ea8FC78574f94B7Cf5bE5fEE354C31` | `poolParams().feeCollector` | Same as owner (EOA) | Defaults to owner when not explicitly set |
| Authority | `0xe35129A1E0BdB913CF6Fd8332E9d3533b5F41472` | `authority` (immutable) | Contract (Authority V3) | Protocol-level permission registry. Controls which adapters/methods are whitelisted. Has its own `owner`. |
| Token Jar | `0xA0F9C380ad1E1be09046319fd907335B2B452B37` | `tokenJar` (immutable) | Contract (TokenJar) | Protocol fee destination. Collects spread fees. Has a `releaser` address set by the TokenJar owner. |
| Wrapped Native | `0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2` | `wrappedNative` (immutable) | Contract (WETH9) | Standard WETH contract |
| Implementation | `0x1DB955265B8DC18715cAb12E805F9b71Fa545420` | EIP-1967 slot | Contract (SmartPool) | Locked at deployment (owner = address(0)) |

## Key parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| Min Period (lockup) | 30 days (2,592,000s) | Maximum lockup — tokens locked after mint |
| Spread | 10 bps (0.10%) | Fee taken on both mint and burn |
| Transaction Fee | 0 bps | No pool-level transaction fee currently |
| KYC Provider | address(0) | No KYC enforcement — open to all |
| Unitary Value | ~0.998 ETH per share | Current NAV per pool token |
| Total Supply | ~39.63 ALC tokens | Total pool tokens outstanding |

## Architecture notes

- **Extension system**: The fallback function routes unknown selectors to extensions via `_extensionsMap` (delegatecall for owner, staticcall for others)
- **Adapter system**: Authority contract maps function selectors to adapter contracts. Owner calls get delegatecall, others get staticcall.
- **Cross-chain**: Virtual supply mechanism for cross-chain NAV management
- **Transient storage**: Uses EIP-1153 transient storage for reentrancy guard and NAV computation
