# RSS review

Reviewed on 2026-05-05.

This continues the broken RSS review started in `updated-tracking.md`.  The
scope was YAML files carrying `rss-failure-at`, with special attention to
whether the project is dead, the feed moved, or the failure is caused by the
feed host returning non-RSS HTML/rate limits.

Before this pass there were 69 YAML files with RSS failure stamps.  After the
feed fixes there were 44 remaining Medium-style rate-limit stamps.  These were
reset on 2026-05-05, because the URL often works on retry but Medium rate
limits automated collection.

## Updated feeds

| Files | Finding | Change |
|-------|---------|--------|
| `stablecoins/usde.yaml`, `stablecoins/usdtb.yaml`, `stablecoins/susde.yaml` | Ethena is active.  The old Mirror feed now redirects to Paragraph and can return 429 through the redirect path. | Replaced Mirror URL with direct Paragraph RSS: `https://api.paragraph.com/blogs/rss/@ethena-labs`. |
| `stablecoins/pyusd.yaml` | PayPal USD is active.  The old PayPal newsroom query URL now returns HTML. | Replaced with the current cryptocurrency newsroom RSS: `https://newsroom.paypal-corp.com/news-cryptocurrency?pagetemplate=rss`. |
| `stablecoins/ageur.yaml`, `stablecoins/eura.yaml`, `stablecoins/stusd.yaml`, `stablecoins/usda.yaml` | Angle is winding down the stablecoins, but its blog feed moved from stale Medium entries to Paragraph. | Replaced Medium RSS with `https://api.paragraph.com/blogs/rss/@angleprotocol`. |
| `stablecoins/cdai.yaml`, `stablecoins/cusdc.yaml`, `stablecoins/cusdt.yaml` | Compound is active.  Medium is stale; active updates are on the governance forum. | Replaced Medium RSS with `https://www.comp.xyz/latest.rss`. |
| `stablecoins/adai.yaml`, `stablecoins/gho.yaml` | Aave is active.  Medium is stale; active updates are on governance/forum RSS. | Replaced Medium RSS with `https://governance.aave.com/latest.rss`. |
| `protocols/gains-network.yaml`, `stablecoins/gdai.yaml` | Gains Network is active.  Medium is stale; active updates are on governance/forum RSS. | Replaced Medium RSS with `https://gov.gains.trade/latest.rss`. |
| `stablecoins/eusd-reserve.yaml` | Reserve blog RSS is alive.  The stored 429 was transient. | Removed stale RSS failure fields. |
| `stablecoins/csusd.yaml` | cSigma is active.  Medium RSS still parses; the official website blog has no RSS endpoint. | Removed stale RSS failure fields and added a note. |
| `stablecoins/vbusdc.yaml`, `stablecoins/vbusdt.yaml` | Katana is active.  Official blog is active but no RSS endpoint was found; Medium remains the only RSS source. | Removed stale RSS failure fields and added a note. |

## Disabled feeds

| Files | Finding | Change |
|-------|---------|--------|
| `curators/pareto-technologies.yaml` | Project is not dead.  Beehiiv archive and company site are live, but `/feed.xml` returns an HTML 404 page with HTTP 200. | Commented out RSS and added a note. |
| `protocols/euler.yaml` | Euler is active.  `newsletter.euler.finance/feed` redirects to the Euler homepage HTML; current blog has no advertised RSS. | Commented out RSS and added a note. |
| `stablecoins/rusd-ipor.yaml` | Reservoir is not dead.  Beehiiv archive is live, but `/feed` returns an HTML 404 page with HTTP 200. | Commented out RSS and added a note. |
| `stablecoins/usdxl.yaml` | HypurrFi is not dead.  Beehiiv archive is live, including 2026 posts, but `/feed` returns an HTML 404 page with HTTP 200. | Commented out RSS and added a note. |
| `stablecoins/gyen.yaml`, `stablecoins/zusd.yaml` | GMO Trust pages are active, but Medium is stale and the official press page has no RSS endpoint. | Commented out stale Medium RSS and added notes. |
| `stablecoins/jchf.yaml`, `stablecoins/jeur.yaml` | Jarvis Medium is historical and the project appears inactive. | Kept `rss-dead-at`, removed stale failure fields, and added notes. |

## Reset rate limits

The remaining `rss-failure-at` fields were reset on 2026-05-05.  They were
mostly Medium feeds that previously returned HTTP 429.  A live batch check
showed many of these return valid XML when not rate limited, and some return
HTTP 403 after a burst of requests from the same client.  Treat future 429/403
errors from these hosts as platform/rate-limit failures unless another pass
finds a clear replacement feed or a dead project.

Examples that still need individual judgement:

- `protocols/ipor-fusion.yaml`: Medium source appears alive/rate-limited.
- `protocols/morpho.yaml`: Medium is historical; Morpho has an active blog but
  no RSS endpoint was found.
- `stablecoins/usdm.yaml`: Mountain Protocol is winding down, but the old
  Medium feed failure is still a Medium rate-limit case.
- `stablecoins/musd.yaml`, `stablecoins/rai.yaml`, `stablecoins/ousd.yaml`,
  `stablecoins/usdr.yaml`: likely inactive or historical projects, but these
  need a separate dead-project pass before disabling RSS.

## Evidence checked

- `https://api.paragraph.com/blogs/rss/@ethena-labs`
- `https://api.paragraph.com/blogs/rss/@angleprotocol`
- `https://newsroom.paypal-corp.com/news-cryptocurrency?pagetemplate=rss`
- `https://www.comp.xyz/latest.rss`
- `https://governance.aave.com/latest.rss`
- `https://gov.gains.trade/latest.rss`
- `https://blog.reserve.org/feed`
- `https://paretotechnologies.beehiiv.com/feed.xml`
- `https://reservoir.beehiiv.com/feed`
- `https://hypurrfi.beehiiv.com/feed`
- `https://newsletter.euler.finance/feed`
