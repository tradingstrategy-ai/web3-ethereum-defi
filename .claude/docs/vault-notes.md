# Vault notes

Vault notes are short Markdown messages shown in vault exports and diagnostics.
They explain special vault behaviour, risk context, missing data, or authoritative
source links that are not otherwise visible from raw on-chain metrics.

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
  and chain-level fallbacks.

Manual bad-flag notes should take precedence over generic descriptive notes. A
vault that has been explicitly marked as `subvault`, `illiquid`, `unofficial`,
or similar should not have that warning hidden by a protocol-level description.

### Protocol vault class notes

Protocol-specific adapters should override `VaultBase.get_notes()` when notes
can be derived from protocol metadata, protocol APIs, or protocol-specific
constants.

Examples:

- IPOR, Lagoon, Ember and Royco call `super().get_notes()` first, then fall back
  to the protocol off-chain description or missing-frontend note.
- Morpho V1/V2 call `super().get_notes()` first, then generate notes from Morpho
  API warnings.
- D2 Finance keeps D2 strategy-page and D2-authored article links in
  `eth_defi/erc_4626/vault_protocol/d2/vault.py`, then exposes them through
  `D2Vault.get_notes()`.
- ODA-FACT stores scan-only notes through `fetch_scan_record_extra_data()`.

Use this approach for notes that are a property of the protocol integration
itself. It keeps protocol knowledge in the protocol module instead of scattering
it in export/reporting code.

### Scan-row storage

ERC-4626 scan rows are built in `eth_defi/erc_4626/scan.py`. These rows store
machine-readable metadata in private keys such as `_flags`, `_risk`,
`_description` and `_short_description`.

Protocol-level notes should be scanned into `_notes` with `vault.get_notes()`.
Lifetime metrics can then use the scanned note without reconstructing protocol
objects during reporting.

Do not store `_notes` when the adapter note only duplicates `_description` or
`_short_description`. Some adapters use `get_notes()` as a generic text hook and
fall back to the same protocol description that is already exported separately.

### Lifetime metrics fallback

`eth_defi/research/vault_metrics.py` calculates the exported `notes` field for
top-vault JSON rows.

The preferred source is `VaultRow["_notes"]`, because it comes from the
protocol adapter at scan time. For old cached vault databases that pre-date
`_notes`, metrics may still call `eth_defi.vault.flag.get_notes()` as a fallback
using only address and chain ID. This fallback should remain generic and should
not contain protocol-specific special cases when a protocol vault class can
provide the note.

## Recommended pattern

1. Put protocol-specific note text and link formatting in the protocol module,
   for example `eth_defi/erc_4626/vault_protocol/<protocol>/vault.py`.
2. Override `get_notes()` in the protocol vault class.
3. Call `super().get_notes()` first so manual address flags and notes keep
   precedence.
4. Store the result in scan metadata as `_notes`.
5. Let lifetime metrics consume `_notes`, with only the central manual note
   fallback for legacy rows.

Avoid adding protocol-specific note branches directly to reporting code like
`vault_metrics.py`.
