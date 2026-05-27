# CI cost reduction implementation plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut per-PR GitHub Actions billable minutes by ≥60% via workflow restructuring and test tiering, without losing master-branch coverage.

**Architecture:** Three sequential steps, each shipped as its own PR with a measurement gate before proceeding. Step 1: workflow-only cheap wins (no test moves). Step 2: unit-manifest split in test.yml (no test moves). Step 3: per-subsystem `git mv` + per-subsystem workflow files.

**Tech Stack:** GitHub Actions YAML, pytest, ruff, Poetry, Foundry v1.2.3, soldeer.

**Spec:** `docs/superpowers/specs/2026-05-27-ci-cost-reduction-design.md`

---

## Chunk 1: Step 1 — cheap wins (no test reorg)

Files touched:
- Modify: `.github/workflows/test.yml`
- Create: `.github/workflows/lint.yml`

---

### Task 1: Capture baseline CI metrics

**Files:** none (read-only investigation)

- [ ] **Step 1: Query last 14 days of test.yml PR runs**

```bash
gh api "repos/tradingstrategy-ai/web3-ethereum-defi/actions/workflows/test.yml/runs?per_page=50&event=pull_request" \
  --jq '.workflow_runs[] | {conclusion: .conclusion, started: .run_started_at, updated: .updated_at}' \
  | head -60
```

- [ ] **Step 2: Compute median wall-clock manually**

Wall-clock = `updated_at` − `run_started_at`. Multiply by 16 = billable minutes. Write the numbers in a comment on issue #1034 for the record. Do NOT commit anything.

---

### Task 2: Add draft-skip, `ready_for_review` trigger, and `paths-ignore` to `test.yml`

**Files:**
- Modify: `.github/workflows/test.yml` lines 3-8 (the `on:` block) and line 10-19 (the job-level `if:`)

- [ ] **Step 1: Replace the `on:` block**

Current (lines 3-8):
```yaml
on:
  push:
    branches: [master]
  pull_request:
    branches: [master]
```

Replace with:
```yaml
on:
  push:
    branches: [master]
  pull_request:
    branches: [master]
    types: [opened, synchronize, reopened, ready_for_review]
    paths-ignore:
      - 'docs/**'
      - 'scripts/**'
      - '**.md'
      - 'eth_defi/data/vaults/**'
      - '.github/workflows/!(test.yml)'
```

- [ ] **Step 2: Add draft-skip condition to the job**

In the `test-python:` job block, after `runs-on:` group line, add:
```yaml
    if: github.event.pull_request.draft == false || github.event_name == 'push'
```

- [ ] **Step 3: Verify YAML is valid**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml'))" && echo "YAML OK"
```

Expected: `YAML OK`

---

### Task 3: Extract lint to its own workflow

**Files:**
- Create: `.github/workflows/lint.yml`
- Modify: `.github/workflows/test.yml` (remove trailing `Ruff lint check` step, lines 172-174)

- [ ] **Step 1: Create `.github/workflows/lint.yml`**

```yaml
name: Lint

on:
  push:
    branches: [master]
  pull_request:
    branches: [master]
    types: [opened, synchronize, reopened, ready_for_review]

jobs:
  ruff:
    if: github.event.pull_request.draft == false || github.event_name == 'push'
    runs-on: ubuntu-latest

    concurrency:
      group: ${{ github.workflow }}-${{ github.ref }}
      cancel-in-progress: true

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.14
        uses: actions/setup-python@v5
        with:
          python-version: "3.14"

      - name: Install ruff
        run: pipx install ruff

      - name: Ruff format check
        run: ruff format --check --diff
```

- [ ] **Step 2: Remove the trailing `Ruff lint check` step from `test.yml`**

Delete lines 172-174 from `test.yml`:
```yaml
      - name: Ruff lint check
        run: |
          poetry run ruff format --check --diff
```

- [ ] **Step 3: Validate both files**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/lint.yml'))" && echo "lint.yml OK"
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml'))" && echo "test.yml OK"
```

Expected: both `OK`.

---

### Task 4: Add Foundry binary cache to `test.yml`

**Files:**
- Modify: `.github/workflows/test.yml` — add cache step before `Install Foundry` (before line 83)

- [ ] **Step 1: Add cache step immediately before the `Install Foundry` step**

Insert before the `- name: Install Foundry` step:
```yaml
      - name: Cache Foundry
        uses: actions/cache@v4
        with:
          path: ~/.foundry
          key: foundry-v1.2.3-${{ runner.os }}
```

- [ ] **Step 2: Validate YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml'))" && echo "YAML OK"
```

---

### Task 5: Add soldeer dependency cache to `test.yml`

**Files:**
- Modify: `.github/workflows/test.yml` — add cache step before `Lagoon dependency issue smoke test` (before line 104)

- [ ] **Step 1: Add cache step immediately before the `Lagoon dependency issue smoke test` step**

Insert before `- name: Lagoon dependency issue smoke test`:
```yaml
      - name: Cache Lagoon soldeer deps
        uses: actions/cache@v4
        with:
          path: contracts/lagoon-v0/dependencies
          key: soldeer-${{ hashFiles('contracts/lagoon-v0/soldeer.lock', 'contracts/lagoon-v0/foundry.toml') }}
          restore-keys: |
            soldeer-
```

- [ ] **Step 2: Validate YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml'))" && echo "YAML OK"
```

---

### Task 6: Review Step 1 diff and commit

- [ ] **Step 1: Inspect the full diff**

```bash
git diff .github/workflows/
```

Verify:
- `on:` block has `types:` and `paths-ignore:`
- `test-python:` job has `if: github.event.pull_request.draft == false || ...`
- `Ruff lint check` step is gone from `test.yml`
- `lint.yml` exists with ubuntu-latest runner
- Foundry cache step present before `Install Foundry`
- Soldeer cache step present before `Lagoon dependency issue smoke test`

- [ ] **Step 2: Ask user to approve commit**

Do NOT commit. Show the diff summary and wait for explicit user approval.

---

### Chunk 1 measurement gate (after Step 1 PR merges)

After merge, wait 5 non-draft merged PRs then run:

```bash
gh api "repos/tradingstrategy-ai/web3-ethereum-defi/actions/workflows/test.yml/runs?per_page=20&event=pull_request" \
  --jq '.workflow_runs[] | {conclusion, started: .run_started_at, updated: .updated_at}'
```

Expected: median billable-min drop ≥30% vs baseline. If <15%, diagnose before Chunk 2.

---

## Chunk 2: Step 2 — unit/integration split in `test.yml` (no test file moves)

Files touched:
- Create: `scripts/ci/list-unit-tests.sh`
- Create: `tests/unit-manifest.txt` (generated, committed)
- Modify: `.github/workflows/test.yml`

---

### Task 7: Build and commit unit manifest

**Files:**
- Create: `scripts/ci/list-unit-tests.sh`
- Create: `tests/unit-manifest.txt`

- [ ] **Step 1: Create the classifier script**

```bash
mkdir -p scripts/ci
```

Create `scripts/ci/list-unit-tests.sh`:

```bash
#!/usr/bin/env bash
# Classifier: unit = test files that don't reference fork fixtures or live RPC env vars.
# Output is a sorted list of test file paths, one per line.
# Usage: bash scripts/ci/list-unit-tests.sh > tests/unit-manifest.txt
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

# Collect all test file paths (excluding gmx — already in its own workflow)
poetry run pytest tests/ --collect-only -q --ignore=tests/gmx 2>/dev/null \
  | grep -E '^tests/' \
  | awk -F'::' '{print $1}' \
  | sort -u \
  | while read -r f; do
      # Exclude if file uses any live RPC / fork fixture patterns
      if ! grep -qE \
        "mainnet_fork|web3_fork|anvil|JSON_RPC_|HYPERSYNC_API_KEY|GCP_ADC_CREDENTIALS|web3_arbitrum_fork|web3_ethereum_fork|web3_base_fork|web3_polygon_fork|web3_bnb_fork|web3_hyperliquid_fork" \
        "$f" 2>/dev/null; then
        echo "$f"
      fi
    done
```

```bash
chmod +x scripts/ci/list-unit-tests.sh
```

- [ ] **Step 2: Run classifier and capture manifest**

```bash
source .local-test.env && bash scripts/ci/list-unit-tests.sh > tests/unit-manifest.txt
```

- [ ] **Step 3: Review manifest**

```bash
wc -l tests/unit-manifest.txt
cat tests/unit-manifest.txt
```

Sanity check: manifest should contain only files that have no network/fork dependencies. If a file looks wrong (e.g. `tests/test_token.py` which may use JSON_RPC), open it and verify. Fix the classifier grep pattern if needed and re-run.

- [ ] **Step 4: Validate manifest files all exist**

```bash
while read -r f; do [ -f "$f" ] || echo "MISSING: $f"; done < tests/unit-manifest.txt
echo "All files checked"
```

Expected: `All files checked` with no `MISSING:` lines.

---

### Task 8: Split `test.yml` into `test-unit` and `test-integration` jobs

**Files:**
- Modify: `.github/workflows/test.yml`

The shared setup steps (checkout, Node.js, pnpm, poetry, Python, dependencies, Ganache, Foundry cache, Foundry install, Aave setup, soldeer cache, soldeer install, Web3.py verify) are duplicated into both jobs. This is intentional — GitHub Actions has no native step-sharing between jobs. Duplication is the standard pattern.

- [ ] **Step 1: Replace the single `test-python` job with two jobs**

Replace the entire `jobs:` section of `.github/workflows/test.yml` with:

```yaml
jobs:
  # ── Unit tests: fast, no fork, no live RPC ──────────────────────────────────
  test-unit:
    if: github.event.pull_request.draft == false || github.event_name == 'push'
    runs-on:
      group: Beefy runners
    concurrency:
      group: ${{ github.workflow }}-unit-${{ github.ref }}-${{ matrix.python-version }}
      cancel-in-progress: true
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.14"]
    name: Unit tests — Python ${{ matrix.python-version }}
    steps:
      - uses: actions/checkout@v3
        with:
          submodules: true
      - name: Setup Node.js
        uses: actions/setup-node@v3
        with:
          node-version: 18
          cache: "npm"
          cache-dependency-path: contracts/aave-v3-deploy/package-lock.json
      - name: Install pnpm
        uses: pnpm/action-setup@v4
        with:
          version: 10.21.0
      - name: Export PNPM location
        run: |
          PNPM_HOME="/home/runner/.local/share/pnpm"
          echo $PNPM_HOME >> $GITHUB_PATH
      - name: Install poetry (using matrix Python)
        run: pipx install "poetry>=2.3" --python "${{ env.PYTHON_PATH }}"
      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: "poetry"
          cache-dependency-path: |
            **/pyproject.toml
      - name: Install dependencies
        run: |
          poetry install
          poetry install -E docs -E data -E test -E hypersync -E ccxt -E duckdb -E posts
      - name: Install Ganache
        run: yarn global add ganache
      - name: Cache Foundry
        uses: actions/cache@v4
        with:
          path: ~/.foundry
          key: foundry-v1.2.3-${{ runner.os }}
      - name: Install Foundry
        uses: foundry-rs/foundry-toolchain@v1
        with:
          version: "v1.2.3"
      - name: Setup Aave v3 for tests
        run: |
          poetry run install-aave-for-testing
      - name: Build needed contracts
        run: |
          pnpm --version
      - name: Cache Lagoon soldeer deps
        uses: actions/cache@v4
        with:
          path: contracts/lagoon-v0/dependencies
          key: soldeer-${{ hashFiles('contracts/lagoon-v0/soldeer.lock', 'contracts/lagoon-v0/foundry.toml') }}
          restore-keys: |
            soldeer-
      - name: Lagoon dependency issue smoke test
        run: |
          export PATH="$HOME/.cargo/bin:$PATH"
          (cd contracts/lagoon-v0 && forge soldeer install)
          ls -lha contracts/lagoon-v0/dependencies/
          ls -lha contracts/lagoon-v0/dependencies/@openzeppelin-contracts-upgradeable-5.0.0/
      - name: Verify Web3.py version
        run: |
          poetry env info
          poetry run python --version
          poetry run python -c "import web3; print(f'Web3.py version: {web3.__version__}')"
      - name: Run unit tests
        run: |
          UNIT_FILES=$(cat tests/unit-manifest.txt | tr '\n' ' ')
          poetry run pytest $UNIT_FILES \
            --timeout-method=thread --tb=native -n auto -v -s --capture=no
        env:
          # Unit tests should not need any of these, but pass them anyway
          # so any misclassified test fails loudly rather than with a missing-env error.
          BNB_CHAIN_JSON_RPC: ${{ secrets.BNB_CHAIN_JSON_RPC }}
          JSON_RPC_POLYGON_ARCHIVE: ${{ secrets.JSON_RPC_POLYGON_ARCHIVE }}
          JSON_RPC_POLYGON: ${{ secrets.JSON_RPC_POLYGON }}
          JSON_RPC_ETHEREUM: ${{ secrets.JSON_RPC_ETHEREUM }}
          JSON_RPC_BASE: ${{ secrets.JSON_RPC_BASE }}
          JSON_RPC_BINANCE: ${{ secrets.JSON_RPC_BINANCE }}
          JSON_RPC_ARBITRUM: ${{ secrets.JSON_RPC_ARBITRUM }}
          JSON_RPC_PLASMA: ${{ secrets.JSON_RPC_PLASMA }}
          JSON_RPC_HYPERLIQUID: ${{ secrets.JSON_RPC_HYPERLIQUID }}
          ETHEREUM_JSON_RPC: ${{ secrets.JSON_RPC_ETHEREUM }}
          GOOGLE_CLOUD_PROJECT: ${{ secrets.GOOGLE_CLOUD_PROJECT }}
          GOOGLE_CLOUD_REGION: ${{ secrets.GOOGLE_CLOUD_REGION }}
          KEY_RING: ${{ secrets.KEY_RING }}
          KEY_NAME: ${{ secrets.KEY_NAME }}
          GCP_ADC_CREDENTIALS_STRING: ${{ secrets.GCP_ADC_CREDENTIALS_STRING }}
          TOKEN_RISK_API_KEY: ${{ secrets.TOKEN_RISK_API_KEY }}
          ARBITRUM_SEPOLIA_RPC_URL: ${{ secrets.ARBITRUM_SEPOLIA_RPC_URL }}
          ARBITRUM_GMX_TEST_SEPOLIA_PRIVATE_KEY: ${{ secrets.ARBITRUM_GMX_TEST_SEPOLIA_PRIVATE_KEY }}
          HYPERSYNC_API_KEY: ${{ secrets.HYPERSYNC_API_KEY }}

  # ── Integration tests: fork + live RPC ─────────────────────────────────────
  test-integration:
    if: github.event.pull_request.draft == false || github.event_name == 'push'
    runs-on:
      group: Beefy runners
    concurrency:
      group: ${{ github.workflow }}-int-${{ github.ref }}-${{ matrix.python-version }}
      cancel-in-progress: true
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.14"]
    name: Integration tests — Python ${{ matrix.python-version }}
    steps:
      - uses: actions/checkout@v3
        with:
          submodules: true
      - name: Setup Node.js
        uses: actions/setup-node@v3
        with:
          node-version: 18
          cache: "npm"
          cache-dependency-path: contracts/aave-v3-deploy/package-lock.json
      - name: Install pnpm
        uses: pnpm/action-setup@v4
        with:
          version: 10.21.0
      - name: Export PNPM location
        run: |
          PNPM_HOME="/home/runner/.local/share/pnpm"
          echo $PNPM_HOME >> $GITHUB_PATH
      - name: Install poetry (using matrix Python)
        run: pipx install "poetry>=2.3" --python "${{ env.PYTHON_PATH }}"
      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: "poetry"
          cache-dependency-path: |
            **/pyproject.toml
      - name: Install dependencies
        run: |
          poetry install
          poetry install -E docs -E data -E test -E hypersync -E ccxt -E duckdb -E posts
      - name: Install Ganache
        run: yarn global add ganache
      - name: Cache Foundry
        uses: actions/cache@v4
        with:
          path: ~/.foundry
          key: foundry-v1.2.3-${{ runner.os }}
      - name: Install Foundry
        uses: foundry-rs/foundry-toolchain@v1
        with:
          version: "v1.2.3"
      - name: Setup Aave v3 for tests
        run: |
          poetry run install-aave-for-testing
      - name: Build needed contracts
        run: |
          pnpm --version
      - name: Cache Lagoon soldeer deps
        uses: actions/cache@v4
        with:
          path: contracts/lagoon-v0/dependencies
          key: soldeer-${{ hashFiles('contracts/lagoon-v0/soldeer.lock', 'contracts/lagoon-v0/foundry.toml') }}
          restore-keys: |
            soldeer-
      - name: Lagoon dependency issue smoke test
        run: |
          export PATH="$HOME/.cargo/bin:$PATH"
          (cd contracts/lagoon-v0 && forge soldeer install)
          ls -lha contracts/lagoon-v0/dependencies/
          ls -lha contracts/lagoon-v0/dependencies/@openzeppelin-contracts-upgradeable-5.0.0/
      - name: Verify Web3.py version
        run: |
          poetry env info
          poetry run python --version
          poetry run python -c "import web3; print(f'Web3.py version: {web3.__version__}')"
      - name: Run integration tests
        run: |
          # Exclude unit-manifest files and GMX (GMX has its own workflow).
          # Build --ignore flags from unit-manifest directories only (not individual files —
          # pytest --ignore must be a path prefix or directory).
          # Individual file exclusion is via --deselect.
          DESELECT_FLAGS=$(awk '{print "--deselect=" $0}' tests/unit-manifest.txt | tr '\n' ' ')
          poetry run pytest tests/ \
            --ignore=tests/gmx \
            $DESELECT_FLAGS \
            --timeout-method=thread --tb=native -n auto -v -s --capture=no
        env:
          BNB_CHAIN_JSON_RPC: ${{ secrets.BNB_CHAIN_JSON_RPC }}
          JSON_RPC_POLYGON_ARCHIVE: ${{ secrets.JSON_RPC_POLYGON_ARCHIVE }}
          JSON_RPC_POLYGON: ${{ secrets.JSON_RPC_POLYGON }}
          JSON_RPC_ETHEREUM: ${{ secrets.JSON_RPC_ETHEREUM }}
          JSON_RPC_BASE: ${{ secrets.JSON_RPC_BASE }}
          JSON_RPC_BINANCE: ${{ secrets.JSON_RPC_BINANCE }}
          JSON_RPC_ARBITRUM: ${{ secrets.JSON_RPC_ARBITRUM }}
          JSON_RPC_PLASMA: ${{ secrets.JSON_RPC_PLASMA }}
          JSON_RPC_HYPERLIQUID: ${{ secrets.JSON_RPC_HYPERLIQUID }}
          ETHEREUM_JSON_RPC: ${{ secrets.JSON_RPC_ETHEREUM }}
          GOOGLE_CLOUD_PROJECT: ${{ secrets.GOOGLE_CLOUD_PROJECT }}
          GOOGLE_CLOUD_REGION: ${{ secrets.GOOGLE_CLOUD_REGION }}
          KEY_RING: ${{ secrets.KEY_RING }}
          KEY_NAME: ${{ secrets.KEY_NAME }}
          GCP_ADC_CREDENTIALS_STRING: ${{ secrets.GCP_ADC_CREDENTIALS_STRING }}
          TOKEN_RISK_API_KEY: ${{ secrets.TOKEN_RISK_API_KEY }}
          ARBITRUM_SEPOLIA_RPC_URL: ${{ secrets.ARBITRUM_SEPOLIA_RPC_URL }}
          ARBITRUM_GMX_TEST_SEPOLIA_PRIVATE_KEY: ${{ secrets.ARBITRUM_GMX_TEST_SEPOLIA_PRIVATE_KEY }}
          HYPERSYNC_API_KEY: ${{ secrets.HYPERSYNC_API_KEY }}
```

- [ ] **Step 2: Validate YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml'))" && echo "YAML OK"
```

- [ ] **Step 3: Ask user to approve diff and commit**

```bash
git diff .github/workflows/test.yml
```

Do NOT commit. Show diff, wait for explicit user approval.

---

### Chunk 2 measurement gate (after Step 2 PR merges)

Wait 5 non-draft merged PRs. Classify each as unit-only (only `tests/unit-manifest.txt` paths changed) or integration-touching. Confirm unit-only PRs hit ~20-40 billable-min target.

---

## Chunk 3: Step 3 — per-subsystem integration split

Files touched per subsystem:
- `git mv tests/<sub>/ tests/integration/<sub>/`
- Create: `.github/workflows/test-integration-<sub>.yml`
- Modify: `.github/workflows/test.yml` (add `--ignore=tests/integration/<sub>` to integration job)

Subsystem migration order: lagoon → hyperliquid → erc_4626 → aave_v3 → enzyme → guard → batch(cctp+gains+grvt+usdc+derive+hibachi+hypersync+ipor+lifi+lighter+morpho+one_delta+orderly+provider+rpc+safe+safe-integration+token_analysis+uniswap_v2+uniswap_v3+vault+velora+velvet+event_reader+feed+research) → flat-file cleanup.

The template below is repeated for each subsystem. Only the `<SUBSYSTEM>` placeholder changes.

---

### Task 9 (template — repeat per subsystem): Migrate `<SUBSYSTEM>`

Replace `<SUBSYSTEM>` with the subsystem name (e.g. `lagoon`).
Replace `<ETH_DEFI_PATH>` with the corresponding source path (e.g. `eth_defi/lagoon`).

**Files:**
- `git mv tests/<SUBSYSTEM>/ tests/integration/<SUBSYSTEM>/`
- Create: `.github/workflows/test-integration-<SUBSYSTEM>.yml`
- Modify: `.github/workflows/test.yml`

- [ ] **Step 1: Create `tests/integration/` if not already present**

```bash
mkdir -p tests/integration
```

- [ ] **Step 2: Move the subsystem test folder**

```bash
git mv tests/<SUBSYSTEM>/ tests/integration/<SUBSYSTEM>/
```

- [ ] **Step 3: Check for broken imports in conftest**

```bash
grep -r "from tests\." tests/integration/<SUBSYSTEM>/ || echo "No broken imports"
grep -r "import tests\." tests/integration/<SUBSYSTEM>/ || echo "No broken imports"
```

If any matches: update the import paths. Most conftest files use `eth_defi.testing.*` — those are fine.

- [ ] **Step 4: Check transitive source deps**

```bash
grep -rhE "^from eth_defi\.|^import eth_defi\." tests/integration/<SUBSYSTEM>/ \
  | grep -oE "eth_defi\.[a-z_0-9]+" | sort -u
```

Add any non-`<SUBSYSTEM>` modules found to the `paths:` filter in the workflow (Step 6) so the subsystem's CI triggers when those modules change.

- [ ] **Step 5: Run subsystem tests locally to confirm no path breakage**

```bash
source .local-test.env && export JSON_RPC_ARBITRUM=$ARBITRUM_CHAIN_JSON_RPC && \
  poetry run pytest tests/integration/<SUBSYSTEM>/ -x -q --timeout=60
```

Expected: same pass/fail result as before the move (ignore pre-existing flaky failures).

- [ ] **Step 6: Create `.github/workflows/test-integration-<SUBSYSTEM>.yml`**

```yaml
name: Integration — <SUBSYSTEM>

on:
  push:
    branches: [master]
    paths:
      - 'eth_defi/<ETH_DEFI_PATH>/**'
      - 'tests/integration/<SUBSYSTEM>/**'
      - '.github/workflows/test-integration-<SUBSYSTEM>.yml'
      - 'pyproject.toml'
      - 'poetry.lock'
  pull_request:
    branches: [master]
    types: [opened, synchronize, reopened, ready_for_review]
    paths:
      - 'eth_defi/<ETH_DEFI_PATH>/**'
      - 'tests/integration/<SUBSYSTEM>/**'
      - '.github/workflows/test-integration-<SUBSYSTEM>.yml'
      - 'pyproject.toml'
      - 'poetry.lock'

jobs:
  test:
    if: github.event.pull_request.draft == false || github.event_name == 'push'
    runs-on:
      group: Beefy runners
    concurrency:
      group: ${{ github.workflow }}-${{ github.ref }}
      cancel-in-progress: true
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.14"]
    name: <SUBSYSTEM> — Python ${{ matrix.python-version }}
    steps:
      - uses: actions/checkout@v3
        with:
          submodules: true
      - name: Setup Node.js
        uses: actions/setup-node@v3
        with:
          node-version: 18
          cache: "npm"
          cache-dependency-path: contracts/aave-v3-deploy/package-lock.json
      - name: Install pnpm
        uses: pnpm/action-setup@v4
        with:
          version: 10.21.0
      - name: Export PNPM location
        run: |
          PNPM_HOME="/home/runner/.local/share/pnpm"
          echo $PNPM_HOME >> $GITHUB_PATH
      - name: Install poetry (using matrix Python)
        run: pipx install "poetry>=2.3" --python "${{ env.PYTHON_PATH }}"
      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: "poetry"
          cache-dependency-path: |
            **/pyproject.toml
      - name: Install dependencies
        run: |
          poetry install
          poetry install -E docs -E data -E test -E hypersync -E ccxt -E duckdb -E posts
      - name: Install Ganache
        run: yarn global add ganache
      - name: Cache Foundry
        uses: actions/cache@v4
        with:
          path: ~/.foundry
          key: foundry-v1.2.3-${{ runner.os }}
      - name: Install Foundry
        uses: foundry-rs/foundry-toolchain@v1
        with:
          version: "v1.2.3"
      - name: Setup Aave v3 for tests
        run: |
          poetry run install-aave-for-testing
      - name: Build needed contracts
        run: |
          pnpm --version
      - name: Cache Lagoon soldeer deps
        uses: actions/cache@v4
        with:
          path: contracts/lagoon-v0/dependencies
          key: soldeer-${{ hashFiles('contracts/lagoon-v0/soldeer.lock', 'contracts/lagoon-v0/foundry.toml') }}
          restore-keys: |
            soldeer-
      - name: Lagoon dependency issue smoke test
        run: |
          export PATH="$HOME/.cargo/bin:$PATH"
          (cd contracts/lagoon-v0 && forge soldeer install)
      - name: Run <SUBSYSTEM> integration tests
        run: |
          poetry run pytest tests/integration/<SUBSYSTEM>/ \
            --timeout-method=thread --tb=native -n auto -v -s --capture=no
        env:
          BNB_CHAIN_JSON_RPC: ${{ secrets.BNB_CHAIN_JSON_RPC }}
          JSON_RPC_POLYGON_ARCHIVE: ${{ secrets.JSON_RPC_POLYGON_ARCHIVE }}
          JSON_RPC_POLYGON: ${{ secrets.JSON_RPC_POLYGON }}
          JSON_RPC_ETHEREUM: ${{ secrets.JSON_RPC_ETHEREUM }}
          JSON_RPC_BASE: ${{ secrets.JSON_RPC_BASE }}
          JSON_RPC_BINANCE: ${{ secrets.JSON_RPC_BINANCE }}
          JSON_RPC_ARBITRUM: ${{ secrets.JSON_RPC_ARBITRUM }}
          JSON_RPC_PLASMA: ${{ secrets.JSON_RPC_PLASMA }}
          JSON_RPC_HYPERLIQUID: ${{ secrets.JSON_RPC_HYPERLIQUID }}
          ETHEREUM_JSON_RPC: ${{ secrets.JSON_RPC_ETHEREUM }}
          GOOGLE_CLOUD_PROJECT: ${{ secrets.GOOGLE_CLOUD_PROJECT }}
          GOOGLE_CLOUD_REGION: ${{ secrets.GOOGLE_CLOUD_REGION }}
          KEY_RING: ${{ secrets.KEY_RING }}
          KEY_NAME: ${{ secrets.KEY_NAME }}
          GCP_ADC_CREDENTIALS_STRING: ${{ secrets.GCP_ADC_CREDENTIALS_STRING }}
          TOKEN_RISK_API_KEY: ${{ secrets.TOKEN_RISK_API_KEY }}
          ARBITRUM_SEPOLIA_RPC_URL: ${{ secrets.ARBITRUM_SEPOLIA_RPC_URL }}
          ARBITRUM_GMX_TEST_SEPOLIA_PRIVATE_KEY: ${{ secrets.ARBITRUM_GMX_TEST_SEPOLIA_PRIVATE_KEY }}
          HYPERSYNC_API_KEY: ${{ secrets.HYPERSYNC_API_KEY }}
```

- [ ] **Step 7: Add `--ignore=tests/integration/<SUBSYSTEM>` to the catch-all integration job in `test.yml`**

In `.github/workflows/test.yml`, in the `test-integration` job's `Run integration tests` step, add the new ignore flag. After all subsystems are migrated, the `pytest tests/` command in `test-integration` becomes very short or empty — that is fine; an empty test run exits 0.

- [ ] **Step 8: Validate YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/test-integration-<SUBSYSTEM>.yml'))" && echo "OK"
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml'))" && echo "OK"
```

- [ ] **Step 9: Ask user to approve and commit**

Do NOT commit. Show diff, wait for explicit user approval per global rule.

---

### Task 10: Final cleanup PR (Step 3.8)

After all subsystems migrated:

- [ ] **Step 1: Classify flat `tests/*.py` files**

```bash
for f in tests/test_*.py; do
  if grep -qE "mainnet_fork|web3_fork|anvil|JSON_RPC_|HYPERSYNC_API_KEY|GCP_ADC_CREDENTIALS" "$f" 2>/dev/null; then
    echo "INTEGRATION: $f"
  else
    echo "UNIT: $f"
  fi
done
```

- [ ] **Step 2: Move unit flat files to `tests/unit/`**

```bash
mkdir -p tests/unit
# For each file classified UNIT above:
git mv tests/test_<name>.py tests/unit/test_<name>.py
```

- [ ] **Step 3: Move integration flat files to best-fit subsystem**

Based on filename / imports. E.g. `test_reorganisation_monitor_polygon.py` → `tests/integration/provider/`.

- [ ] **Step 4: Update `tests/unit-manifest.txt`**

Regenerate:
```bash
bash scripts/ci/list-unit-tests.sh > tests/unit-manifest.txt
```

- [ ] **Step 5: Remove catch-all integration job from `test.yml` if no untiered tests remain**

If `tests/` root contains only `unit/` and `integration/` subdirs (no loose `test_*.py` files), the `test-integration` catch-all job in `test.yml` can be removed. `test.yml` becomes the unit-tier-only workflow.

- [ ] **Step 6: Validate all workflows and ask user to approve commit**

```bash
for f in .github/workflows/*.yml; do
  python3 -c "import yaml; yaml.safe_load(open('$f'))" && echo "OK: $f" || echo "FAIL: $f"
done
```

---

## Subsystem path-filter reference

Use this table when filling `<ETH_DEFI_PATH>` and `paths:` in each subsystem workflow. Add transitive deps discovered in Task 9 Step 4.

| Subsystem folder | Primary source path | Notes |
|---|---|---|
| lagoon | eth_defi/lagoon | Also depends on eth_defi/safe, eth_defi/erc_4626 |
| hyperliquid | eth_defi/hyperliquid | Live API tests — mark expected-flaky if needed |
| erc_4626 | eth_defi/erc_4626 | Largest; check transitive deps carefully |
| aave_v3 | eth_defi/aave_v3 | |
| enzyme | eth_defi/enzyme | |
| guard | eth_defi/guard | Also eth_defi/safe_integration |
| cctp | eth_defi/cctp | |
| gains | eth_defi/gains | |
| grvt | eth_defi/grvt | |
| usdc | eth_defi/usdc | |
| derive | eth_defi/derive | |
| hibachi | eth_defi/hibachi | |
| hypersync | eth_defi/hypersync | |
| ipor | eth_defi/ipor | |
| lifi | eth_defi/lifi | |
| lighter | eth_defi/lighter | |
| morpho | eth_defi/morpho | |
| one_delta | eth_defi/one_delta | |
| orderly | eth_defi/orderly | |
| provider | eth_defi/provider | |
| rpc | eth_defi/rpc | |
| safe | eth_defi/safe | |
| safe-integration | eth_defi/safe_integration | |
| token_analysis | eth_defi/token_analysis | |
| uniswap_v2 | eth_defi/uniswap_v2 | |
| uniswap_v3 | eth_defi/uniswap_v3 | |
| vault | eth_defi/vault | |
| velora | eth_defi/velora | |
| velvet | eth_defi/velvet | |
| event_reader | eth_defi/event_reader | |
| feed | eth_defi/feed | |
| research | eth_defi/research | |
