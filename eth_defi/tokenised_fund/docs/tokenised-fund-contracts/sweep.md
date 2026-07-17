# State Street Galaxy Onchain Liquidity Sweep Fund (SWEEP) contract research

Checked on 2026-07-17. This is contract-discovery research, not an assessment
of investor eligibility, legal rights, custody, or investment suitability.

## Result

| Fund name | Chain and token | Smart-contract name | Description | GitHub | Docs |
| --- | --- | --- | --- | --- | --- |
| State Street Galaxy Onchain Liquidity Sweep Fund (`SWEEP`) | Solana at launch; **no public mint address or Solana program address discovered** | Not publicly disclosed | A tokenised private-liquidity-fund product operated using Galaxy Digital Infrastructure. Official material identifies the commercial lifecycle (PYUSD/USDC subscriptions, PYUSD/USD redemptions and a non-rebasing token) but does not disclose the token mint, token-program variant, transfer-control programme, or issuance/redemption programme. | No public Galaxy/State Street SWEEP smart-contract repository was found in GitHub/web searches. Galaxy describes its tokenisation technology as proprietary. | [Galaxy SWEEP fund page](https://am.galaxy.com/galaxy-state-street-sweep-fund), [State Street launch release](https://investors.statestreet.com/investor-news-events/press-releases/news-details/2026/State-Street-Investment-Management-and-Galaxy-Digital-Bring-Cash-Management-Onchain/default.aspx), [Galaxy tokenisation overview](https://www.galaxy.com/tokenization) |

## What is publicly established

State Street and Galaxy officially launched SWEEP on 5 May 2026 as a tokenised
private liquidity fund. The State Street release says that Galaxy's Digital
Infrastructure provides the tokenisation technology for issuing and managing
SWEEP tokens; it also names Anchorage as digital custodian, NAV Consulting as
transfer agent, Chainlink NAVLink for daily on-chain NAV publication, and
Chainlink CCIP for planned cross-chain interoperability.

Galaxy's fund page says the fund launched on Solana, is non-rebasing, accepts
PYUSD and USDC subscriptions at any time (and USD on business days), and permits
daily USD redemption or 24/7 PYUSD redemption subject to portfolio availability.
The stated next-chain plans are Stellar and Ethereum. These sources establish
that a Solana on-chain token exists, but they do not publish the Solana mint or
a programme address.

## Mint and programme discovery result

**No attributable SWEEP mint or contract/programme address is publicly
discoverable as of the check date.**

The official fund page, State Street release, Galaxy tokenisation page, Galaxy
Digital Assets Portal link, and the public Form D records were checked. None
states a Solana base58 mint address, transaction signature, token account,
programme ID, explorer link, or an SPL Token versus Token-2022 choice. The
issuer instead directs prospective users to a Qualified Purchaser-gated Digital
Assets Portal.

Focused public web searches combining the complete fund name, `SWEEP`,
`Solana`, `mint`, `token address`, `Solscan`, `program`, and
`contract address` found launch reporting but no attributable mint. The same
search terms on GitHub found no public Galaxy/State Street SWEEP Solidity, Rust,
Anchor, or Solana-program repository. Searches for the distinctive issuer
phrases `Galaxy Digital Infrastructure` and `SWEEP tokens` likewise led
only to issuer marketing/press material.

This negative result is material: without the mint address, it is not possible
to query a Solana explorer for the token's owner programme, mint authority,
freeze authority, Token-2022 extensions, transfer-hook configuration, metadata,
supply, or transactions. It would be unsafe to infer a mint from an unrelated
ticker-matched Solana token.

## Smart-contract/framework conclusion

**Conclusion: proprietary Galaxy tokenisation infrastructure, with the
deployed SWEEP token/programme intentionally not publicly identified — high
confidence for the negative discovery result.**

Galaxy describes its in-house tokenisation platform as proprietary technology
for compliant digital tokens. The SWEEP announcement attributes issuance and
management to Galaxy Digital Infrastructure, rather than identifying a public
framework such as SPL Token, Token-2022, Metaplex, Superstate FundOS,
ERC-3643/T-REX, or a named open-source Solana programme. No evidence supports
assigning SWEEP to any of those frameworks.

This is unlike Galaxy's separate tokenised `GLXY` equity product: Galaxy
publishes that product's exact Solana token address and identifies Superstate
as transfer agent. The absence of equivalent SWEEP disclosure should be treated
as deliberate lack of public contract metadata, not evidence that the GLXY
mint, Superstate contracts, or any other Galaxy-associated Solana token belong
to SWEEP.

The publicly documented product-level controls are:

| Publicly stated component | Publicly stated role | On-chain address available? |
| --- | --- | --- |
| Galaxy Digital Infrastructure | Tokenisation technology; issuance and management of SWEEP tokens | No |
| SWEEP Solana token | Non-rebasing representation of fund units at launch | No mint or token account disclosed |
| Chainlink NAVLink | Publication of daily NAV on-chain | No SWEEP-linked feed/address disclosed |
| Chainlink CCIP | Intended secure cross-chain interoperability | No SWEEP-linked sender/receiver disclosed |
| Anchorage / NAV Consulting | Digital custody / transfer agency | Service providers, not disclosed Solana programmes |

## On-chain supply and ABI price availability

Not available. The issuer has not disclosed a SWEEP Solana mint or programme,
so neither the mint supply nor an ABI/programme share-price interface can be
queried safely.

## Integration implications

- Do not add a guessed SPL mint, generic `SWEEP` ticker result, or Galaxy's
  separate GLXY token address to production metadata.
- Classify SWEEP as **tracked fund, contract address unknown/not publicly
  disclosed** until the issuer provides a mint address through a trustworthy
  source or an authorised investor supplies a verifiable on-chain transaction.
- Once a mint is obtained, independently verify it against an issuer-controlled
  source, inspect it in [Solana Explorer](https://explorer.solana.com/) or
  [Solscan](https://solscan.io/), and record its owner programme, authorities,
  Token-2022 extensions and transfer restrictions before treating it as
  transferable collateral.
- The press release makes clear that the fund is a private placement for
  eligible Qualified Purchasers. A publicly visible mint, if subsequently
  supplied, would not itself establish transfer or redemption eligibility.

## Primary sources

- [State Street launch release](https://investors.statestreet.com/investor-news-events/press-releases/news-details/2026/State-Street-Investment-Management-and-Galaxy-Digital-Bring-Cash-Management-Onchain/default.aspx)
- [Galaxy SWEEP fund page](https://am.galaxy.com/galaxy-state-street-sweep-fund)
- [Galaxy tokenisation overview](https://www.galaxy.com/tokenization)
- [SEC Form D: SWEEP onshore fund](https://www.sec.gov/Archives/edgar/data/2130185/000090266426002249/xslFormDX01/primary_doc.xml)
- [Solana Explorer](https://explorer.solana.com/) and [Solscan](https://solscan.io/) (no SWEEP mint was supplied by the issuer to query)
