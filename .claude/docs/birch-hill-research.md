# Birch Hill description research

Reviewed 2026-11-06. Public documentation identifies two deployed vaults on Base:

| Product | Address | Description accessor |
|---|---|---|
| Groma Ecosystem USDC | `0xb038cf3e233b78225f6d350ac8319bec8457db4a` | Morpho V2 |
| RWA USDC Flagship | `0xa5afd3a598ef000a5edd96ed65929ac7a6feea39` | Yearn V3 |

Vault navigation links use the published Trading Strategy pages, checked
against the technical details' contract addresses on 2026-11-06:
[Groma Ecosystem](https://tradingstrategy.ai/vaults/birch-hill-rwa-usdc) retains
its former RWA name in the slug, while
[Flagship](https://tradingstrategy.ai/vaults/birch-hill-rwa-usdc-2) uses the
numeric suffix. Do not derive either destination from the current display name.
Official documentation and announcements remain source citations; the Morpho
fee evidence is retained below rather than used as a vault navigation link.
The Groma description cites the Trading Strategy page's performance and fees
table for the 10% performance and 0% management figures, also confirmed there
on 2026-11-06. Its original Morpho evidence remains in the source checks below.

The [documentation index](https://docs.birchhill.io/llms.txt) contains vault books
for these two products. Their technical appendix and onchain references confirm
these addresses. The platform also renders an unnamed TBA product and a count
of hidden Flagship entries. No public identities or contracts were supplied for
those entries, so they are not invented as deployed vaults. The GRO/USDC market
is an investment of a vault, not a third vault.

Current Flagship mandate: holds Ecosystem vault positions and idle USDC, earning
borrower interest through the underlying shares. Its structure-and-loan-flow
page specifies a 10% fee on interest. Access-and-monitoring requires identity,
accreditation and wallet screening for investors, with onchain deposit and
withdrawal checks. Borrower verification is asset-specific; do not repeat the
July launch's blanket accredited-borrower wording as a current universal rule.

The Ecosystem vault book names GRO property-share lending and cbBTC-backed loans
for withdrawal liquidity. The October launch distinguishes direct deposits from
frontend identity verification and describes property valuation, default and
quarterly redemption risks. Morpho's vault page showed 10% performance and 0%
management fees in the current session. Neither vault promises immediate exit.
The Flagship description states its published fee without assuming a combined
all-in rate across both layers.

Public copy lives in `eth_defi/vault/handwritten_metadata.py`, with sources inline.
The curator YAML explains the organisation and funding; individual descriptions
explain what deposits fund and how access and withdrawals work. Both fields are
verified through the adapter, scanner and individual JSON-export boundary.

Migration 038 updates only existing rows' two description fields. It reports
missing targets and does not discover vaults, change attribution or price data.
Production migration and normal export publication remain operational steps.

## Source checks

All checks below were made on 2026-11-06. The sources support these specific
claims; they do not establish a combined fee across the two vault layers.

- The [Groma vault book](https://docs.birchhill.io/vault-books/birch-hill-groma-ecosystem-usdc-vault)
  names GromaCorp and verified GRO holders as borrowers, and describes property
  equity in Boston and Providence and the cbBTC liquidity allocation.
- The [Flagship mandate](https://docs.birchhill.io/vault-books/birch-hill-rwa-usdc/1.-strategy-mandate)
  names institutional and accredited investors, Ecosystem positions and idle
  USDC. It explicitly describes verified token holders; this does not mean all
  borrowers must satisfy the investors' accreditation requirement.
- The [Flagship risk controls](https://docs.birchhill.io/vault-books/birch-hill-rwa-usdc/3.-risk-controls)
  describe inherited bhGROeco market controls and the cbBTC liquidity market.
  The [technical appendix](https://docs.birchhill.io/vault-books/birch-hill-rwa-usdc/6.-technical-appendix)
  identifies the Flagship accountant's 10% performance fee on interest.
- The [platform page](https://birchhill.io/platforms) names BH Digital Asset
  Management as the Flagship adviser and states restricted investor eligibility.
- The [Morpho vault page](https://app.morpho.org/base/vault/0xb038cF3e233b78225F6D350AC8319bEC8457db4A/birch-hill-groma-ecosystem-usdc)
  showed 10% performance and 0% management fees for Groma Ecosystem USDC.
- The [October funding announcement](https://www.businesswire.com/news/home/20261028277814/en/Birch-Hill-Raises-%244-Million-Seed-Round-Led-by-Multicoin-Capital-to-Bring-U.S.-Credit-Onchain)
  identifies the New York firm, the $4 million Multicoin-led seed round, ParaFi
  Capital, Castle Island Ventures and Nascent, and $6.5 million total financing.
