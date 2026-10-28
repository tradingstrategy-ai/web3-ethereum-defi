# Vault notes

Vault notes are short Markdown messages shown in vault exports and diagnostics.
They explain special vault behaviour, risk context, missing data, or authoritative
source links that are not otherwise visible from raw onchain metrics.

## Current sources

### Manual address and protocol notes

`eth_defi/vault/flag.py` is the central manual note table.

- `VAULT_NOTES` contains address-specific descriptive notes that do not make a
  vault flagged.
- `VAULT_FLAGS_AND_NOTES` contains address-specific notes paired with an optional
  `VaultFlag`. If the flag is in `BAD_FLAGS`, downstream risk logic can blacklist
  the vault.
- `PROTOCOL_FLAGS_AND_NOTES` contains protocol-wide manual notes and flags.
- `get_notes(address, chain_id=None, protocol_name=None)` applies those tables
  and chain-level fallbacks. Pass `include_defaults=False` to check current
  address notes and manual protocol warnings without generic defaults.

Manual bad-flag notes should take precedence over generic descriptive notes. A
vault that has been explicitly marked as `subvault`, `illiquid`, `unofficial`,
or similar should not have that warning hidden by a protocol-level description.

### Protocol vault class notes

Protocol-specific adapters should override `VaultBase.get_notes()` when notes
can be derived from protocol metadata, protocol APIs, or protocol-specific
constants.

Examples:

- IPOR, Lagoon, Ember and Royco call `super().get_notes()` first, then fall back
  to the protocol offchain description or missing-frontend note.
- Morpho V1/V2 call `super().get_notes()` first, then generate notes from Morpho
  API warnings.
- D2 Finance keeps D2 strategy-page and D2-authored article links in
  `eth_defi/erc_4626/vault_protocol/d2/vault.py`, then exposes them through
  `D2Vault.get_notes()`.
- ODA-FACT stores scan-only notes through `fetch_scan_record_extra_data()`.

Use this approach for notes that are a property of the protocol integration
itself. It keeps protocol knowledge in the protocol module instead of scattering
it in export/reporting code.

### Reviewed strategy descriptions

Maintain strategy descriptions separately from risk notes. The chain/address
mapping in [handwritten metadata](../../eth_defi/vault/handwritten_metadata.py)
provides researched `description` and `short_description` values when protocol
metadata is missing or needs a clearer explanation. Morpho V1/V2 and IPOR
adapters use these overrides without replacing onchain names or existing notes.
The Morini-specific note formatter is not a generic formatter for other managers.

Descriptions are materialised in `_description` and `_short_description` during
scanning. Deploy adapter changes before a metadata-only repair, then run the
normal analysis/export to publish the copy. Otherwise an older scanner can
overwrite the repair on its next scan. The [AlphaPing research and repair
guide](../../docs/protocol-research/alphaping.md) documents a fixed-scope example
that preserves names, flags, notes, prices and reader state.

### Scan-row storage

ERC-4626 scan rows are built in `eth_defi/erc_4626/scan.py`. These rows store
machine-readable metadata in private keys such as `_flags`, `_risk`,
`_description` and `_short_description`.

Protocol-level notes should be scanned into `_notes` with `vault.get_notes()`.
Lifetime metrics can then use the scanned note without reconstructing protocol
objects during reporting.

`_short_description` is plain text: Markdown is not allowed, because the
website renders it verbatim in listings and tables. Put documentation links in
`_notes` or `_description` instead.

Do not store `_notes` when the adapter note only duplicates `_description` or
`_short_description`. Some adapters use `get_notes()` as a generic text hook and
fall back to the same protocol description that is already exported separately.

### Lifetime metrics fallback

`eth_defi/research/vault_metrics.py` calculates the exported `notes` field for
top-vault JSON rows.

Current explicit manual notes and warnings take precedence over
`VaultRow["_notes"]`, so a warning added after scanning appears on the next
metadata export. Metrics first call `get_notes(..., include_defaults=False)`,
then use the cached adapter note, then apply generic protocol/chain defaults.
This preserves richer strategy text when only a chain default is available.
The lookup remains generic: do not add protocol-specific branches when an
adapter can provide the strategy note.

## Recommended pattern

1. Put protocol-specific note text and link formatting in the protocol module,
   for example `eth_defi/erc_4626/vault_protocol/<protocol>/vault.py`.
2. Override `get_notes()` in the protocol vault class.
3. Call `super().get_notes()` first so manual address flags and notes keep
   precedence.
4. Store the result in scan metadata as `_notes`.
5. Let lifetime metrics consume `_notes` after checking current manual
   overrides, with generic defaults as the final fallback.

Avoid adding protocol-specific note branches directly to reporting code like
`vault_metrics.py`.
