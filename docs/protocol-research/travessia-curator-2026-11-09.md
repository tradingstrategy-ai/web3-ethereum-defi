# Travessia Credit curator attribution

Reviewed on 2026-11-09. The [official website](https://www.travessiacredit.com/)
identifies Travessia Credit as the organisation connecting lenders with trade
businesses. Its [platform documentation](https://docs.travessiacredit.com/pages/full-platform-flow.html)
assigns borrower onboarding, financial/legal review and monitoring to Travessia
and identifies Accountable as the credit-vault infrastructure partner.
Tauri Agrícola is the borrower and grain-trading operator, not a separate curator.
This supports product-level attribution, not a claim about admin-key custody.

The published Trading Strategy page `travessia-tauri-vault-3` was opened during
this task and identifies Avalanche (43114), share token TTV and address
`0x9c3da70cd7fedeaef528d060ea99ace133e08fd2`, matching the official product.

## Reviewed inventory

The local metadata snapshot (last modified 2026-10-28) has seven
Travessia-branded vaults. Each official page below returned HTTP 200 and
contained the matching vault address and the Tauri grain-financing strategy.
The web reader could not open Ethereum USDT and USDC, so these were checked
with Requests against the official site. Base and Polygon were checked the same
way. No authenticated API or RPC integration is introduced by this change.

| Chain | Share address | Official documentation |
| --- | --- | --- |
| Ethereum | `0x4c18e2bb9942b12b28e780acf2d9ec2dda126df9` | [AUSD product](https://www.travessiacredit.com/vaults/1/tauri/0x4C18E2bb9942b12b28e780acF2D9EC2DDA126df9) |
| Ethereum | `0xe4ee0fe4b09021fcdf76b165aa1e8eaf7d447c35` | [USDT product](https://www.travessiacredit.com/vaults/1/tauri/0xe4EE0fe4B09021FCdF76B165AA1e8eAF7d447c35) |
| Ethereum | `0xf84a37545e313e8177d80039158ed478c7dc9079` | [USDC product](https://www.travessiacredit.com/vaults/1/tauri/0xf84A37545e313E8177d80039158ed478C7Dc9079) |
| Monad | `0x8d24b48cb736addb5b7069565f192a052f42ba61` | [AUSD product](https://www.travessiacredit.com/vaults/143/tauri/0x8D24B48Cb736addb5B7069565F192A052F42Ba61) |
| Avalanche | `0x9c3da70cd7fedeaef528d060ea99ace133e08fd2` | [USDT product](https://www.travessiacredit.com/vaults/43114/tauri/0x9c3da70cD7feDEAeF528d060ea99AcE133E08Fd2) |
| Base | `0xb37cc5e891d87c5a3ef750e1bdf59c960575593c` | [USDC product](https://www.travessiacredit.com/vaults/8453/tauri/0xb37cc5e891d87c5a3ef750e1bdf59c960575593c) |
| Polygon | `0x08dd5f2a0b107b8f93c4f524901994353bedfaf7` | [BRZ product](https://www.travessiacredit.com/vaults/137/tauri/0x08dd5f2a0b107b8f93c4f524901994353bedfaf7) |

All seven receive `rwa`, `rwa_credit` and `rwa_lending`: deposits lend money
to a real business to fund purchases and transport while invoices settle.
Do not tag commodity-price trading merely because the borrower trades grain.
Curator matching uses the specific `Travessia Tauri` product name rather than
`Tauri` alone. No official social account was linked from the current homepage;
optional social feed fields are omitted rather than inferred.

## Persistence and price history

Curator attribution is calculated in `vault_metrics.py` during export, so it
needs no persisted curator migration. Strategy tags are cached; numbered
migration `043-travessia-strategy-tags.py` refreshes only `_strategy_tags` for
the six Accountable-classified deployments, using the same chain-aware resolver
as the adapter. It reports missing records and the deferred Polygon BRZ row,
rejects mismatched protocol detection for the six targets and preserves all
other fields. See the failed production attempt and correction below.

The local hourly price file was read with chain/address filters to compare
first-seen timestamps with the first price row. This is an older local snapshot,
not a statement about current production coverage. All times below are UTC.

| Deployment | First seen | First local price |
| --- | --- | --- |
| Ethereum AUSD | 2026-03-27 08:49:11 | 2026-04-09 19:18:23 |
| Ethereum USDT | 2026-07-02 11:35:47 | 2026-09-21 21:08:47 |
| Ethereum USDC | 2026-04-21 14:56:47 | 2026-04-23 13:19:35 |
| Monad AUSD | 2026-03-27 08:54:53 | 2026-07-16 16:45:47 |
| Avalanche USDT | 2026-07-15 21:56:27 | 2026-07-16 17:32:08 |
| Base USDC | 2026-04-23 11:06:47 | No rows |
| Polygon BRZ | 2026-10-09 08:58:43 | 2026-10-14 19:23:00 |

Proposed follow-up: inspect a current production snapshot and, if gaps remain,
prepare a separate numbered price-backfill migration scoped to these deployments.
Prioritise the Ethereum AUSD/USDT gaps and investigate why Base has no prices
before scanning it. Fix exact block bounds from discovery to the first existing
price, validate historical reader support and preserve all existing rows.
Monad cannot recover arbitrarily old state; do not attempt a March-to-July
state backfill. Check only the provider's currently executable state window,
preserve older prices and use Hypersync for events/timestamps. Follow the
[migration and historical backfill rules](../../.claude/docs/migration-scripts.md).
Migration 043 deliberately does not add a network-dependent historical scan to
this curator metadata change.

## Cleanup checklist

- Public curator copy checked against `.claude/docs/description-writing-style.md`.
- Official logo extraction and theme output provenance recorded beside originals.
- Focused curator, adapter and migration checks cover attribution, case handling,
  missing information, cross-chain isolation, preview, idempotence and guard errors.
- Production migration execution and any historical backfill remain separate work.

The independent Claude Opus 5.5 review prompted a generic-logo correction,
clearer plural product copy and explicit documentation of the frontend slug.
Protocol-local tag mappings are retained as required by the invoked
`add-curator` and `categorise-vault-strategy` skills; the new shared resolver
keeps the adapter and persisted refresh in agreement without duplicating
business rules. PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1741.

A further requested writing-guide review simplified the curator descriptions:
“buy and transport goods” explains the business purpose, borrower reviews are
spelled out, “Avalanche vault” replaces “deployment”, and the copy identifies
interest and grain-trading income as the lender's return and repayment source.
The linked official product page supports the repayment obligation and income
source; the wording does not promise repayment or infer admin-key custody.

The focused copy review also clarified Accountable's software role, explicitly
identified interest as depositors' income, and qualified grain-trading income
as the intended repayment source. The official product page's loan terms and
trade-cycle sections support the lending-terms link and buyer-payment timing;
its operator overview confirms Brazil. The reviewed seven-address inventory
supports the plural “Tauri vaults”.

## Migration failure and correction

The production apply on 9 November 2026 stopped at migration 043 because
Polygon BRZ (`137:0x08dd5f2a0b107b8f93c4f524901994353bedfaf7`) has only
`erc_7540_like` and `erc_7575_like` in its persisted detection, with protocol
`<unknown ERC-7540>`. The local October snapshot reproduces that distinction.
The original tests incorrectly assigned `accountable_like` to all seven rows.
The published product description establishes a business strategy, but does
not establish compatibility with the Accountable adapter.

The corrected failed script refreshes the six Accountable-classified vaults
and reports Polygon as `deferred_specs`, leaving its complete row untouched.
This agrees with the scope of migration 047. The other six retain their strict
protocol guards; no feature or adapter classification is forced. Polygon's
strategy mapping remains available if a future protocol review establishes
compatibility, but this correction does not publish tags for the generic row.

The reported failure happened before staging or committing any metadata.
The migration guide permits correcting failed scripts in place; a later script
alone cannot unblock a batch stopped at 043. Keep the existing journal entry
and backups. After merging the fix, update the production checkout and rebuild
with `scripts/build-container.sh`. With the scanner and competing writers
stopped, run the usual full-batch preview and apply from the migration runbook.
Do not rerun the old image or delete/reset migration state. The next invocation
retries failed 043 and skips migrations already recorded as successful.
