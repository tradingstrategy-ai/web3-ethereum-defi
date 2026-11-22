# Morpho curators

Source: `https://data.morpho.org/curation`
Extracted: 2026-05-05 (38 verified curators)

## API

The authoritative source is the **Morpho Blue GraphQL API** — public, no auth required.

**Endpoint:** `POST https://blue-api.morpho.org/graphql`

**Query:**

```graphql
{
  curators(first: 100) {
    items {
      id
      name
      description
      verified
      image
      addresses {
        chainId
        address
      }
      state {
        aum
      }
    }
    pageInfo {
      countTotal
    }
  }
}
```

**Logo CDN pattern:** `https://cdn.morpho.org/v2/assets/images/{slug}.{svg|png}`

## How to extract

The `data.morpho.org/curation` page is a Next.js App Router (RSC) app. It makes **no** XHR/fetch API calls visible in DevTools — all data is embedded in `__next_f.push(...)` inline scripts in the SSR HTML. The GraphQL API above is the clean programmatic alternative.

### Python snippet

```python
import requests

GRAPHQL_URL = "https://blue-api.morpho.org/graphql"

QUERY = """
{
  curators(first: 100) {
    items {
      id
      name
      description
      verified
      image
      addresses { chainId address }
      state { aum }
    }
    pageInfo { countTotal }
  }
}
"""

def fetch_morpho_curators() -> list[dict]:
    """Fetch all verified Morpho curators from the Blue API."""
    resp = requests.post(GRAPHQL_URL, json={"query": QUERY}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["data"]["curators"]["items"]

curators = fetch_morpho_curators()
for c in curators:
    print(c["id"], c["name"], c["state"]["aum"])
```

### Alternative: parse SSR HTML (fallback)

If the GraphQL API is unavailable, fetch the page HTML and extract the RSC payload:

```python
import re, json, requests

def fetch_morpho_curators_from_html() -> list[dict]:
    """Parse curators from the SSR RSC payload in the curation page HTML."""
    html = requests.get("https://data.morpho.org/curation", timeout=30).text
    # Find all __next_f.push script contents
    scripts = re.findall(r'self\.__next_f\.push\(\[1,"(.+?)"\]\)', html, re.DOTALL)
    for raw in scripts:
        # Unescape the JSON-encoded string
        decoded = raw.replace(r'\n', '\n').replace(r'\"', '"').replace(r'\\', '\\')
        if '"table":[' not in decoded:
            continue
        # Extract the table array
        idx = decoded.index('"table":[') + 8
        depth, end = 0, idx
        for i in range(idx, len(decoded)):
            if decoded[i] == '[': depth += 1
            elif decoded[i] == ']':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        table = json.loads(decoded[idx:end])
        return [
            {
                "name": row["name"],
                "auc_usd": round(row["value"]),
                "image": row.get("icon") if row.get("icon") != "$undefined" else None,
            }
            for row in table
        ]
    return []
```

Note: the HTML method only returns curators with current AUC (from the paginated table, page 1 only in SSR) — the GraphQL API is preferred.

## Curator list (2026-05-05)

| ID | Name | AUM USD | Chains | Image slug |
|----|------|---------|--------|------------|
| `9summits` | 9Summits | $350K | 1, 130, 8453 | `9summits.png` |
| `alphaping` | AlphaPing | $52M | 1, 999, 8453, 42161 | `alphaping.png` |
| `anthias-labs` | Anthias Labs | $25M | 8453 | `anthias.svg` |
| `api3` | Api3 | $8M | 1 | `api3.svg` |
| `apostro` | Apostro | $269K | 1, 8453 | `apostro.svg` |
| `architect` | Architect | ~$0 | 8453 | `architect.svg` |
| `august-digital` | August Digital | $20M | 1, 143 | `august.svg` |
| `avantgarde` | Avantgarde | $1.6M | 1, 8453, 42161 | `avantgarde.svg` |
| `b-protocol` | B.Protocol | $507K | 1, 10, 8453 | `bprotocol.png` |
| `block-analitica` | Block Analitica | $507K | 1, 10, 8453 | `block-analitica.png` |
| `clearstar` | Clearstar | $19M | 1, 130, 137, 8453, 42161, 747474 | `clearstar.svg` |
| `compound-dao` | Compound DAO | $5.8M | 137 | `compound.svg` |
| `eco-vaults-shf` | Ecosystem Vaults by SHF | ~$0 | 1 | `eco-sh.svg` |
| `felix` | Felix | $89M | 999 | `felix.svg` |
| `flowdesk` | Flowdesk | $20M | 1 | `flowdesk.svg` |
| `galaxy` | Galaxy Curation | $49M | 1, 8453 | `galaxy.png` |
| `gauntlet` | Gauntlet | $913M | 1, 10, 130, 137, 988, 999, 8453, 42161, 747474 | `gauntlet.svg` |
| `hakutora` | Hakutora | $25M | 1 | `hakutora.png` |
| `hyperithm` | Hyperithm | $63M | 1, 143, 988, 999, 42161, 747474 | `hyperithm.svg` |
| `k3-capital` | K3 Capital | $130K | 1, 130 | `k3-capital.svg` |
| `keyrock` | Keyrock | $775K | 1 | `keyrock.svg` |
| `kpk` | KPK | $32M | 1, 42161 | `kpk.svg` |
| `mev-capital` | MEV Capital | $11M | 1, 130, 137, 999, 8453, 42161, 747474 | `mevcapital.png` |
| `moonwell` | Moonwell | $25M | 10, 8453 | `moonwell.svg` |
| `pangolins` | Pangolins | $26M | 1, 130, 8453 | `pangolins.svg` |
| `re7-labs` | RE7 Labs | $3.6M | 1, 10, 130, 137, 480, 999, 8453, 42161, 747474 | `re7.png` |
| `rockawayx` | RockawayX | $11M | 1 | `rkx.svg` |
| `sentora` | Sentora | $472M | 1 | `sentora.svg` |
| `singularv` | SingularV | $2.2M | 1, 130, 747474 | `singularv.svg` |
| `sky-money` | Sky Money | $196M | 1 | `skymoney.svg` |
| `sparkdao` | SparkDAO | $10M | 1, 8453 | `spark.svg` |
| `stake-dao` | Stake DAO | $7.8M | 1, 10, 137, 8453, 42161 | `stakedao.svg` |
| `steakhouse-financial` | Steakhouse Financial | $1.90B | 1, 130, 137, 143, 8453, 42161, 747474 | `steakhouse.svg` |
| `swissborg` | Swissborg | $11M | 1 | `swissborg.svg` |
| `test` | Prime USDC Vault | $120K | 8453 | `TEST.svg` |
| `ultrayield` | UltraYield | $1.4M | 1, 10, 143, 8453, 42161 | `ultrayield.svg` |
| `unified-labs` | Unified Labs | $100K | 1, 137, 143, 42161 | `unified-labs.svg` |
| `yearn` | Yearn | $33M | 1, 8453, 42161, 747474 | `yearn.svg` |

Chain IDs: 1=Ethereum, 10=Optimism, 130=Unichain, 137=Polygon, 143=Katana, 480=WorldChain, 988=Monad, 999=HyperEVM, 8453=Base, 42161=Arbitrum, 747474=Plume

## Notes

- `b-protocol` and `block-analitica` share the same vault addresses (co-managed vaults)
- `test` (id) / "Prime USDC Vault" (name) appears to be a test/legacy entry
- The `data.morpho.org/curation` page shows 24 curators in its AUC table — smaller curators are aggregated under "Other"; the GraphQL API returns all 38 individually
- Historical curators visible only in chart data (no current AUC): none at time of writing — all 38 have some registered addresses
