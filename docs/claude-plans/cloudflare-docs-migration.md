# Migrate Sphinx docs hosting to Cloudflare Pages

## Current setup

- **Primary hosting**: ReadTheDocs (`.readthedocs.yml`) - will keep as backup
- **Secondary hosting**: GitHub Pages via `.github/workflows/docs.yml`
- **Build output**: Static HTML at `docs/build/html/`
- **Current URL**: `https://web3-ethereum-defi.readthedocs.io/`
- **New URL**: `https://web3-ethereum-defi.tradingstrategy.ai/`

## Migration approach

### 1. Add Makefile target for Cloudflare deployment

Add to `Makefile`:

```makefile
# Deploy documentation to Cloudflare Pages
#
# Prerequisites:
# 1. Install wrangler: npm install -g wrangler
# 2. Create Cloudflare API token:
#    - Go to https://dash.cloudflare.com/profile/api-tokens
#    - Click "Create Token"
#    - Use "Edit Cloudflare Workers" template (includes Pages permissions)
#    - Or create custom token with: Account > Cloudflare Pages > Edit
# 3. Get your Account ID:
#    - Log in to Cloudflare dashboard
#    - Account ID is in the right sidebar under "Account details"
# 4. Set environment variables:
#    - export CLOUDFLARE_API_TOKEN=your_token_here
#    - export CLOUDFLARE_ACCOUNT_ID=your_account_id_here
#
# Usage:
#   make deploy-docs-cloudflare     (builds and deploys)
#   make deploy-docs-cloudflare-only (deploys existing build)
#
deploy-docs-cloudflare: build-docs deploy-docs-cloudflare-only

deploy-docs-cloudflare-only:
	@if [ -z "$$CLOUDFLARE_API_TOKEN" ]; then echo "Error: CLOUDFLARE_API_TOKEN not set"; exit 1; fi
	@if [ -z "$$CLOUDFLARE_ACCOUNT_ID" ]; then echo "Error: CLOUDFLARE_ACCOUNT_ID not set"; exit 1; fi
	npx wrangler pages deploy docs/build/html --project-name=web3-ethereum-defi
```

### 2. Update GitHub Actions workflow

Modify `.github/workflows/docs.yml`:

- Remove GitHub Pages artifact upload and deployment steps
- Add Cloudflare deployment using Makefile:

```yaml
- name: Install wrangler
  run: npm install -g wrangler

- name: Deploy to Cloudflare Pages
  env:
    CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
    CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
  run: make deploy-docs-cloudflare-only
```

### 3. Add secrets to GitHub repository

Go to: Repository → Settings → Secrets and variables → Actions → New repository secret

Add:
- `CLOUDFLARE_API_TOKEN` - API token with Pages edit permissions
- `CLOUDFLARE_ACCOUNT_ID` - your Cloudflare account ID

### 4. Create Cloudflare Pages project

1. Go to Cloudflare dashboard → Workers & Pages → Create
2. Create new Pages project named `web3-ethereum-defi`
3. Initial deploy will be at `web3-ethereum-defi.pages.dev`
4. Add custom domain: configure routing for `web3-ethereum-defi.tradingstrategy.ai/web3-ethereum-defi/`

### 5. Keep ReadTheDocs as backup

- Keep `.readthedocs.yml` unchanged
- RTD will continue building in parallel

### 6. Update Sphinx configuration for new base URL

Modify `docs/source/conf.py`:

```python
html_baseurl = "https://web3-ethereum-defi.tradingstrategy.ai/"
```

### 7. Analytics (optional)

**Current state:**
- Google Site Verification tag exists in `docs/source/_templates/base.html`
- No Google Analytics configured (RTD doesn't have analytics for this project)

**To add Google Analytics** (optional), add to `docs/source/_templates/base.html` before `</head>`:
```html
<script async src="https://www.googletagmanager.com/gtag/js?id=G-XXXXXXXXXX"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', 'G-XXXXXXXXXX');
</script>
```

**Google Search Console:**
- Current verification tag preserved: `i0qQRaR9OA3tSz_9tDocdcXGY27Ox_cy4FrvTHD2C_0`
- Add new property for `web3-ethereum-defi.tradingstrategy.ai` in Google Search Console
- Submit new sitemap: `https://web3-ethereum-defi.tradingstrategy.ai/sitemap-generated.xml`

## Files to modify

1. `Makefile` - add `deploy-docs-cloudflare` target
2. `.github/workflows/docs.yml` - use Makefile for deployment
3. `docs/source/conf.py` - update `html_baseurl`
4. `docs/source/_templates/base.html` - add analytics snippet (optional)
5. `docs/claude-plans/cloudflare-docs-migration.md` - save this plan

## Verification

1. Test locally: `make deploy-docs-cloudflare`
2. Push changes to trigger CI workflow
3. Check Cloudflare Pages dashboard for successful deployment
4. Verify docs accessible at `https://web3-ethereum-defi.tradingstrategy.ai/web3-ethereum-defi/`
