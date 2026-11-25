# Nest AXI source evidence

Reviewed on 2 November 2026 for the description override of Plume vault
`98866:0xe154b281bd9ef3bd94df2364799d0b22b1d4ebd0`.

## Confirmed product connection

[Nest's product-specific catalogue](https://api.nest.credit/v1/vaults/nest-axi-vault)
returns `name: Nest AXI Vault`, `symbol: nAXI` and share token
`0x7488b23f4c26b44eef2e0766896be47443e86d79`. Its relevant fields are:

```json
{
  "targetComposition": [
    {
      "priorityChain": 98866,
      "slug": "axiym-axi",
      "name": "Axiym AXI",
      "description": "Payment Settlement Financing"
    }
  ]
}
```

The same response maps the USDC entrypoint to the reviewed address above and
lists an XUPL entrypoint `0x99edd0d70ac2893713110148bf3f32ac1894327b` using
Plume asset `0x72ee4fb6ed94436438e9c45c39660d7c2b4152f5`.
This is explicit first-party evidence of intended Axiym exposure, stronger
than the previously identified overlap in the InterFi marketing wording.
The catalogue describes target composition; it is not a loan agreement or
independent verification of current offchain investments.

[The AXI transparency panel](https://app.plume.org/vaults/nest-axi-vault?section=transparency)
displayed XUPL as approximately 100% of the allocation, with a small separate
liquid stablecoin balance. This supports the immediate asset holding, not the
identity of the ultimate borrowers.

[The explorer's token record](https://explorer.plume.org/api/v2/tokens/0x72ee4fb6ed94436438e9c45c39660d7c2b4152f5)
identifies that exact Plume asset as `USDC PayFi Lending Vault`, symbol
`XUPL`, with six decimals. Do not identify deployments by ticker alone.

[Axiym's platform terms](https://www.axiym.io/terms-conditions), dated
1 March 2026, identify its operator as Axi Labs AG, Swiss company number
CHE-299.451.950. Financing requires a separate facilities agreement with
Axiym or a relevant affiliate. These general terms do not identify the
vault's particular contracting entity or establish that Nest is a party.

## Supporting announcements and examples

- [Avalanche's Axiym overview, 24 April 2025](https://www.avalanche.com/about/blog/how-axiym-is-rewiring-global-payments-on-avalanche), explains real-time payment financing for licensed money service businesses and uses the InterFi name also found in AXI's description.
- [Freemarket's partnership announcement, 10 April 2025](https://www.wearefreemarket.com/news/freemarket-and-axiym-partner-to-unlock-instant-liquidity-for-businesses), describes Axiym financing inside Freemarket accounts in USD and EUR. Freemarket is a platform partner; its individual financed customers are not named.
- [Tether's Hyundai announcement, 13 July 2026](https://tether.io/news/global-industrial-conglomerate-hyundai-completes-enterprise-treasury-pilot-on-tether-usdt-moving-corporate-funds-across-global-borders/), describes a $20,000 settlement pilot between Hyundai Motor America and Hyundai Motor de México using Axiym. It does not describe a loan or identify nAXI as the funding source.
- [Plume's OpenTrade integration announcement, 22 January 2025](https://www.prnewswire.com/news-releases/nest-integrates-opentrade-to-enhance-rwa-yield-offerings-on-plume-network-302357849.html), confirms a platform-wide integration focused on Treasury-backed products. It does not establish the specific nAXI/XUPL lending structure.

## Remaining gaps and excluded inferences

- No reviewed source names the businesses borrowing money attributable to this vault, or establishes that it financed either example above.
- No reviewed vault-specific agreement establishes the full lending chain, the borrowers' financing costs or the division of income among the parties.
- Token Terminal separately lists an OpenTrade product called Axiym PayFi Lending Vault (`xAPFL`). This is a research lead, not proof that `xAPFL` is the same instrument as the exact Plume XUPL contract. Do not transplant its generic legal or collateral claims into nAXI's description.
- OpenTrade's current contract directory does not list the reviewed Plume XUPL address. The XUPL explorer record resolves to a `PoolDynamic` implementation, but the reviewed source text does not identify Axiym or establish an OpenTrade legal relationship. Similar contract code or tickers cannot fill that gap.

The public copy should state the confirmed target exposure and immediate XUPL
holding, while limiting uncertainty to the legal terms and ultimate borrowers.
Do not retain the earlier suggestion that AXI and Axiym are connected only by
matching marketing language.

Migration `023-nest-axi-description.py` was prepared alongside this copy and
had not been applied during authoring. It reads the maintained copy, so no
additional migration is needed during authoring. Any change after a successful
production apply requires a new numbered migration.
