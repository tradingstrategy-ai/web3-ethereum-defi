# Guggenheim DCP implementation hand-off

This note is the implementation brief for adding Guggenheim Treasury Services
Digital Commercial Paper (DCP / GDCP) to the data pipeline. It intentionally
does not describe DCP as an ERC-20 or ERC-4626 vault: the Ethereum deployment
is a permissioned ERC-1155 contract in which every token ID is an independent
commercial-paper issuance.

## Verified starting point

- Ethereum proxy: `0xb5710a6fede27d1048c75b157bd3403ba08cdbe0`
- Current verified implementation: `0xe0dd433372ac31c0055b7a40663033cfb3542671`
- Implementation family: Zeconomy/AmpFi.Digital `CPTOKEN_V3`
- Issuer/programme administrator: Guggenheim Treasury Services
- Tokenisation platform: Zeconomy

The first two addresses, the proxy architecture and the reviewed ABI are
documented in `tokenised-fund-contracts/dcp.md`. Re-resolve the proxy
implementation immediately before adding production support: the implementation
is upgradeable and has changed since the original deployment.

## Non-negotiable data model

Model each DCP note by the composite identity:

```text
(chain_id, contract_address, token_id)
```

Do not use the proxy address alone as a vault ID. `totalSupply(token_id)`,
`getLockedTokens(token_id)`, `isPaid(token_id)` and any maturity/redemption
data are all specific to one ERC-1155 token ID. Combining them by proxy would
mix separate issuances and maturities.

Extend `VaultSpec` or introduce a parallel immutable instrument identifier
with an optional integer `token_id`. Its string form must be unambiguous and
backwards compatible, for example:

```text
1-0xb5710a6fede27d1048c75b157bd3403ba08cdbe0-42
```

Existing address-only records must migrate with a null token ID; they must not
be reinterpreted as token ID zero.

## Required implementation work

1. Add `eth_defi/tokenised_fund/zeconomy/` with constants, an ERC-1155
   instrument adapter, a historical reader and a targeted backfill.
2. Do not subclass `ERC4626Vault`, and do not expose a generic deposit manager.
   DCP issuance, transfer and payment are programme-whitelisted operational
   flows, not public deposit/redemption functions.
3. Discover token IDs with Hypersync, never JSON-RPC `eth_getLogs`. Stream the
   ERC-1155 issue/burn transfers and the implementation's issue events. Retain
   IDs with non-zero current `totalSupply(token_id)`.
4. For every active ID, collect first issuance block, total supply, locked
   supply, paid status, denomination, maturity, and the authoritative
   redemption amount or price convention.
5. Do not create `share_price`, TVL or return history until an issuer or
   programme source proves the per-note valuation. `isPaid(token_id)` is a
   lifecycle signal, not sufficient evidence of price, payment or legal
   redeemability.
6. Record Guggenheim as issuer/programme administrator and Zeconomy as the
   platform in separate curator metadata. Do not collapse these roles.

## Pipeline migration and safety checklist

The current metadata database, reader state and Parquet price data are keyed
by `chain_id` and address. Update every use of `VaultSpec.as_string_id()`,
reader-state key, price-data `id`, JSON export and targeted history replacement
to preserve the token-ID dimension.

Before merging:

- Download or copy the production raw and cleaned Parquet files.
- Exercise the migration against those copies and compare row counts and
  non-DCP IDs before and after it.
- Verify new columns have null defaults for all existing rows.
- Make schema/cast errors abort the migration. Never reset a Parquet table to
  empty or silently discard rows.
- Verify the targeted backfill replaces only the selected composite IDs and
  leaves unrelated metadata, reader state and price rows intact.
- Add focused tests for composite-ID serialisation/parsing, ERC-1155 discovery,
  per-token supply/lock/paid reads, migration safety and backfill isolation.

## Deployment criteria

Enable a non-dry-run DCP backfill only when at least one active token ID has a
verified lifecycle and valuation source. Until then, it is acceptable to
publish instrument metadata and supply with explicitly unavailable price/TVL;
it is not acceptable to assume a one-dollar price or to represent the proxy as
one fungible fund.
