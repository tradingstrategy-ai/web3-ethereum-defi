# Description style audit

Audit date: 2026-10-28. Standard: the repository's
[description writing style guide](../../.claude/docs/description-writing-style.md).

Snapshot: repository base `1a26c81c5170e2ead1ca5c1904949e838621e590`, with
this branch's Accountable descriptions and style-guide changes applied. Counts,
findings and CSV line numbers refer to that snapshot. Subsequent upstream changes
incorporated when preparing the pull request have not been re-audited here.

The catalogue does not consistently meet the guide. The most important problems
are incorrect or stale product claims, implementation details in public copy,
promotional claims, unexplained financial terms and weak source attribution.

The [full field inventory](description-style-audit-2026-10-28.csv) contains the
text, source file and line, candidate rule matches and confirmed findings.
Filter `review_status` to distinguish confirmed findings from automated signals.

## Coverage and limits

| Source | Files | Fields | Nonempty fields |
|---|---:|---:|---:|
| Vault/protocol metadata YAML | 122 | 366 | 339 |
| Curator feed YAML, including incident descriptions | 266 | 534 | 534 |
| Protocol feed YAML | 15 | 30 | 30 |
| Stablecoin metadata YAML, including nested entries | 212 | 448 | 448 |
| Python descriptions, notes and strategy explanations | 39 | 250 | 250 |
| Total | 654 | 1,628 | 1,601 |

The inventory was built from all YAML files under `eth_defi/data` and a static
scan of Python description properties, description constructor arguments,
description dictionaries and note constants. Python references to the same
local constant are counted once at their definition. Static concatenations and
joins are included; runtime placeholders remain placeholders. API-supplied
descriptions, generated exports, technical docstrings and integration manuals
are outside this manually maintained public-copy inventory.

Every inventoried field was checked for wording, formatting and source-link
signals. This is a repository-wide automated scan with targeted editorial
review, not a sentence-by-sentence approval of every field or a complete
verification of every financial claim. **45 fields in 28 files have confirmed
findings.** The scan found candidate signals in 1,154 fields across 621 files.
Fields with no rule match have not been certified compliant.

Founder, investor, partner and milestone omissions cannot be established from
word matching: their inclusion depends on relevant, attributable evidence.
Likewise, absence of a percentage does not establish missing fees, and an empty
fee description does not mean zero fees. These require product research.

No public descriptions were changed during this audit. The working tree already
contained the earlier Accountable copy and style-guide changes.

## Highest-priority factual and historical findings

1. **USDC reserve fund and history.**
   [The description](../../eth_defi/data/stablecoins/usdc.yaml) identifies
   BlackRock BUIDL as a reserve fund. Circle's
   [reserve disclosure](https://www.circle.com/transparency), checked on the
   audit date, names the Circle Reserve Fund (USDXX), managed by BlackRock.
   The text also says reserves “always” cover supply and describes risk as
   eliminated. Its history omits the March 2023 depeg and banking disruption;
   [Circle's contemporary announcement](https://www.circle.com/pressroom/3-3-billion-of-usdc-reserve-risk-removed-dollar-de-peg-closes)
   records the $3.3 billion Silicon Valley Bank reserve deposit and its recovery.
   Rewrite the backing, access and history sections with direct sources; review
   the legal claims separately before retaining them.
2. **CAP strategy identity.**
   [Both descriptions](../../eth_defi/data/vaults/metadata/cap.yaml) call CAP a
   covered-call protocol. Its [official site](https://www.cap.app/) describes
   a platform connecting depositors, private-credit borrowers and underwriters.
   The description should explain those roles and the guarantee arrangement,
   without suggesting an unconditional promise of investor safety.
3. **BOLD launch and governance.**
   [The description](../../eth_defi/data/stablecoins/bold.yaml) says Liquity V2
   launched in 2024 and has no governance. The official
   [deployment history](https://www.liquity.org/blog/liquity-v2-redeployment)
   documents the 2025 redeployment, while the
   [2024 announcement](https://www.liquity.org/blog/liquity-v2-improving-defi-with-bold-innovation)
   concerned a codebase release for the forthcoming stablecoin.
   The [current FAQ](https://docs.liquity.org/v2-faq/general) describes limited
   governance over incentive allocation. Distinguish code release, launch and
   redeployment, and recheck redemption and collateral claims before rewriting.
4. **ynRWAx maturity and safety wording (superseded).**
   `YIELDNEST_YNRWAX` in [vault notes](../../eth_defi/vault/flag.py) describes
   the investment as “Made safe”, earning 11% APY, with 0% fees. Its stated
   maturity, 15 October 2026, is already past. Verify the present status and
   date any historical yield/fee terms. The date alone establishes neither
   repayment nor closure.
   This is a historical snapshot finding: [PR #1649](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1649)
   has already replaced this text on `master` with a liquidation warning.
   The CSV retains the original text and counts, with this supersession recorded
   in its finding; it is no longer an outstanding rewrite of the current note.
5. **IPOR fee contradiction.**
   [The fee description](../../eth_defi/data/vaults/metadata/ipor-fusion.yaml)
   first says investors pay no deposit fee, then describes an optional deposit
   fee. It also spends 511 words on contract internals. Resolve the contradiction
   against current terms and explain the fee basis, timing and vault-specific
   settings in plain language.

## Confirmed editorial patterns

| Problem | Examples | Required change |
|---|---|---|
| Programming details replace the investment explanation | Enzyme, Gains Network, CrystalClear, Mellow, Upshift, Barker | Remove scanner discovery, proxy types, method names and metadata mapping; explain investments, income and access |
| Fee descriptions document the implementation | IPOR Fusion, 3Jane, Enzyme, Mellow, Kinexys, Secured Finance | Explain what is charged, on what amount, when and by whom; keep useful fee dates and waiver periods |
| Curator profiles expose maintenance decisions | Monarq, Nova Markets, Sylva.money | Move alias mapping and API registration commentary into maintainer notes |
| Promotional language replaces fundamentals | Goat Protocol, YieldNest, Trust Wallet, Atoma | Remove leadership, safety and maximum-return claims; describe the actual strategy and operator roles |
| Specialist vocabulary is unexplained | Warwolf, Shift, Axis, older Morini summaries | Explain trades and withdrawal conditions in ordinary language; retain technical terms only where needed |
| Historical facts or metrics lack usable dates | USDXL price/volume, Trust Wallet user counts, ynRWAx return claims | Verify and date material figures, or remove unnecessary changing statistics |
| Long quotation substitutes for a neutral account | ESPN note in `flag.py` | Summarise the attributed withdrawal complaint with its date and source |
| Product identity is displaced by a data-access problem | Fidelity FDIT feed, WisdomTree metadata and notes | Lead with the fund strategy and eligibility; explain only material data limitations in public copy |

These examples are confirmed editorial problems, not assertions that every
financial claim in the named files has been verified. The CSV records the
affected fields and specific recommendations.

## Catalogue-wide review signals

| Signal | Fields | Interpretation |
|---|---:|---|
| Specialist terms | 734 | Check whether the term is necessary and explained; its presence alone is not a violation |
| No inline source in a long description | 337 | Check accompanying sources and add claim-level links where supported; does not prove the claim is unsourced |
| Multiple-sentence short description | 240 | Usually shorten to one sentence; abbreviations such as “U.S.” can produce false positives |
| American spelling or “on-chain” | 191 | Correct ordinary prose, preserving official names and quoted text |
| Implementation vocabulary | 166 | Inspect context; some terms describe the product's business rather than repository internals |
| Promotional or vague wording | 146 | Replace with concrete facts; official names and factual uses can be exceptions |
| Short description over 35 words | 127 | Editorial review aid, not a hard word limit in the guide |
| Percentage without a year in the same field | 82 | Check rate, fee and observation date; collateral ratios are not necessarily stale yield claims |
| Relative time such as “currently” or “now” | 57 | Check whether the surrounding text supplies a date |
| Absolute-claim vocabulary | 32 | Inspect negation and attribution before changing; “not guaranteed” is not a guarantee |
| Long, list-heavy description | 32 | Check whether lists repeat the prose or expose unnecessary implementation details |
| Empty fee description | 27 | Research coverage gap; do not infer that fees are absent |

Counts overlap. Automated signals must not become a blanket text-replacement
operation: doing so could alter official fund names, weaken useful risk
explanations or remove legitimate business terms.

## Suggested remediation order

1. Correct product identity, backing, historical claims and misleading safety
   language using primary sources.
2. Rewrite engineering-heavy strategy and fee descriptions, preserving material
   financial terms and withdrawal restrictions.
3. Shorten summaries, remove promotional language and explain specialist terms.
4. Check evidence for founders, backers, commercial partners, significant
   milestones, operating history and the business model. Include relevant known
   facts without filling gaps with generic prose.
5. Finish the remaining field-level editorial review, resolve source-link and
   date issues, and apply British spelling outside official names and quotations.

The existing Accountable descriptions are substantially closer to the guide
after their earlier edit. That does not establish compliance for the wider
catalogue or supply missing vault-specific evidence for the unresolved products.
