# Description writing style

Use this guide only for user-facing text that we manually maintain for the
website and published data: vault and protocol metadata, curator profiles,
stablecoin descriptions, handwritten Python fallbacks, fee descriptions and
source-linked vault notes. It applies whether the text lives in YAML, Python
or Markdown.

It does not apply to repository engineering documentation, such as READMEs,
`docs/` and Sphinx pages, `.claude` docs and skills, docstrings, code
comments, `CHANGELOG.md` entries and pull request descriptions. Those are
written for developers and follow the English rules in `CLAUDE.md`.

## Write for a general audience

Assume the reader wants to understand the investment or business but does not
know its specialist vocabulary. Lead with what it does, how it uses money and
where returns come from. Use familiar words, concrete verbs and short paragraphs.

- Write a short description as one useful sentence about the product or strategy.
- Use the longer description to explain the fundamentals and add relevant facts.
  Do not pad it to reach a sentence or paragraph count.
- Follow the field's output format. Individual vault summaries are one plain
  sentence of single-line text. Full vault descriptions, protocol descriptions
  and source-linked notes are Markdown: split them into paragraphs of two or
  three sentences, use bold for key words or short phrases a reader must not miss, link
  sources inline and add `####` subheadings when the text covers several
  topics. See the [vault description formatting rules](../../docs/README-vault-description-formatting.md).
- Keep bold emphasis selective: highlight key words, amounts or short phrases,
  rather than whole sentences or paragraphs. Emphasise an entire sentence only
  when its full wording is essential to understanding a material warning.
  Keep source links visually selective too, as described below.
- Explain necessary financial and technical terms in a few familiar words on
  first use, especially when describing vaults and strategies. For example,
  “collateral, assets pledged to secure a loan” and “liquidation, a forced sale
  to repay lenders”. Explain named products and tokens too; do not assume a
  reader knows Pendle, principal tokens or USDat.
- Link the first useful mention of a specialist term to its frontend
  [glossary](https://tradingstrategy.ai/glossary) page using
  `https://tradingstrategy.ai/glossary/<slug>`. Keep the brief explanation in
  the description: readers should not need to follow a link to understand it.
  Reuse existing terms; add missing definitions to the documentation repository's
  `source/glossary.rst` and publish them before linking newly introduced pages.
  Check the actual frontend slug rather than guessing an abbreviation or using
  a Sphinx anchor. Put longer explanations and hypothetical worked examples in
  the glossary. Glossary links do not replace sources for product-specific claims.
- Describe business activity directly. Prefer “loans funding grain
  purchases and transport” to “short-cycle working-capital credit exposure”.
- Do not explain familiar assets such as USDC, USDT, ether or bitcoin. Reserve
  first-use definitions and glossary links for specialist terms and less familiar
  products or tokens that readers need to understand the strategy.
- Do not repeat the vault's curator, hosting protocol or blockchain name in its
  description; the page already displays them in dedicated boxes. Start with
  what the money funds. Name other products or venues only when needed to explain
  an investment mechanism, cost, risk or withdrawal condition.
- Keep token names when they identify an actual deposit asset or investment,
  but avoid strings of unexplained acronyms.
- Use British English, spell “onchain” without a hyphen, and use sentence case
  for headings.

## Describe each vault in small protocols

For protocols with **1–9 known supported vaults**, provide researched,
handwritten short and full descriptions for every covered vault. Count the
known supported vault inventory across chains, not just the example deployment.
This is an onboarding and maintained-content requirement, not a runtime rule
that removes descriptions when a protocol grows.

Protocol and curator descriptions do not populate the individual vault's
"About the vault" section. Wire the handwritten copy through the vault adapter's
`description` and `short_description` accessors and verify the scanner record
and individual vault JSON export, separately from protocol metadata export.
Use the existing address-scoped handwritten metadata workflow where available.
Research each vault's actual strategy; leave undisclosed details explicitly
unknown rather than applying generic protocol copy to every vault.

## Describe current terms first

Focus descriptions on how the product works now: what depositors fund, what
they receive, and any costs or withdrawal conditions that need explanation
beyond the displayed metrics. Explain current fee
allocations directly instead of narrating when they changed or comparing them
with superseded rates. A dated announcement can support current terms without
putting its publication month or year in the description; link the relevant
claim inline.

Use a short bullet-point breakdown for fee allocations or other parallel
terms. Put vault depositors first, followed by allocations to token buybacks,
the operator or other recipients. Preserve the fee categories and calculation
bases: do not simplify a share of selected trading fees into a share of all fees.
For example:

- Vault depositors receive **70% of fees collected on opening, closing and
  profitable trades**, linked to the announcement supporting that allocation.
- Vault depositors also receive **100% of margin fees**, which traders pay for
  using the pool's capital.
- The remaining **30% of fees on opening, closing and profitable trades** buys the
  protocol's token and permanently removes it from circulation.

Keep superseded fee schedules and detailed roadmap speculation in research
notes. Include history in public descriptions only when it helps readers
understand the current product, identify a former name or assess a material
track record or loss. Include a scheduled future change when it affects a
depositor's decision, clearly distinguishing it from current terms.

## Do not repeat charts and metrics

Do not narrate routine returns, performance paths, deposited amounts, capacity
limits or trading-cycle dates already shown in the page's charts and metrics.
For example, omit “The first epoch ran with $100,000 and generated a
manager-reported 6% net return” and “A further cycle was announced for
5 to 20 November, capped at 100,000 USDG”. Explain the investment, costs and
risks instead. Retain material loss events or restrictions that need context,
and risk policies such as a manager's loss limit; these are not chart summaries.

Keep all relevant announcement links, each with a short sentence explaining
what it introduced or changed. An update about performance can instead explain
the costs or proposed strategy changes it discusses. Label proposals as plans,
without presenting them as current investments. Do not preserve redundant
figures merely to justify retaining their source link.

Long descriptions do not need to repeat fees or lock-up periods already
available in the chart and key metrics context. Leave routine fee rates and
withdrawal timing in those dedicated displays, and use the description for the
investment strategy and facts that need explanation. Include fee or withdrawal
details only when they add material context that the displayed metrics do not
convey, such as a complex charging basis or an exceptional withdrawal restriction.

## Cover the facts that matter

Select the following details according to the subject and available evidence.
A short note does not need to become a full product profile.

- **Strategy and fundamentals:** what depositors fund, who manages or borrows
  the money, the assets or markets involved, and the source of returns. Explain
  the real business purpose, such as financing purchases before customers pay
  invoices. For stablecoins, explain the price reference, reserves and how
  holders can exchange them for the underlying money or assets.
- **People and organisations:** mention known founders, backers and partners
  when relevant. State their actual roles. Distinguish investors in the company
  from lenders to a vault, service providers and commercial partners. A logo
  or integration does not establish financial backing or a repayment guarantee.
- **Milestones and track record:** include significant launches, funding rounds,
  acquisitions, operating history and documented performance when relevant to
  understanding the current product or assessing its track record.
  Include material losses, defaults, closures or strategy changes where they
  help assess the current product. Give dates and the period covered by performance
  figures; distinguish live results from backtests and company history from
  the history of a particular vault.
- **Fees, funding and business model:** where applicable, explain how the
  operator earns money, who provides capital, who receives payments and who
  bears losses. For vaults, include fee details only when they add material
  context beyond the displayed rates. Cover borrowing costs, withdrawal charges,
  use of borrowed money and any arrangement under which one party absorbs
  losses first. When including a fee, explain what it is charged on and how
  often when those terms are known.
- **Access and limitations:** include withdrawal restrictions beyond displayed
  lock-up periods, eligibility and concrete risks when they materially affect
  how the product works. Tie them to the strategy instead of appending a
  generic list of risk categories.

## Remove AI slop and technical clutter

Use specific facts instead of promotional adjectives or abstract business
language. Remove phrases such as “innovative ecosystem”, “seamless access”,
“robust yield infrastructure”, “unlock opportunities” and “leverages cutting-edge
technology”. Replace “the business use case is to provide the yield component”
with a direct explanation of what generates the income.

Do not repeat the same point in different words, add rhetorical questions or
finish with a summary that merely repeats the opening. Avoid unsupported labels
such as “leading”, “safe”, “trusted” or “institutional-grade”.

Do not add research-process preambles such as “As checked”, “As verified”,
“At the time of review” or “Our research found”. Describe the product or fact
directly. When a changing fact needs a snapshot date, use wording such as
“In November 2026, the vault held…” rather than describing when we checked it.
Keep verification dates in research notes or source comments, outside the public
description.

Keep programming and repository details out of public descriptions: no scanner
behaviour, adapters, API methods, ABI names, classifiers, bytecode or metadata
repair history. Mention a network or technical mechanism only when it helps
the reader understand access, strategy, costs or a material limitation.

## Keep claims traceable and current

All vault links must point to the vault's `https://tradingstrategy.ai/vaults/<slug>`
page unless the user explicitly requests another destination. This includes
linked vault names, product references and vault detail links in descriptions,
notes and related-link lists. Apply this rule when adding or editing maintained
public copy, including its associated vault destination fields such as
`HandwrittenVaultMetadata.link`. Verify the published page's chain and contract
address; names and URL slugs can differ after a rename. Do not guess a slug
or link to a different vault with a similar name. If no page is available,
leave the vault name unlinked and record the missing page in research notes.

Keep links to supporting documentation, terms, reports and announcements when
they substantiate a claim. Use link text that identifies the supported claim or
source, rather than the vault's name alone. These source citations do not replace
the Trading Strategy destination for links to the vault itself.

Use authoritative sources for factual claims, preferably official product
documentation, terms, fee schedules, reports and dated announcements. An
attributable official social post can support an announcement; it does not
establish a performance history or current holdings by itself.

Link material claims to their sources in Markdown-capable descriptions or
notes. For plain-text fields, preserve the sources in the accompanying note
or research file.

Never introduce a source with “According to X” or an equivalent attribution
preamble such as “X says”, “X reports” or “as described by X”. State the fact
directly and link a short, meaningful phrase within the supported claim.
Avoid generic labels such as “source” or “click here”, and avoid turning long
sentences into blocks of linked text. Link a whole sentence only when it is
short and the full wording is needed to make the source connection clear.
For example:

> The vault's [investment mandate](https://api.nest.credit/v1/vaults/nest-axi-vault) describes planned financing for payment businesses.

Direct linking does not make an unverified claim a verified fact. Retain
necessary qualifications in the sentence, such as “targets”, “aims to”
or “reported”, and state material evidence gaps explicitly. Link each source
page once, choosing the claim that best represents what it supports.

Check that the evidence describes the actual vault or product. Do not turn a
provider's general investment approach into a claim about every vault it runs.
State a material evidence gap plainly and omit unsupported details rather than
filling them with plausible generic copy.

Verify changing facts such as fees and funding totals before adding or updating
them. Include dates when they describe an actual event, effective date or
performance period, not when the author checked a source. Do not present a past rate as a
current offer or a historical result as a promise of future returns.

Use month–year dates in public descriptions by default, for example “April
2025” or “November 2026”. Include the day only when it is critical to the
context, such as a redemption deadline, incident timeline, effective date or
comparison within the same month. Announcement examples and routine holdings
snapshots normally need only the month and year. Keep exact research dates
and source publication dates in accompanying research notes where useful.

## Final read

Read the copy as someone unfamiliar with the product. Can they understand what
their money funds, how income is earned and who is responsible? Keep the
verified people, business details and history that answer those questions.
Remove any sentence that adds only jargon, promotion or repetition.
