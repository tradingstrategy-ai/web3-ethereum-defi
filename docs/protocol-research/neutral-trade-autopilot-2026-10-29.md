# Neutral Trade Autopilot vaults

Reviewed on 2026-10-29. This records the sources and decisions behind the
Neutral Trade curator entry and the handwritten copy for its Autopilot
feeder vaults.

## Deployments

| Vault | Chain | Address | Notes |
|---|---|---|---|
| Neutral Trade Autopilot (NTA) | Ethereum | `0x909dadbca7955614a455d9e7447ad4adb4902c8e` | Accountable vault page uses loan address `0x56B935Fe5183cC0DE489233d032F9A4B8ec2f9Ff`; about 207,600 USDC in the 2026-10-28 metadata snapshot |
| Neutral Trade Autopilot (NTA) | Monad | `0xaabab7598be3c4fe58c593e73c2f5934b73b573e` | Accountable vault page uses loan address `0x5ee57E42DF67e5707F0CAE1a18DfeaDB4F0Df86c`; the cached row has no manager name, although Accountable's API now lists Neutral Trade; 10,000 USDC in the 2026-10-28 metadata snapshot |

Both are Accountable vaults. They feed Neutral Autopilot, Neutral Trade's
Solana vault (vault ID 76), which holds the main capital.

## Issues found

- **Wrong curator.** Both deployments resolved to IPOR because the generic
  `"Autopilot"` name pattern belongs to Harvest's IPOR Fusion Autopilot
  vaults. No Neutral Trade curator existed. The new `neutral-trade` curator is
  metadata-only: exact Accountable company name plus address overrides.
- **Ethereum copy.** The Ethereum deployment had scraped Accountable copy
  with US spelling and a malformed paragraph structure, and no notes.
- **Monad copy.** The earlier handwritten copy omitted the Solana feeder
  structure, shared exchange accounts and custody, and its investor list
  lacked YZi Labs, now named in the FAQ.
- **Strategy tags.** Neither deployment had tags. Both now have
  `multistrategy` and `delta_neutral`, through the chain-scoped curator tag
  map, because the Accountable protocol map only covers Monad.
- **Meridian Liquidity Provider.** Accountable lists the manager as
  "Meridian x Kappa x Neutral Trade". Neutral Trade distributes the vault and
  names Kappa Lab as its curator, so it is now attributed to Kappa Lab.

## Sources

- Neutral Trade's [September and October 2026 update](https://www.neutral.trade/blog/september-october-2026-update)
  says NTA is "curated by Neutral Trade and powered by Accountable NAV Vaults"
  and "allocates to Neutral Autopilot". It also says exchange capital stays in
  off-exchange custody with Ceffu or Copper, perp DEX accounts use Fordefi MPC
  policies and trading firms have trading authority only. It reports $4.2M
  Autopilot TVL on 2026-10-02, names Kappa Lab as Meridian's curator, and gives
  the Saturday Autopilot redemption schedule effective 2026-10-12.
- The cached Accountable description for the Ethereum vault, supplied by
  the manager, says Neutral Trade runs some Autopilot strategies in-house and
  vetted external trading firms run the rest.
- The [Autopilot strategy page](https://www.neutral.trade/strategies/neutral-autopilot?tab=deposit)
  offers Ethereum and Monad as "Deposit on another network" options.
- The [Autopilot launch post](https://www.neutral.trade/blog/neutral-autopilot-launch)
  (2026-08-21) gives the market-neutral core and the 5% cap on directional
  strategies. The [multi-strategy design post](https://www.neutral.trade/blog/neutral-autopilot-multi-strat-vault)
  describes allocation across independent trading firms.
- The [FAQ](https://www.neutral.trade/faq) gives the 2024 founding date and
  names Solana Labs, YZi Labs, DRW Labs, Skyland Ventures, BlackPine and Monke
  Ventures as company backers. Both the rendered answer text and the page's FAQ
  structured data said this when fetched twice on 2026-10-29.
- Neutral Trade's [launch post on X](https://x.com/tradeneutral/status/2085403562226725178)
  (2026-08-06) and Accountable's [YieldApp announcement](https://x.com/accountabledata/status/2106036446503047195)
  (2026-10-02). The latter says NAV is computed independently by
  Accountable's Data Verification Network.
- The statement that the Accountable EVM vault wraps the Solana vault, where
  the main capital is held, and that the deployments share centralised
  exchange accounts through custody arrangements, comes from Neutral Trade
  via the maintainer on 2026-10-29.

## Fees

Accountable's per-vault API (`/api/loan/608250934` for Ethereum and
`/api/loan/608250958` for Monad), read on 2026-10-29, reports each vault's
onchain fee manager. Both have `managementFee` 0, `establishmentFee` 0 and
`performanceFee` 30000 on a 1,000,000 basis, a 3% performance fee.
`protocolSplit` is 1,000,000 and `managerSplit` 0, so the whole performance
fee goes to Accountable's treasury. Both vaults also have a 50 USDC minimum
redemption.

## Evidence gaps

- Neutral Trade does not state whether its own Autopilot fees apply to
  money arriving through the Accountable vaults. Its deposit page displayed a
  0% performance fee for Ethereum and 3% for Monad, which disagrees with the
  onchain settings above; the copy uses the onchain settings.
- `Solana ETH-Super-Staking by Neutral Trade` (Enzyme, Ethereum,
  `0xa0ebf27dd6a3ad98a758041245f5a4b5fce03fff`, about 13 WETH) and
  `HYPE-NeutralTrade` (HyperEVM, `0x6000f684071ea5ba6cca1246094a165ac967bb71`,
  empty) carry the brand in their names, but no Neutral Trade source
  confirms them. They remain unattributed.
