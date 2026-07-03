# Kinexys vault integration plan

## Summary

This note documents the minimal ODA-FACT integration needed for the existing
vault matching and historical price pipeline.

ODA-FACT comes from J.P. Morgan's token-standard terminology. Current Kinexys
material describes it as Kinexys Digital Assets Fungible Asset Contract, while
the ODA prefix reflects the earlier Onyx Digital Assets branding before Onyx
was renamed to Kinexys.

The first supported contract is JPMorgan's OnChain Liquidity-Token Money Market
Fund token, JLTXX:

- Chain: Ethereum mainnet
- Token and diamond address: `0x09864f52B035AE22eE739dFa5c748fA080D07bD8`
- Token decimals: `2`
- First seen block: `25042223`
- Verified source package reference: `@odaplatform/da-fact-smartcontracts`

## Scope

The integration is intentionally scan-only.

It should:

- hardcode the single production JLTXX lead in `classification.py`;
- route the hardcoded address to `ERC4626Feature.oda_fact_like`;
- create an `OdaFactVault` through the normal vault classifier;
- read ERC-20 `totalSupply()` for current and historical supply;
- report JLTXX share price as an explicitly labelled `1.00` USD estimate until
  an official NAV source is integrated;
- hardcode JLTXX Token Class prospectus fees by address: `0.16%` current net
  annual expenses after waivers, with `0.71%` gross expenses kept as scan-row
  diagnostics;
- export rows through the shared vault scan-record path;
- keep active deposit, redemption and flow accounting unsupported.

It should not:

- pretend ODA-FACT is ERC-4626;
- map ERC-20 mint/burn/transfer events to generic vault deposits or redemptions;
- add a generic discovery scanner before there is more than one production
  contract;
- add a placeholder NAV provider abstraction before there is a real NAV source.

## Why this is not ERC-4626

ODA-FACT exposes an ERC-20-compatible token surface, but not the standard
ERC-4626 accounting and investor flow surface.

ERC-4626 assumes methods such as `asset()`, `totalAssets()`,
`convertToAssets()`, `deposit()`, `withdraw()` and canonical `Deposit` /
`Withdraw` events. JLTXX does not expose this surface. It is a permissioned
tokenised fund share contract where subscription, redemption and NAV accounting
are handled outside the generic DeFi vault interface.

## Pipeline fit

The implementation follows the same shape as the recent Mellow adapter work:

- use a `VaultBase` adapter instead of forcing ERC-4626 methods;
- reuse `create_vault_scan_record()` for row export;
- provide a protocol-specific historical reader;
- use a feature flag only as a routing marker.

The only special discovery rule is the hardcoded JLTXX lead. Because there is
one production contract, a separate ODA-FACT discovery module is unnecessary.

## Open follow-up

Historical TVL is currently based on `totalSupply() * 1.00`. This is useful for
pipeline compatibility, but it is still an estimate. The next meaningful
improvement is an official JPMorgan, Kinexys or transfer-agent NAV source that
can provide block- or date-aligned NAV per share.
