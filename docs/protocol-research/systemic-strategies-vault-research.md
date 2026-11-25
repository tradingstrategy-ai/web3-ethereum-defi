# Systemic Strategies L/S Grids

Research date: 6 November 2026. Scope: Hypercore (9999),
`0x07fd993f0fa3a185f7207adccd29f7a87404689d`, the
[published L/S Grids vault](https://tradingstrategy.ai/vaults/systemic-strategies-l-s-grids).

## First-party evidence

- The public [Hyperliquid info API](https://api.hyperliquid.xyz/info), queried
  with `type=vaultDetails` and this `vaultAddress`, returned the vault name,
  its low-inflation/unlock long and high-inflation/unlock short grid mandate,
  `leaderCommission=0.1`, `isClosed=false` and `allowDeposits=true`.
- The operator's [3 November 2026 exposure-management post](https://x.com/SystemicStratHL/status/2117599240150987067)
  describes readjusting positions across its vaults to avoid extreme exposures.
  It does not publish numerical limits for this particular vault. X returned
  HTTP 403; the authored post and timestamp were retrieved through the public
  FXTwitter status API and its text corroborated in the
  [TwStalker thread mirror](https://w.twstalker.com/SystemicStratHL/status/2117599240150987067).
- Rini's [12 August 2026 beta-cohort announcement](https://x.com/rini_xyz/status/2087554972061966390)
  names Systemic Strategies and describes existing legacy vaults as lacking
  access to HIP-3 and crypto spot markets. Retrieved through the FXTwitter
  status API and corroborated by [Rini's announcement channel](https://t.me/s/rini_xyz).
  This is an infrastructure participation announcement, not evidence that
  L/S Grids migrated or gained new market access; omit it from the strategy copy.
- The vault's own `tinyurl.com/systemicStrategies` link resolves to a
  [Telegram invite](https://t.me/+NJttB-FJfvYxZDFl). It does not expose a public
  announcement archive; no private group membership or messages were accessed.
- [Hyperliquid's depositor terms](https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/for-vault-depositors-legacy)
  describe profit sharing, losses, potential withdrawal slippage and the
  one-day user-vault lock-up.

## Copy boundaries

Explain grid orders, supply inflation, unlocks and long/short exposure in plain
language. The operator's brief exposure-management post was reviewed but is
excluded from the public description; it does not establish this vault's policy.
Do not import HyperGrowth's HYPE-specific mandate, personal trade ideas, Rini
roadmap access or third-party performance statistics. Exact grid settings,
position limits and a measured high win rate are not established by these
sources. The existing HFT classification is a maintainer request, not measured
performance evidence. No promotional return promises or routine chart metrics
belong in the description.

The perpetual-future glossary slug was verified. `grid-trading` is not a
published glossary page (HTTP 404), so the description explains it inline.
