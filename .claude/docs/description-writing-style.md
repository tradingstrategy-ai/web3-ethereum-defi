# Description writing style

Use this guide for the public descriptions we manually maintain in repository
files: vault and protocol metadata, curator profiles, stablecoin descriptions,
handwritten Python fallbacks, fee descriptions and source-linked vault notes.
It applies whether the text lives in YAML, Python or Markdown. Technical API
documentation and code comments have a different audience.

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
  three sentences, use bold for the few facts a reader must not miss, link
  sources inline and add `####` subheadings when the text covers several
  topics. See the [vault description formatting rules](../../docs/README-vault-description-formatting.md).
- Explain a necessary financial term on first use. Prefer “loans funding grain
  purchases and transport” to “short-cycle working-capital credit exposure”.
- Keep token names when they identify an actual deposit asset or investment,
  but avoid strings of unexplained acronyms.
- Use British English, spell “onchain” without a hyphen, and use sentence case
  for headings.

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
  acquisitions, operating history and documented performance when known.
  Include material losses, defaults, closures or strategy changes where they
  help explain the history. Give dates and the period covered by performance
  figures; distinguish live results from backtests and company history from
  the history of a particular vault.
- **Fees, funding and business model:** where applicable, explain how the
  operator earns money, who provides capital, who receives payments and who
  bears losses. For vaults, cover material management or performance fees,
  borrowing costs, withdrawal charges, use of borrowed money and any arrangement
  under which one party absorbs losses first. Explain what a fee is charged on
  and how often when those terms are known.
- **Access and limitations:** include withdrawal restrictions, eligibility and
  concrete risks when they materially affect how the product works. Tie them
  to the strategy instead of appending a generic list of risk categories.

## Remove AI slop and technical clutter

Use specific facts instead of promotional adjectives or abstract business
language. Remove phrases such as “innovative ecosystem”, “seamless access”,
“robust yield infrastructure”, “unlock opportunities” and “leverages cutting-edge
technology”. Replace “the business use case is to provide the yield component”
with a direct explanation of what generates the income.

Do not repeat the same point in different words, add rhetorical questions or
finish with a summary that merely repeats the opening. Avoid unsupported labels
such as “leading”, “safe”, “trusted” or “institutional-grade”.

Keep programming and repository details out of public descriptions: no scanner
behaviour, adapters, API methods, ABI names, classifiers, bytecode or metadata
repair history. Mention a network or technical mechanism only when it helps
the reader understand access, strategy, costs or a material limitation.

## Keep claims traceable and current

Use authoritative sources for factual claims, preferably official product
documentation, terms, fee schedules, reports and dated announcements. An
attributable official social post can support an announcement; it does not
establish a performance history or current holdings by itself.

Link material claims to their sources in Markdown-capable descriptions or
notes. For plain-text fields, preserve the sources in the accompanying note
or research file. Attribute manager-reported claims where needed.

Check that the evidence describes the actual vault or product. Do not turn a
provider's general investment approach into a claim about every vault it runs.
State a material evidence gap plainly and omit unsupported details rather than
filling them with plausible generic copy.

Date changing facts such as fees, funding totals and historical returns, and
verify them before adding or updating them. Do not present a past rate as a
current offer or a historical result as a promise of future returns.

## Final read

Read the copy as someone unfamiliar with the product. Can they understand what
their money funds, how income is earned and who is responsible? Keep the
verified people, business details and history that answer those questions.
Remove any sentence that adds only jargon, promotion or repetition.
